#!/usr/bin/env python3
"""
Webhook receiver — receives HTTP POST requests and dispatches shell scripts
based on path and payload conditions defined in config.yml.
"""

import argparse
import glob
import hashlib
import hmac
import json
import logging
import os
import pwd
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler

import yaml


def load_config(path):
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault('server', {})
    cfg['server'].setdefault('host', '0.0.0.0')
    cfg['server'].setdefault('port', 9000)
    cfg.setdefault('log', {})
    cfg['log'].setdefault('file', '/home/rocky/webhook/webhook.log')
    cfg['log'].setdefault('level', 'INFO')
    cfg['log'].setdefault('max_bytes', 10 * 1024 * 1024)
    cfg['log'].setdefault('backup_count', 5)
    cfg.setdefault('routes_dir', '/home/rocky/webhook/routes.d')
    cfg.setdefault('pidfile', '/home/rocky/webhook/webhook.pid')
    cfg.setdefault('routes', [])
    for route in cfg['routes']:
        _apply_route_defaults(route)
    return cfg


def _apply_route_defaults(route):
    route.setdefault('async', False)
    route.setdefault('timeout', 30)
    route.setdefault('env', {})
    route.setdefault('match', {})


def load_all_routes(base_cfg_path, routes_dir, logger=None):
    """Return merged routes from config.yml and all routes.d/*.yml files.

    config.yml routes take priority. Among routes.d files, alphabetical order
    is used and duplicate paths are skipped with a warning.
    """
    with open(base_cfg_path) as f:
        base = yaml.safe_load(f)

    seen_paths = set()
    routes = []

    for route in base.get('routes', []):
        _apply_route_defaults(route)
        seen_paths.add(route['path'])
        routes.append(route)

    if os.path.isdir(routes_dir):
        for filepath in sorted(glob.glob(os.path.join(routes_dir, '*.yml'))):
            try:
                with open(filepath) as f:
                    user_cfg = yaml.safe_load(f)
                if not isinstance(user_cfg, dict) or 'routes' not in user_cfg:
                    continue
                for route in user_cfg['routes']:
                    _apply_route_defaults(route)
                    if route['path'] in seen_paths:
                        if logger:
                            logger.warning('duplicate path %s in %s — skipped',
                                           route['path'], filepath)
                        continue
                    seen_paths.add(route['path'])
                    routes.append(route)
            except Exception as e:
                if logger:
                    logger.error('failed to load %s: %s', filepath, e)

    return routes


def setup_logging(log_cfg):
    level = getattr(logging, log_cfg['level'].upper(), logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    logger = logging.getLogger('webhook')
    logger.setLevel(level)

    handler = RotatingFileHandler(
        log_cfg['file'],
        maxBytes=log_cfg['max_bytes'],
        backupCount=log_cfg['backup_count'],
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def verify_signature(secret, body_bytes, signature_header):
    """Return True if HMAC-SHA256 of body matches the X-Hub-Signature-256 header."""
    if not signature_header or not signature_header.startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(
        secret.encode('utf-8'), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def find_route(routes, path, payload):
    """Return the first route whose path matches and all match conditions are satisfied."""
    for route in routes:
        if route['path'] != path:
            continue
        if all(payload.get(k) == v for k, v in route.get('match', {}).items()):
            return route
    return None


def build_env(route, payload, body_bytes, path):
    env = os.environ.copy()
    for key, val in payload.items():
        env_key = 'WEBHOOK_PAYLOAD_' + key.upper().replace('-', '_')
        env[env_key] = val if isinstance(val, str) else json.dumps(val)
    env['WEBHOOK_BODY'] = body_bytes.decode('utf-8', errors='replace')
    env['WEBHOOK_PATH'] = path
    env['WEBHOOK_METHOD'] = 'POST'
    for k, v in route.get('env', {}).items():
        env[k] = str(v)
    return env


def run_script_sync(script, env, timeout, logger):
    try:
        result = subprocess.run(
            [script], env=env, capture_output=True, text=True, timeout=timeout
        )
        logger.info('script=%s exit=%d', script, result.returncode)
        if result.stdout:
            logger.debug('stdout: %s', result.stdout.strip())
        if result.stderr:
            logger.debug('stderr: %s', result.stderr.strip())
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        logger.warning('script=%s timed out after %ds', script, timeout)
        return -1, 'timeout'
    except Exception as e:
        logger.error('script=%s error: %s', script, e)
        return -2, str(e)


def run_script_async(script, env, logger):
    try:
        proc = subprocess.Popen(
            [script], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        logger.info('script=%s launched async pid=%d', script, proc.pid)
    except Exception as e:
        logger.error('script=%s async launch error: %s', script, e)


class WebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        logger = self.server.logger
        path = self.path.split('?')[0]

        content_length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(content_length) if content_length else b''

        logger.info('POST %s from %s', path, self.client_address[0])

        try:
            payload = json.loads(body_bytes) if body_bytes else {}
            if not isinstance(payload, dict):
                payload = {}
        except json.JSONDecodeError:
            self._send_json(400, {'error': 'invalid JSON body'})
            return

        with self.server.routes_lock:
            route = find_route(self.server.routes, path, payload)

        if route is None:
            logger.warning('no route matched: %s', path)
            self._send_json(404, {'error': 'no matching route'})
            return

        secret = route.get('secret')
        if secret:
            sig = self.headers.get('X-Hub-Signature-256', '')
            if not verify_signature(secret, body_bytes, sig):
                logger.warning('HMAC verification failed: %s', path)
                self._send_json(401, {'error': 'invalid signature'})
                return

        env = build_env(route, payload, body_bytes, path)
        script = route['script']

        if route.get('async'):
            run_script_async(script, env, logger)
            self._send_json(202, {'status': 'accepted'})
        else:
            rc, output = run_script_sync(script, env, route.get('timeout', 30), logger)
            if rc == 0:
                self._send_json(200, {'status': 'ok', 'exit_code': rc})
            else:
                self._send_json(500, {'status': 'error', 'exit_code': rc, 'output': output})


def main():
    parser = argparse.ArgumentParser(description='Webhook receiver')
    parser.add_argument('--config', default='/home/rocky/webhook/config.yml')
    args = parser.parse_args()

    cfg = load_config(args.config)
    logger = setup_logging(cfg['log'])
    logger.info('Starting webhook server')

    routes_dir = cfg['routes_dir']
    pidfile = cfg['pidfile']

    with open(pidfile, 'w') as f:
        f.write(str(os.getpid()))

    host = cfg['server']['host']
    port = cfg['server']['port']

    routes_lock = threading.RLock()
    initial_routes = load_all_routes(args.config, routes_dir, logger)

    server = ThreadingHTTPServer((host, port), WebhookHandler)
    server.logger = logger
    server.routes_lock = routes_lock
    server.routes = initial_routes

    def _reload_routes():
        new_routes = load_all_routes(args.config, routes_dir, logger)
        with routes_lock:
            server.routes = new_routes
        logger.info('routes reloaded: %d route(s)', len(new_routes))

    def _on_sighup(signum, frame):
        logger.info('SIGHUP received, reloading routes')
        _reload_routes()

    signal.signal(signal.SIGHUP, _on_sighup)

    reload_flag = os.path.join(routes_dir, '.reload')

    def _watch_reload():
        while True:
            time.sleep(2)
            if os.path.exists(reload_flag):
                try:
                    os.remove(reload_flag)
                except OSError:
                    pass
                logger.info('.reload flag detected, reloading routes')
                _reload_routes()

    watcher = threading.Thread(target=_watch_reload, daemon=True)
    watcher.start()

    def _shutdown(signum, frame):
        logger.info('SIGTERM received, shutting down')
        threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGTERM, _shutdown)

    logger.info('Listening on %s:%d with %d route(s)', host, port, len(initial_routes))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info('KeyboardInterrupt, shutting down')
    finally:
        server.server_close()
        try:
            os.remove(pidfile)
        except OSError:
            pass
        logger.info('Server stopped')


if __name__ == '__main__':
    main()
