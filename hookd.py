#!/usr/bin/env python3
"""
hookd — webhook and schedule dispatcher daemon.

Receives HTTP POST requests and runs registered shell scripts based on
path and JSON payload conditions. Also runs scripts on cron schedules.
Configuration is loaded from config.yml and per-user files in routes.d/.
"""

import argparse
import glob
import hashlib
import hmac
import json
import logging
import os
import pwd
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler

import yaml

MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB
_NON_POSIX_RE = re.compile(r'[^A-Z0-9_]')


# ── Config loading ────────────────────────────────────────────────────────────

def load_config(path):
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault('server', {})
    cfg['server'].setdefault('host', '0.0.0.0')
    cfg['server'].setdefault('port', 9000)
    cfg.setdefault('log', {})
    cfg['log'].setdefault('file', '/home/rocky/hookd/hookd.log')
    cfg['log'].setdefault('level', 'INFO')
    cfg['log'].setdefault('max_bytes', 10 * 1024 * 1024)
    cfg['log'].setdefault('backup_count', 5)
    cfg.setdefault('routes_dir', '/home/rocky/hookd/routes.d')
    cfg.setdefault('pidfile', '/home/rocky/hookd/hookd.pid')
    cfg.setdefault('routes', [])
    cfg.setdefault('schedules', [])
    for route in cfg['routes']:
        _apply_route_defaults(route)
    for sched in cfg['schedules']:
        _apply_schedule_defaults(sched)
    return cfg


def _apply_route_defaults(route):
    route.setdefault('async', False)
    route.setdefault('timeout', 30)
    route.setdefault('env', {})
    route.setdefault('match', {})


def _apply_schedule_defaults(sched):
    sched.setdefault('timeout', 30)
    sched.setdefault('env', {})


# ── User switching ────────────────────────────────────────────────────────────

def _drop_to_user(username):
    """Called in the child process (after fork, before exec) to switch UID/GID.

    Requires CAP_SETUID and CAP_SETGID on the parent process
    (set via AmbientCapabilities in hookd.service).
    """
    pw = pwd.getpwnam(username)
    os.initgroups(username, pw.pw_gid)
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)


def _make_preexec(owner):
    """Return a preexec_fn closure for the given owner, or None for admin routes."""
    if owner is None:
        return None
    return lambda: _drop_to_user(owner)


def _owner_env(owner):
    """Return {USER, HOME} overrides for owner, or empty dict for admin routes."""
    if owner is None:
        return {}
    try:
        pw = pwd.getpwnam(owner)
        return {'USER': owner, 'HOME': pw.pw_dir}
    except KeyError:
        return {}


# ── Cron parser ───────────────────────────────────────────────────────────────

def parse_cron_field(field, lo, hi):
    """Expand one cron field to a frozenset of matching integers.

    Supports: * 5 1-5 */15 0-30/5 1,3,5
    """
    result = set()
    for part in field.split(','):
        if '/' in part:
            range_part, step = part.split('/', 1)
            step = int(step)
            start, end = (lo, hi) if range_part == '*' else map(int, range_part.split('-'))
            result.update(range(start, end + 1, step))
        elif part == '*':
            result.update(range(lo, hi + 1))
        elif '-' in part:
            start, end = map(int, part.split('-'))
            result.update(range(start, end + 1))
        else:
            result.add(int(part))
    return frozenset(result)


def parse_cron(expr):
    """Parse a 5-field cron expression into a tuple of five frozensets.

    Fields: minute hour day-of-month month day-of-week
    Day-of-week: 0 and 7 both mean Sunday.
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f'cron expression must have 5 fields, got {len(fields)}: {expr!r}')
    minute, hour, dom, month, dow = fields
    return (
        parse_cron_field(minute, 0, 59),
        parse_cron_field(hour,   0, 23),
        parse_cron_field(dom,    1, 31),
        parse_cron_field(month,  1, 12),
        parse_cron_field(dow,    0,  7),
    )


def cron_matches(parsed, dt):
    """Return True if datetime dt matches the parsed cron tuple."""
    minute_set, hour_set, dom_set, month_set, dow_set = parsed
    # Python weekday: Mon=0..Sun=6 → cron: Sun=0, Mon=1..Sat=6
    cron_dow = (dt.weekday() + 1) % 7
    effective_dow = set(dow_set)
    if 7 in effective_dow:   # 7 is an alias for Sunday (0)
        effective_dow.add(0)
    return (
        dt.minute in minute_set
        and dt.hour in hour_set
        and dt.day in dom_set
        and dt.month in month_set
        and cron_dow in effective_dow
    )


# ── Route / schedule loaders ──────────────────────────────────────────────────

def load_all_routes(base_cfg_path, routes_dir, logger=None):
    """Return merged routes from config.yml and all routes.d/*.yml files.

    Admin routes (config.yml) are served as-is.
    User routes (routes.d/<username>.yml) are prefixed with /<username>.
    Duplicate paths: first match wins (config.yml, then alphabetical by file).
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
            username = os.path.basename(filepath)[:-4]
            try:
                with open(filepath) as f:
                    user_cfg = yaml.safe_load(f)
                if not isinstance(user_cfg, dict):
                    continue
                for route in user_cfg.get('routes', []):
                    route = dict(route)
                    _apply_route_defaults(route)
                    namespaced = f'/{username}{route["path"]}'
                    if namespaced in seen_paths:
                        if logger:
                            logger.warning('duplicate path %s in %s — skipped',
                                           namespaced, filepath)
                        continue
                    seen_paths.add(namespaced)
                    route['path'] = namespaced
                    route['_owner'] = username
                    routes.append(route)
            except Exception as e:
                if logger:
                    logger.error('failed to load routes from %s: %s', filepath, e)

    return routes


def load_all_schedules(base_cfg_path, routes_dir, logger=None):
    """Return merged schedules from config.yml and all routes.d/*.yml files.

    Admin schedules (config.yml) are used as-is.
    User schedules (routes.d/<username>.yml) are prefixed with <username>/.
    Duplicate names: first match wins.
    """
    with open(base_cfg_path) as f:
        base = yaml.safe_load(f)

    seen_names = set()
    schedules = []

    for sched in base.get('schedules', []):
        sched = dict(sched)
        _apply_schedule_defaults(sched)
        try:
            sched['_parsed_cron'] = parse_cron(sched['cron'])
        except (ValueError, KeyError) as e:
            if logger:
                logger.error('invalid cron in admin schedule %r: %s', sched.get('name'), e)
            continue
        seen_names.add(sched['name'])
        schedules.append(sched)

    if os.path.isdir(routes_dir):
        for filepath in sorted(glob.glob(os.path.join(routes_dir, '*.yml'))):
            username = os.path.basename(filepath)[:-4]
            try:
                with open(filepath) as f:
                    user_cfg = yaml.safe_load(f)
                if not isinstance(user_cfg, dict):
                    continue
                for sched in user_cfg.get('schedules', []):
                    sched = dict(sched)
                    _apply_schedule_defaults(sched)
                    namespaced = f'{username}/{sched["name"]}'
                    if namespaced in seen_names:
                        if logger:
                            logger.warning('duplicate schedule %s in %s — skipped',
                                           namespaced, filepath)
                        continue
                    try:
                        sched['_parsed_cron'] = parse_cron(sched['cron'])
                    except (ValueError, KeyError) as e:
                        if logger:
                            logger.error('invalid cron in %s schedule %r: %s',
                                         filepath, sched.get('name'), e)
                        continue
                    seen_names.add(namespaced)
                    sched['name'] = namespaced
                    sched['_owner'] = username
                    schedules.append(sched)
            except Exception as e:
                if logger:
                    logger.error('failed to load schedules from %s: %s', filepath, e)

    return schedules


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(log_cfg):
    level = getattr(logging, log_cfg['level'].upper(), logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    logger = logging.getLogger('hookd')
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


# ── Verification / routing ────────────────────────────────────────────────────

def verify_signature(secret, body_bytes, signature_header):
    """Return True if HMAC-SHA256 of body matches the X-Hub-Signature-256 header."""
    if not signature_header or not signature_header.startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(
        secret.encode('utf-8'), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def find_route(routes, path, payload):
    """Return the first route whose path and all match conditions are satisfied."""
    for route in routes:
        if route['path'] != path:
            continue
        if all(payload.get(k) == v for k, v in route.get('match', {}).items()):
            return route
    return None


# ── Environment builders ──────────────────────────────────────────────────────

def build_webhook_env(route, payload, body_bytes, path):
    env = os.environ.copy()
    env.update(_owner_env(route.get('_owner')))
    for key, val in payload.items():
        env_key = 'WEBHOOK_PAYLOAD_' + _NON_POSIX_RE.sub('_', key.upper())
        env[env_key] = val if isinstance(val, str) else json.dumps(val)
    env['WEBHOOK_BODY'] = body_bytes.decode('utf-8', errors='replace')
    env['WEBHOOK_PATH'] = path
    env['WEBHOOK_METHOD'] = 'POST'
    for k, v in route.get('env', {}).items():
        env[k] = str(v)
    return env


def build_schedule_env(sched, triggered_at):
    env = os.environ.copy()
    env.update(_owner_env(sched.get('_owner')))
    env['SCHEDULE_NAME'] = sched['name']
    env['SCHEDULE_CRON'] = sched['cron']
    env['SCHEDULE_TRIGGERED_AT'] = triggered_at.isoformat()
    for k, v in sched.get('env', {}).items():
        env[k] = str(v)
    return env


# ── Script execution ──────────────────────────────────────────────────────────

def run_script_sync(script, env, timeout, logger, preexec_fn=None):
    try:
        result = subprocess.run(
            [script], env=env, capture_output=True, text=True,
            timeout=timeout, preexec_fn=preexec_fn,
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


def run_script_async(script, env, logger, preexec_fn=None):
    try:
        proc = subprocess.Popen(
            [script], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, preexec_fn=preexec_fn,
        )
        logger.info('script=%s launched async pid=%d', script, proc.pid)
    except Exception as e:
        logger.error('script=%s async launch error: %s', script, e)


# ── HTTP handler ──────────────────────────────────────────────────────────────

class HookHandler(BaseHTTPRequestHandler):

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

        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            self._send_json(400, {'error': 'invalid Content-Length'})
            return
        if not (0 <= content_length <= MAX_BODY_BYTES):
            self._send_json(413, {'error': 'payload too large'})
            return
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
                logger.warning('signature verification failed: %s', path)
                self._send_json(401, {'error': 'invalid signature'})
                return

        owner = route.get('_owner')
        env = build_webhook_env(route, payload, body_bytes, path)
        preexec_fn = _make_preexec(owner)
        script = route['script']

        if route.get('async'):
            run_script_async(script, env, logger, preexec_fn=preexec_fn)
            self._send_json(202, {'status': 'accepted'})
        else:
            rc, output = run_script_sync(
                script, env, route.get('timeout', 30), logger, preexec_fn=preexec_fn,
            )
            if rc == 0:
                self._send_json(200, {'status': 'ok', 'exit_code': rc})
            else:
                self._send_json(500, {'status': 'error', 'exit_code': rc, 'output': output})


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='hookd — webhook and schedule dispatcher')
    parser.add_argument('--config', default='/home/rocky/hookd/config.yml')
    args = parser.parse_args()

    cfg = load_config(args.config)
    logger = setup_logging(cfg['log'])
    logger.info('Starting hookd')

    routes_dir = cfg['routes_dir']
    pidfile = cfg['pidfile']

    with open(pidfile, 'w') as f:
        f.write(str(os.getpid()))

    host = cfg['server']['host']
    port = cfg['server']['port']

    routes_lock = threading.RLock()
    schedules_lock = threading.RLock()

    initial_routes = load_all_routes(args.config, routes_dir, logger)
    initial_schedules = load_all_schedules(args.config, routes_dir, logger)

    server = ThreadingHTTPServer((host, port), HookHandler)
    server.logger = logger
    server.routes_lock = routes_lock
    server.schedules_lock = schedules_lock
    server.routes = initial_routes
    server.schedules = initial_schedules

    def _reload():
        new_routes = load_all_routes(args.config, routes_dir, logger)
        new_schedules = load_all_schedules(args.config, routes_dir, logger)
        with routes_lock:
            server.routes = new_routes
        with schedules_lock:
            server.schedules = new_schedules
        logger.info('reloaded: %d route(s), %d schedule(s)',
                    len(new_routes), len(new_schedules))

    def _on_sighup(signum, frame):
        logger.info('SIGHUP received, reloading')
        _reload()

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
                logger.info('.reload flag detected, reloading')
                _reload()

    def _schedule_runner():
        while True:
            now = datetime.now()
            sleep_secs = 60 - now.second - now.microsecond / 1_000_000
            time.sleep(sleep_secs)
            triggered_at = datetime.now().replace(second=0, microsecond=0)
            with schedules_lock:
                current_schedules = list(server.schedules)
            for sched in current_schedules:
                if cron_matches(sched['_parsed_cron'], triggered_at):
                    logger.info('schedule name=%s triggered', sched['name'])
                    env = build_schedule_env(sched, triggered_at)
                    preexec_fn = _make_preexec(sched.get('_owner'))
                    run_script_sync(
                        sched['script'], env, sched.get('timeout', 30), logger,
                        preexec_fn=preexec_fn,
                    )

    for func, name in [(_watch_reload, 'watcher'), (_schedule_runner, 'scheduler')]:
        threading.Thread(target=func, daemon=True, name=name).start()

    def _shutdown(signum, frame):
        logger.info('SIGTERM received, shutting down')
        threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGTERM, _shutdown)

    logger.info('Listening on %s:%d — %d route(s), %d schedule(s)',
                host, port, len(initial_routes), len(initial_schedules))
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
        logger.info('hookd stopped')


if __name__ == '__main__':
    main()
