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

from config import load_all_routes, load_all_schedules, load_config
from cron import cron_matches
from handler import HookHandler
from runner import build_schedule_env, run_script_sync
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
