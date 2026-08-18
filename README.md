# hookd

A webhook receiver and cron schedule dispatcher that runs shell scripts based
on HTTP request paths, JSON payload conditions, and time-based schedules —
inspired by GitHub Actions workflow triggers.

Three trigger types are supported:

- **Webhook**: a `POST` request to a registered path dispatches a script,
  optionally filtered by payload conditions and verified by HMAC-SHA256 signature.
- **Schedule**: a cron expression triggers a script at the configured time,
  analogous to GitHub Actions `on: schedule`.
- **Daemon**: a long-running process registered by a start script; hookd checks
  health on a configurable interval and restarts it if the check fails.

## Requirements

- Python 3.9 or later
- `python3-pyyaml` (installed by default on Rocky Linux 9)
- A writable directory for logs (default: the directory containing `hookd.py`)

No additional packages need to be installed via pip.

## Installation

Run `install.sh` as root on the target host:

```bash
HOOKD_USER=<user> bash <(curl -fsSL https://raw.githubusercontent.com/irohiroki/hookd/main/install.sh)
```

Replace `<user>` with the Linux account hookd will run as. The script
downloads the Python modules, generates `config.yml`, installs `hookctl` and
the systemd service, and starts hookd. For environment variable options and
split admin / service user setups, see [DEPLOY.md](DEPLOY.md).

To start hookd in the foreground for local testing:

```bash
python3 hookd.py --config config.yml
```

## Registering routes, schedules, and daemons

Each user registers their own configuration with `hookctl`:

```bash
hookctl my-config.yml
# registered 1 route(s) and 1 schedule(s) and 1 daemon(s) for alice — hookd will reload within 2 seconds
```

Routes, schedules, and daemons from `my-config.yml` are automatically namespaced
under the current Unix username. A user named `alice` with `path: /deploy/app`
will have that route served at `/alice/deploy/app`.

## Configuration

Edit `config.yml` to define the listening address and admin-level routes and
schedules. Users register their own entries via `hookctl` (see above).

```yaml
server:
  host: "0.0.0.0"
  port: 9000

log:
  file: /home/<user>/hookd/hookd.log
  level: INFO       # DEBUG | INFO | WARNING | ERROR
  max_bytes: 10485760
  backup_count: 5

routes_dir: /var/lib/hookd/routes.d

env_groups: {}    # named env var sets jobs opt into with `env_group:` (admin-only)

routes: []

schedules: []

daemons: []
```

### User config file format

```yaml
routes:
  - path: /deploy/my-app        # served at /<username>/deploy/my-app
    script: /home/alice/deploy.sh
    async: true                  # default: false
    timeout: 60                  # seconds; default: 30; ignored when async: true
    match:                       # all conditions must match; omit to accept any payload
      ref: "refs/heads/main"
    env:
      APP_ENV: production

schedules:
  - name: weekly-report         # internal ID: <username>/weekly-report
    cron: '0 9 * * 1'           # Monday at 09:00 UTC
    script: /home/alice/weekly.sh
    timeout: 120                 # default: 30
    env:
      REPORT_TYPE: weekly

daemons:
  - name: my-agent              # internal ID: <username>/my-agent
    script: /home/alice/start-agent.sh    # runs to start the daemon, then exits
    health_check: /home/alice/check-agent.sh  # exit 0 = healthy; non-0 = restart
    health_interval: 60         # seconds between health checks; default: 60
    env:
      PORT: "8080"
```

The start script is executed when hookd starts and whenever the health check
fails. It should be idempotent if multiple rapid restarts are possible, but
because hookd waits for the health check before retrying, a plain "start if not
running" pattern is sufficient in most cases.

Forbidden keys in user files: `server`, `log`, `env_groups`. Routes, schedules,
and daemons may *reference* admin-defined env groups with `env_group: <name>`.

### Route fields

| Field | Required | Description |
|---|---|---|
| `path` | yes | Request path; served as `/<username><path>` |
| `script` | yes | Absolute path to the script to execute |
| `secret` | no | Shared passphrase for HMAC-SHA256 signature verification |
| `async` | no | `true` to fire and forget; default `false` |
| `timeout` | no | Execution timeout in seconds; default `30` |
| `match` | no | Key-value conditions on the JSON body |
| `env_group` | no | Admin-defined env group name (or list of names) to opt into |
| `env` | no | Extra environment variables passed to the script |

### Schedule fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Unique identifier within the user's config |
| `cron` | yes | 5-field cron expression (minute hour dom month dow) |
| `script` | yes | Absolute path to the script to execute |
| `timeout` | no | Execution timeout in seconds; default `30` |
| `env_group` | no | Admin-defined env group name (or list of names) to opt into |
| `env` | no | Extra environment variables passed to the script |

**Cron syntax** supports: `*`, numbers, ranges (`1-5`), steps (`*/15`, `0-30/5`),
and comma-separated lists (`1,3,5`). Day-of-week: `0` and `7` both mean Sunday.

### Daemon fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Unique identifier within the user's config |
| `script` | yes | Absolute path to the start script |
| `health_check` | yes | Absolute path to the health check script |
| `health_interval` | no | Seconds between health checks; default `60` |
| `env_group` | no | Admin-defined env group name (or list of names) to opt into |
| `env` | no | Extra environment variables passed to the start and health check scripts |

Both `script` and `health_check` receive the same environment (including `DAEMON_NAME`
and any `env`/`env_group` values). The health check runs with a fixed 10-second
timeout; the start script runs with a fixed 30-second timeout.

## Environment variables passed to scripts

### Webhook triggers

| Variable | Value |
|---|---|
| `WEBHOOK_PATH` | Request path |
| `WEBHOOK_METHOD` | `POST` |
| `WEBHOOK_BODY` | Raw request body as a string |
| `WEBHOOK_PAYLOAD_<KEY>` | Each top-level JSON field (uppercased) |

### Schedule triggers

| Variable | Value |
|---|---|
| `SCHEDULE_NAME` | Full schedule name, e.g. `alice/weekly-report` |
| `SCHEDULE_CRON` | The cron expression |
| `SCHEDULE_TRIGGERED_AT` | ISO 8601 timestamp of the trigger time |

### Daemon triggers

| Variable | Value |
|---|---|
| `DAEMON_NAME` | Full daemon name, e.g. `alice/my-agent` |

### Precedence

Scripts receive, from weakest to strongest: the daemon's own environment,
the env groups the job opted into via `env_group:`, the owner's `USER`/`HOME`,
the built-in `WEBHOOK_*`/`SCHEDULE_*`/`DAEMON_*` variables, and finally the
job's own `env:` entries.

## Signature verification

Set `secret` on a route to enable HMAC-SHA256 verification. hookd checks the
`X-Hub-Signature-256` request header using the same algorithm as GitHub webhooks.
Requests without a valid signature receive `401 Unauthorized`.

The expected header format is:

```
X-Hub-Signature-256: sha256=<hex digest>
```

## HTTP responses

| Status | Meaning |
|---|---|
| `200 OK` | Script ran and exited 0 |
| `202 Accepted` | Route matched; script launched in background (`async: true`) |
| `400 Bad Request` | Request body is not valid JSON |
| `401 Unauthorized` | Signature is missing or incorrect |
| `404 Not Found` | No route matched the request path and payload |
| `500 Internal Server Error` | Script exited with a non-zero status |

## Testing and debugging

### Sync routes

For routes with `async: false`, hookd captures stdout and stderr and returns
them in the response body. Test from within the server host:

```bash
curl -s -X POST http://127.0.0.1:9000/alice/deploy/my-app \
  -H 'Content-Type: application/json' \
  -d '{"ref":"refs/heads/main"}' | python3 -m json.tool
```

A successful response looks like:

```json
{"status": "ok", "exit_code": 0}
```

A failure response includes the captured output:

```json
{"status": "error", "exit_code": 1, "output": "..."}
```

### Async routes and schedules

There is no response body to inspect. Add a redirect at the top of your script
to capture all output to a file:

```bash
#!/bin/bash
exec >> ~/hookd/myscript.log 2>&1
set -x
```

### Testing a script without hookd

Simulate the environment variables hookd injects and run the script directly:

```bash
WEBHOOK_PATH=/alice/deploy/my-app WEBHOOK_METHOD=POST \
  WEBHOOK_BODY='{"ref":"refs/heads/main"}' \
  WEBHOOK_PAYLOAD_REF=refs/heads/main \
  bash ~/scripts/deploy.sh
```

For schedule scripts, use `SCHEDULE_NAME`, `SCHEDULE_CRON`, and
`SCHEDULE_TRIGGERED_AT` instead.

## Calling Claude from scripts

When `bedrock-proxy` is installed and the admin has defined the `bedrock`
env group (see DEPLOY.md), a route or schedule opts in with
`env_group: bedrock` and its script can call the `claude` binary directly
with no additional setup:

```bash
result=$(git -C ~/my-app log -20 --format='%s' | claude -p "Summarize these commit messages into release notes")
echo "$result"
```

hookd injects the necessary proxy environment variables into opted-in jobs
only — jobs without `env_group: bedrock` are untouched, so scripts that call
`claude` with a personal account need no group (and must not opt in).
`AWS_BEARER_TOKEN_BEDROCK` in the script environment is a dummy value;
the real token is held only by the `bedrock-proxy` daemon and is never
accessible to user scripts.

See [DEPLOY.md](DEPLOY.md) for the admin setup steps.
