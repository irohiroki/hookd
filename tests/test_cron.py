"""
Unit tests for hookd.py (cron parser, env key sanitization, user switching,
env groups, env builder precedence). No running server is required.

Usage:
    python3 tests/test_cron.py
"""

import os
import pwd
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import load_all_routes, load_all_schedules
from cron import cron_matches, parse_cron
from runner import _NON_POSIX_RE, build_schedule_env, build_webhook_env
from user import _make_preexec, _owner_env

_CURRENT_USER = pwd.getpwuid(os.getuid())

CASES = [
    # (cron_expr, datetime_str, expected_match)
    ('* * * * *',     '2026-07-15 09:00', True),
    ('0 9 * * 1',     '2026-07-13 09:00', True),   # Monday
    ('0 9 * * 1',     '2026-07-14 09:00', False),  # Tuesday
    ('*/15 * * * *',  '2026-07-15 09:00', True),
    ('*/15 * * * *',  '2026-07-15 09:07', False),
    ('0 3 * * 0',     '2026-07-12 03:00', True),   # Sunday dow=0
    ('0 3 * * 7',     '2026-07-12 03:00', True),   # Sunday dow=7
    ('1,2,3 * * * *', '2026-07-15 09:02', True),
    ('1,2,3 * * * *', '2026-07-15 09:04', False),
    ('0 0 1 1 *',     '2026-01-01 00:00', True),
    ('0 0 1 1 *',     '2026-07-15 00:00', False),
    ('0-30/5 * * * *','2026-07-15 09:10', True),
    ('0-30/5 * * * *','2026-07-15 09:31', False),
]


ENV_KEY_CASES = [
    # (raw_json_key, expected_env_suffix)
    ('foo',       'FOO'),
    ('foo-bar',   'FOO_BAR'),
    ('foo.bar',   'FOO_BAR'),
    ('foo=bar',   'FOO_BAR'),
    ('foo bar',   'FOO_BAR'),
    ('foo\x00',   'FOO_'),
    ('',          ''),
]


GROUPS_CONFIG = """\
env_groups:
  bedrock:
    HTTPS_PROXY: "http://127.0.0.1:8888"
    no_proxy: "example.com,.example.com"
    RETRIES: 3
  extra:
    EXTRA_KEY: extra
routes:
  - path: /with-group
    script: /bin/true
    env_group: bedrock
  - path: /with-list
    script: /bin/true
    env_group: [bedrock, extra]
  - path: /unknown-group
    script: /bin/true
    env_group: nope
  - path: /plain
    script: /bin/true
schedules:
  - name: sched-group
    cron: '* * * * *'
    script: /bin/true
    env_group: bedrock
"""

GROUPS_USER_CONFIG = """\
routes:
  - path: /user-route
    script: /bin/true
    env_group: bedrock
"""


def run():
    failures = []

    print('--- cron parser ---')
    for expr, dt_str, expected in CASES:
        parsed = parse_cron(expr)
        dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M')
        result = cron_matches(parsed, dt)
        status = 'OK  ' if result == expected else 'FAIL'
        if result != expected:
            failures.append(f'cron {expr!r} @ {dt_str}')
        print(f'{status}  {expr!r:25s}  {dt_str}  expected={expected} got={result}')

    print()
    print('--- env key sanitization ---')
    for raw, expected_suffix in ENV_KEY_CASES:
        result_suffix = _NON_POSIX_RE.sub('_', raw.upper())
        expected_key = 'WEBHOOK_PAYLOAD_' + expected_suffix
        result_key = 'WEBHOOK_PAYLOAD_' + result_suffix
        status = 'OK  ' if result_key == expected_key else 'FAIL'
        if result_key != expected_key:
            failures.append(f'env key {raw!r}')
        print(f'{status}  {raw!r:20s}  →  {result_key}')

    print()
    print('--- user switching (unit) ---')

    # _owner_env: None → empty dict
    result = _owner_env(None)
    ok = result == {}
    status = 'OK  ' if ok else 'FAIL'
    if not ok:
        failures.append('_owner_env(None)')
    print(f'{status}  _owner_env(None) → {result!r}')

    # _owner_env: current OS user → correct USER and HOME
    username = _CURRENT_USER.pw_name
    result = _owner_env(username)
    ok = result.get('USER') == username and result.get('HOME') == _CURRENT_USER.pw_dir
    status = 'OK  ' if ok else 'FAIL'
    if not ok:
        failures.append(f'_owner_env({username!r})')
    print(f'{status}  _owner_env({username!r}) → {result!r}')

    # _owner_env: nonexistent user → empty dict
    result = _owner_env('__hookd_no_such_user__')
    ok = result == {}
    status = 'OK  ' if ok else 'FAIL'
    if not ok:
        failures.append('_owner_env(nonexistent)')
    print(f'{status}  _owner_env("__hookd_no_such_user__") → {result!r}')

    # _make_preexec: None → None
    result = _make_preexec(None)
    ok = result is None
    status = 'OK  ' if ok else 'FAIL'
    if not ok:
        failures.append('_make_preexec(None)')
    print(f'{status}  _make_preexec(None) is None → {ok}')

    # _make_preexec: valid user → callable
    result = _make_preexec(username)
    ok = callable(result)
    status = 'OK  ' if ok else 'FAIL'
    if not ok:
        failures.append(f'_make_preexec({username!r})')
    print(f'{status}  _make_preexec({username!r}) is callable → {ok}')

    def check(ok, label):
        status = 'OK  ' if ok else 'FAIL'
        if not ok:
            failures.append(label)
        print(f'{status}  {label}')

    print()
    print('--- env groups (load) ---')
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg_path = os.path.join(tmpdir, 'config.yml')
        routes_dir = os.path.join(tmpdir, 'routes.d')
        os.mkdir(routes_dir)
        with open(cfg_path, 'w') as f:
            f.write(GROUPS_CONFIG)
        with open(os.path.join(routes_dir, 'alice.yml'), 'w') as f:
            f.write(GROUPS_USER_CONFIG)

        routes = {r['path']: r for r in load_all_routes(cfg_path, routes_dir)}
        schedules = {s['name']: s for s in load_all_schedules(cfg_path, routes_dir)}

        bedrock_env = {'HTTPS_PROXY': 'http://127.0.0.1:8888',
                       'no_proxy': 'example.com,.example.com',
                       'RETRIES': '3'}
        check(routes['/with-group'].get('_group_env') == bedrock_env,
              'env_group resolves; values str()-normalized; lowercase key kept')
        check(routes['/with-list'].get('_group_env') == {**bedrock_env, 'EXTRA_KEY': 'extra'},
              'env_group accepts a list of names, merged in order')
        check('_group_env' not in routes['/unknown-group'],
              'unknown group name skipped, route still loads')
        check('_group_env' not in routes['/plain'],
              'route without env_group is untouched')
        check(routes['/alice/user-route'].get('_group_env') == bedrock_env,
              'user routes (routes.d) can reference admin env groups')
        check(schedules['sched-group'].get('_group_env') == bedrock_env,
              'schedules resolve env_group too')

    print()
    print('--- env builders (precedence) ---')
    group_env = {'HTTPS_PROXY': 'from-group', 'no_proxy': 'from-group',
                 'WEBHOOK_PATH': 'spoof', 'HOOKD_TEST_OS': 'from-group',
                 'USER': 'spoof', 'HOME': '/tmp/spoof'}

    os.environ['HOOKD_TEST_OS'] = 'from-os'
    try:
        route = {'path': '/x', 'script': '/bin/true', '_owner': username,
                 'env': {'HTTPS_PROXY': 'from-route'}, '_group_env': group_env}
        env = build_webhook_env(route, {}, b'', '/real')
    finally:
        del os.environ['HOOKD_TEST_OS']

    check(env['HOOKD_TEST_OS'] == 'from-group', 'group env overrides os.environ')
    check(env['no_proxy'] == 'from-group', 'lowercase group key passes through unchanged')
    check(env['HTTPS_PROXY'] == 'from-route', 'route env overrides group env')
    check(env['WEBHOOK_PATH'] == '/real', 'built-in WEBHOOK_* overrides group env')
    check(env['USER'] == username and env['HOME'] == _CURRENT_USER.pw_dir,
          'owner USER/HOME override group env')

    plain_route = {'path': '/x', 'script': '/bin/true', 'env': {}}
    env = build_webhook_env(plain_route, {}, b'', '/real')
    check(env['WEBHOOK_PATH'] == '/real', 'route without _group_env still works')

    sched = {'name': 't', 'cron': '* * * * *', 'script': '/bin/true',
             'env': {'HTTPS_PROXY': 'from-sched'},
             '_group_env': {'HTTPS_PROXY': 'from-group', 'no_proxy': 'np',
                            'SCHEDULE_NAME': 'spoof'}}
    env = build_schedule_env(sched, datetime(2026, 8, 13, 9, 0))
    check(env['HTTPS_PROXY'] == 'from-sched', 'schedule env overrides group env')
    check(env['no_proxy'] == 'np', 'schedule group env injected')
    check(env['SCHEDULE_NAME'] == 't', 'built-in SCHEDULE_* overrides group env')

    print()
    if failures:
        print(f'{len(failures)} test(s) FAILED: {failures}')
        sys.exit(1)
    print('All tests passed.')


if __name__ == '__main__':
    run()
