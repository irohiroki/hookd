"""Environment builders and script execution."""

import json
import os
import re
import subprocess

from user import _owner_env

MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB
_NON_POSIX_RE = re.compile(r'[^A-Z0-9_]')


def build_webhook_env(route, payload, body_bytes, path):
    env = os.environ.copy()
    env.update(_owner_env(route.get('_owner')))
    for key, val in payload.items():
        env_key = 'WEBHOOK_PAYLOAD_' + _NON_POSIX_RE.sub('_', key.upper())
        env[env_key] = val if isinstance(val, str) else json.dumps(val)
    env['WEBHOOK_BODY'] = body_bytes.decode('utf-8', errors='replace')
    env['WEBHOOK_PATH'] = path
    env['WEBHOOK_METHOD'] = 'POST'
    for k, v in route.get('env', {}).items():
        env[k] = str(v)
    return env


def build_schedule_env(sched, triggered_at):
    env = os.environ.copy()
    env.update(_owner_env(sched.get('_owner')))
    env['SCHEDULE_NAME'] = sched['name']
    env['SCHEDULE_CRON'] = sched['cron']
    env['SCHEDULE_TRIGGERED_AT'] = triggered_at.isoformat()
    for k, v in sched.get('env', {}).items():
        env[k] = str(v)
    return env


def run_script_sync(script, env, timeout, logger, preexec_fn=None):
    try:
        result = subprocess.run(
            [script], env=env, capture_output=True, text=True,
            timeout=timeout, preexec_fn=preexec_fn,
        )
        logger.info('script=%s exit=%d', script, result.returncode)
        if result.stdout:
            logger.debug('stdout: %s', result.stdout.strip())
        if result.stderr:
            logger.debug('stderr: %s', result.stderr.strip())
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        logger.warning('script=%s timed out after %ds', script, timeout)
        return -1, 'timeout'
    except Exception as e:
        logger.error('script=%s error: %s', script, e)
        return -2, str(e)


def run_script_async(script, env, logger, preexec_fn=None):
    try:
        proc = subprocess.Popen(
            [script], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, preexec_fn=preexec_fn,
        )
        logger.info('script=%s launched async pid=%d', script, proc.pid)
    except Exception as e:
        logger.error('script=%s async launch error: %s', script, e)
