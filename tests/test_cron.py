"""
Unit tests for the cron parser in hookd.py.
No running server is required.

Usage:
    python3 tests/test_cron.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hookd import cron_matches, parse_cron

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


def run():
    failures = []
    for expr, dt_str, expected in CASES:
        parsed = parse_cron(expr)
        dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M')
        result = cron_matches(parsed, dt)
        status = 'OK  ' if result == expected else 'FAIL'
        if result != expected:
            failures.append((expr, dt_str, expected, result))
        print(f'{status}  {expr!r:25s}  {dt_str}  expected={expected} got={result}')

    print()
    if failures:
        print(f'{len(failures)} test(s) FAILED.')
        sys.exit(1)
    print('All tests passed.')


if __name__ == '__main__':
    run()
