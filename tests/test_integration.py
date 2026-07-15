"""
Integration tests for hookd's webhook and schedule dispatch.

Prerequisites:
- hookd is running on 127.0.0.1:9000
- hookctl is installed at /usr/local/bin/hookctl
- Run from the repository root or the bone/hookd directory

Usage:
    python3 tests/test_integration.py
    python3 tests/test_integration.py --skip-schedule
"""

import argparse
import json
import os
import pwd
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures')
SCRIPTS = os.path.join(HERE, 'scripts')
BASE_URL = 'http://127.0.0.1:9000'
USERNAME = pwd.getpwuid(os.getuid()).pw_name


def fail(msg):
    print(f'FAIL  {msg}', file=sys.stderr)
    sys.exit(1)


def post(path, payload):
    req = urllib.request.Request(
        f'{BASE_URL}{path}',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    try:
        r = urllib.request.urlopen(req, timeout=15)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def hookctl(fixture_name):
    path = os.path.join(FIXTURES, fixture_name)
    result = subprocess.run(['hookctl', path], capture_output=True, text=True)
    if result.returncode != 0:
        fail(f'hookctl {fixture_name} failed: {result.stderr.strip()}')
    print(f'      hookctl: {result.stdout.strip()}')


def test_webhook():
    print('--- webhook test ---')
    hookctl('route.yml')
    time.sleep(2)

    route_path = f'/{USERNAME}/test-hookd'
    status, body = post(route_path, {'action': 'ping'})
    if status != 200:
        fail(f'POST {route_path} returned {status}: {body}')
    if body.get('exit_code') != 0:
        fail(f'script exited non-zero: {body}')
    print(f'OK    POST {route_path} → {status} exit={body["exit_code"]}')


def test_schedule(timeout=90):
    print('--- schedule test ---')
    hookctl('schedule.yml')

    import subprocess as sp
    deadline = time.time() + timeout
    schedule_name = f'{USERNAME}/test-hookd-schedule'
    print(f'      waiting up to {timeout}s for schedule "{schedule_name}" to fire...')

    while time.time() < deadline:
        time.sleep(5)
        result = sp.run(
            ['sudo', 'journalctl', '-u', 'hookd', '--since', '2 minutes ago',
             '--no-pager', '-q'],
            capture_output=True, text=True,
        )
        if schedule_name in result.stdout and 'exit=0' in result.stdout:
            print(f'OK    schedule "{schedule_name}" fired and exited 0')
            return

    fail(f'schedule "{schedule_name}" did not fire within {timeout}s')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-schedule', action='store_true',
                        help='Skip the schedule test (avoids waiting up to 90s)')
    args = parser.parse_args()

    test_webhook()
    if not args.skip_schedule:
        test_schedule()
    print('\nAll integration tests passed.')


if __name__ == '__main__':
    main()
