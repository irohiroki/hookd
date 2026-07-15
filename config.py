"""Configuration loading and route/schedule loaders."""

import glob
import os

import yaml

from cron import parse_cron


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
