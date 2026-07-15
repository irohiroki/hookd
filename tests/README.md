# Tests

Two levels of tests are provided.

## Unit tests

`test_cron.py` verifies the cron parser, env key sanitization, and user-switching
helpers (`_owner_env`, `_make_preexec`) in `hookd.py`.
No running server or EC2 access is required. Run from the repository root:

```bash
python3 tests/test_cron.py
```

Expected output:

```
OK    '* * * * *'                2026-07-15 09:00  expected=True got=True
...
All tests passed.
```

## Integration tests

`test_integration.py` verifies the full dispatch path against a running hookd
instance. It registers test fixtures via `hookctl`, sends an HTTP request to
the server, and checks the response.

### Prerequisites

- hookd is running on `127.0.0.1:9000`
- `hookctl` is installed at `/usr/local/bin/hookctl`
- Test scripts are deployed and executable on the server:

  ```bash
  cp -r tests/scripts /home/rocky/hookd/tests/
  find /home/rocky/hookd/tests/scripts -name '*.sh' -exec chmod +x {} +
  ```

### Running

Webhook test only (completes in a few seconds):

```bash
python3 tests/test_integration.py --skip-schedule
```

Full test including the schedule test (waits up to 90 seconds for the next
minute boundary):

```bash
python3 tests/test_integration.py
```

Expected output:

```
--- webhook test ---
      hookctl: registered 1 route(s) for rocky — hookd will reload within 2 seconds
OK    POST /rocky/test-hookd → 200 exit=0
--- user switch test ---
      hookctl: registered 1 route(s) for rocky — hookd will reload within 2 seconds
OK    script ran with USER=rocky HOME=/home/rocky uid=1000 gid=1000
--- schedule test ---
      hookctl: registered 1 schedule(s) for rocky — hookd will reload within 2 seconds
      waiting up to 90s for schedule "rocky/test-hookd-schedule" to fire...
OK    schedule "rocky/test-hookd-schedule" fired and exited 0

All integration tests passed.
```

The user switch test (`test_user_switch`) registers `fixtures/owner-check.yml`,
triggers the route, and reads `tests/owner-check.txt` written by the script
to confirm that `USER`, `HOME`, and `uid` match the registering OS user.

## Fixtures

`fixtures/route.yml`, `fixtures/schedule.yml`, and `fixtures/owner-check.yml`
are the YAML files passed to `hookctl` during integration tests.
They reference scripts under `scripts/`.
