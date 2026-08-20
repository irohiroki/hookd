"""Configuration loading and route/schedule loaders."""

import glob
import os

import yaml

from cron import parse_cron

SYSTEM_ROUTES_DIR_FILE = '/etc/hookd/routes_dir'
FALLBACK_ROUTES_DIR = '/var/lib/hookd/routes.d'


def default_routes_dir():
    """Return the per-user config directory recorded by install.sh.

    hookd and hookctl must agree on this path without depending on the caller's
    environment, so the admin's choice is written once to a root-owned,
    world-readable file and read from there by both.
    """
    try:
        with open(SYSTEM_ROUTES_DIR_FILE) as f:
            path = f.read().strip()
    except OSError:
        path = ''
    return path or FALLBACK_ROUTES_DIR


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
    cfg.setdefault('routes_dir', default_routes_dir())
    cfg.setdefault('pidfile', '/home/rocky/hookd/hookd.pid')
    cfg.setdefault('routes', [])
    cfg.setdefault('schedules', [])
    cfg.setdefault('daemons', [])
    for route in cfg['routes']:
        _apply_route_defaults(route)
    for sched in cfg['schedules']:
        _apply_schedule_defaults(sched)
    for daemon in cfg['daemons']:
        _apply_daemon_defaults(daemon)
    return cfg


def _apply_route_defaults(route):
    route.setdefault('async', False)
    route.setdefault('timeout', 30)
    route.setdefault('env', {})
    route.setdefault('match', {})


def _load_env_groups(base, logger=None):
    """Return normalized env groups from a parsed config: {name: {key: str(value)}}.

    Keys are kept verbatim (no case folding — lowercase keys like no_proxy are
    significant); keys containing '=' or NUL are skipped as they cannot be
    passed through exec.
    """
    raw = base.get('env_groups') or {}
    if not isinstance(raw, dict):
        if logger:
            logger.error('config "env_groups" must be a mapping — ignored')
        return {}
    groups = {}
    for name, mapping in raw.items():
        if not isinstance(mapping, dict):
            if logger:
                logger.error('env group %r must be a mapping — skipped', name)
            continue
        env = {}
        for k, v in mapping.items():
            k, v = str(k), str(v)
            if '=' in k or '\x00' in k or '\x00' in v:
                if logger:
                    logger.warning('env group %r: invalid key %r — skipped', name, k)
                continue
            env[k] = v
        groups[name] = env
    return groups


def _resolve_group_env(item, env_groups, logger=None, context=''):
    """Merge the env groups referenced by item['env_group'] (a name or list of names)."""
    names = item.get('env_group')
    if not names:
        return {}
    if isinstance(names, str):
        names = [names]
    env = {}
    for name in names:
        if name not in env_groups:
            if logger:
                logger.error('unknown env group %r in %s — skipped', name, context)
            continue
        env.update(env_groups[name])
    return env


def _apply_schedule_defaults(sched):
    sched.setdefault('timeout', 30)
    sched.setdefault('env', {})


def _apply_daemon_defaults(daemon):
    daemon.setdefault('health_interval', 60)
    daemon.setdefault('health_check', None)
    daemon.setdefault('env', {})


def load_all_routes(base_cfg_path, routes_dir, logger=None):
    """Return merged routes from config.yml and all routes.d/*.yml files.

    Admin routes (config.yml) are served as-is.
    User routes (routes.d/<username>.yml) are prefixed with /<username>.
    Duplicate paths: first match wins (config.yml, then alphabetical by file).
    Routes referencing env groups via 'env_group' get the merged mapping
    attached as '_group_env'.
    """
    with open(base_cfg_path) as f:
        base = yaml.safe_load(f)

    env_groups = _load_env_groups(base, logger)
    seen_paths = set()
    routes = []

    for route in base.get('routes', []):
        _apply_route_defaults(route)
        group_env = _resolve_group_env(route, env_groups, logger,
                                       f'route {route.get("path")!r}')
        if group_env:
            route['_group_env'] = group_env
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
                    group_env = _resolve_group_env(route, env_groups, logger,
                                                   f'route {route.get("path")!r} in {filepath}')
                    if group_env:
                        route['_group_env'] = group_env
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


def load_all_daemons(base_cfg_path, routes_dir, logger=None):
    """Return merged daemons from config.yml and all routes.d/*.yml files.

    Admin daemons (config.yml) are used as-is.
    User daemons (routes.d/<username>.yml) are prefixed with <username>/.
    Duplicate names: first match wins.
    Daemons referencing env groups via 'env_group' get the merged mapping
    attached as '_group_env'.
    """
    with open(base_cfg_path) as f:
        base = yaml.safe_load(f)

    env_groups = _load_env_groups(base, logger)
    seen_names = set()
    daemons = []

    for daemon in base.get('daemons', []):
        daemon = dict(daemon)
        _apply_daemon_defaults(daemon)
        group_env = _resolve_group_env(daemon, env_groups, logger,
                                       f'daemon {daemon.get("name")!r}')
        if group_env:
            daemon['_group_env'] = group_env
        seen_names.add(daemon['name'])
        daemons.append(daemon)

    if os.path.isdir(routes_dir):
        for filepath in sorted(glob.glob(os.path.join(routes_dir, '*.yml'))):
            username = os.path.basename(filepath)[:-4]
            try:
                with open(filepath) as f:
                    user_cfg = yaml.safe_load(f)
                if not isinstance(user_cfg, dict):
                    continue
                for daemon in user_cfg.get('daemons', []):
                    daemon = dict(daemon)
                    _apply_daemon_defaults(daemon)
                    group_env = _resolve_group_env(daemon, env_groups, logger,
                                                   f'daemon {daemon.get("name")!r} in {filepath}')
                    if group_env:
                        daemon['_group_env'] = group_env
                    namespaced = f'{username}/{daemon["name"]}'
                    if namespaced in seen_names:
                        if logger:
                            logger.warning('duplicate daemon %s in %s — skipped',
                                           namespaced, filepath)
                        continue
                    seen_names.add(namespaced)
                    daemon['name'] = namespaced
                    daemon['_owner'] = username
                    daemons.append(daemon)
            except Exception as e:
                if logger:
                    logger.error('failed to load daemons from %s: %s', filepath, e)

    return daemons


def load_all_schedules(base_cfg_path, routes_dir, logger=None):
    """Return merged schedules from config.yml and all routes.d/*.yml files.

    Admin schedules (config.yml) are used as-is.
    User schedules (routes.d/<username>.yml) are prefixed with <username>/.
    Duplicate names: first match wins.
    Schedules referencing env groups via 'env_group' get the merged mapping
    attached as '_group_env'.
    """
    with open(base_cfg_path) as f:
        base = yaml.safe_load(f)

    env_groups = _load_env_groups(base, logger)
    seen_names = set()
    schedules = []

    for sched in base.get('schedules', []):
        sched = dict(sched)
        _apply_schedule_defaults(sched)
        group_env = _resolve_group_env(sched, env_groups, logger,
                                       f'schedule {sched.get("name")!r}')
        if group_env:
            sched['_group_env'] = group_env
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
                    group_env = _resolve_group_env(sched, env_groups, logger,
                                                   f'schedule {sched.get("name")!r} in {filepath}')
                    if group_env:
                        sched['_group_env'] = group_env
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
