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
_PW = pwd.getpwuid(os.getuid())
USERNAME = _PW.pw_name
OWNER_CHECK_FILE = os.path.join(HERE, 'owner-check.txt')


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


def test_user_switch():
    print('--- user switch test ---')
    hookctl('owner-check.yml')
    time.sleep(2)

    if os.path.exists(OWNER_CHECK_FILE):
        os.remove(OWNER_CHECK_FILE)

    route_path = f'/{USERNAME}/owner-check'
    status, body = post(route_path, {})
    if status != 200:
        fail(f'POST {route_path} returned {status}: {body}')

    if not os.path.exists(OWNER_CHECK_FILE):
        fail(f'script did not write to {OWNER_CHECK_FILE}')

    content = open(OWNER_CHECK_FILE).read().strip()

    if f'USER={USERNAME}' not in content:
        fail(f'USER mismatch in script output: {content!r}')
    if f'HOME={_PW.pw_dir}' not in content:
        fail(f'HOME mismatch in script output: {content!r}')
    if f'uid={_PW.pw_uid}' not in content:
        fail(f'uid mismatch in script output: {content!r}')

    print(f'OK    script ran with {content}')


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
    test_user_switch()
    if not args.skip_schedule:
        test_schedule()
    print('\nAll integration tests passed.')


if __name__ == '__main__':
    main()
