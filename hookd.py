#!/usr/bin/env python3
"""
hookd — webhook and schedule dispatcher daemon.

Entry point. Wires together config, handler, and background threads.
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer
from logging.handlers import RotatingFileHandler

from config import load_all_daemons, load_all_routes, load_all_schedules, load_config
from cron import cron_matches
from handler import HookHandler
from runner import build_daemon_env, build_schedule_env, run_script_sync
from user import _make_preexec


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
    daemons_lock = threading.RLock()

    initial_routes = load_all_routes(args.config, routes_dir, logger)
    initial_schedules = load_all_schedules(args.config, routes_dir, logger)
    initial_daemons = load_all_daemons(args.config, routes_dir, logger)

    server = ThreadingHTTPServer((host, port), HookHandler)
    server.logger = logger
    server.routes_lock = routes_lock
    server.schedules_lock = schedules_lock
    server.daemons_lock = daemons_lock
    server.routes = initial_routes
    server.schedules = initial_schedules
    server.daemons = initial_daemons

    def _reload():
        try:
            new_routes = load_all_routes(args.config, routes_dir, logger)
            new_schedules = load_all_schedules(args.config, routes_dir, logger)
            new_daemons = load_all_daemons(args.config, routes_dir, logger)
        except Exception as e:
            logger.error('reload failed, keeping previous config: %s', e)
            return
        with routes_lock:
            server.routes = new_routes
        with schedules_lock:
            server.schedules = new_schedules
        with daemons_lock:
            server.daemons = new_daemons
        logger.info('reloaded: %d route(s), %d schedule(s), %d daemon(s)',
                    len(new_routes), len(new_schedules), len(new_daemons))

    def _on_sighup(signum, frame):
        logger.info('SIGHUP received, reloading')
        _reload()

    signal.signal(signal.SIGHUP, _on_sighup)

    reload_flag = os.path.join(routes_dir, '.reload')

    def _watch_reload():
        last_mtime = 0
        while True:
            time.sleep(2)
            try:
                mtime = os.path.getmtime(reload_flag)
            except OSError:
                mtime = 0
            if mtime > last_mtime:
                last_mtime = mtime
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

    def _daemon_manager():
        next_check = {}

        def _run_script(script, owner, env):
            run_script_sync(script, env, timeout=30, logger=logger,
                            preexec_fn=_make_preexec(owner))

        def _is_healthy(daemon, env):
            hc = daemon.get('health_check')
            if not hc:
                return False
            rc, _ = run_script_sync(hc, env, timeout=10, logger=logger,
                                    preexec_fn=_make_preexec(daemon.get('_owner')))
            return rc == 0

        def _check_and_start(daemon):
            env = build_daemon_env(daemon)
            if not _is_healthy(daemon, env):
                logger.info('daemon name=%s starting', daemon['name'])
                _run_script(daemon['script'], daemon.get('_owner'), env)

        with daemons_lock:
            current = list(server.daemons)
        now = time.monotonic()
        for d in current:
            _check_and_start(d)
            next_check[d['name']] = now + d['health_interval']

        while True:
            with daemons_lock:
                current = list(server.daemons)
            now = time.monotonic()
            current_names = {d['name'] for d in current}

            for d in current:
                if d['name'] not in next_check:
                    _check_and_start(d)
                    next_check[d['name']] = now + d['health_interval']

            for name in list(next_check):
                if name not in current_names:
                    del next_check[name]

            for d in current:
                if next_check.get(d['name'], 0) <= now:
                    env = build_daemon_env(d)
                    if not _is_healthy(d, env):
                        logger.info('daemon name=%s unhealthy, restarting', d['name'])
                        _run_script(d['script'], d.get('_owner'), env)
                    next_check[d['name']] = now + d['health_interval']

            sleep_secs = max(1, min(next_check.values()) - time.monotonic()) if next_check else 60
            time.sleep(sleep_secs)

    for func, name in [(_watch_reload, 'watcher'), (_schedule_runner, 'scheduler'),
                        (_daemon_manager, 'daemon-manager')]:
        threading.Thread(target=func, daemon=True, name=name).start()

    def _shutdown(signum, frame):
        logger.info('SIGTERM received, shutting down')
        threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGTERM, _shutdown)

    logger.info('Listening on %s:%d — %d route(s), %d schedule(s), %d daemon(s)',
                host, port, len(initial_routes), len(initial_schedules), len(initial_daemons))
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
