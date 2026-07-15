"""
Unit tests for hookd.py (cron parser, env key sanitization, user switching).
No running server is required.

Usage:
    python3 tests/test_cron.py
"""

import os
import pwd
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cron import cron_matches, parse_cron
from runner import _NON_POSIX_RE
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

    print()
    if failures:
        print(f'{len(failures)} test(s) FAILED: {failures}')
        sys.exit(1)
    print('All tests passed.')


if __name__ == '__main__':
    run()
