"""
Unit tests for hookd.py (cron parser and env key sanitization).
No running server is required.

Usage:
    python3 tests/test_cron.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hookd import _NON_POSIX_RE, cron_matches, parse_cron

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
    if failures:
        print(f'{len(failures)} test(s) FAILED: {failures}')
        sys.exit(1)
    print('All tests passed.')


if __name__ == '__main__':
    run()
