# hookd

A webhook receiver and cron schedule dispatcher that runs shell scripts based
on HTTP request paths, JSON payload conditions, and time-based schedules —
inspired by GitHub Actions workflow triggers.

Two trigger types are supported:

- **Webhook**: a `POST` request to a registered path dispatches a script,
  optionally filtered by payload conditions and verified by HMAC-SHA256 signature.
- **Schedule**: a cron expression triggers a script at the configured time,
  analogous to GitHub Actions `on: schedule`.

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

## Registering routes and schedules

Each user registers their own configuration with `hookctl`:

```bash
hookctl my-config.yml
# registered 1 route(s) and 1 schedule(s) for alice — hookd will reload within 2 seconds
```

Routes and schedules from `my-config.yml` are automatically namespaced under the
current Unix username. A user named `alice` with `path: /deploy/app` will have
that route served at `/alice/deploy/app`.

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

routes: []

schedules: []
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
```

Forbidden keys in user files: `server`, `log`.

### Route fields

| Field | Required | Description |
|---|---|---|
| `path` | yes | Request path; served as `/<username><path>` |
| `script` | yes | Absolute path to the script to execute |
| `secret` | no | Shared passphrase for HMAC-SHA256 signature verification |
| `async` | no | `true` to fire and forget; default `false` |
| `timeout` | no | Execution timeout in seconds; default `30` |
| `match` | no | Key-value conditions on the JSON body |
| `env` | no | Extra environment variables passed to the script |

### Schedule fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Unique identifier within the user's config |
| `cron` | yes | 5-field cron expression (minute hour dom month dow) |
| `script` | yes | Absolute path to the script to execute |
| `timeout` | no | Execution timeout in seconds; default `30` |
| `env` | no | Extra environment variables passed to the script |

**Cron syntax** supports: `*`, numbers, ranges (`1-5`), steps (`*/15`, `0-30/5`),
and comma-separated lists (`1,3,5`). Day-of-week: `0` and `7` both mean Sunday.

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

## Testing a route

From within the server host (no firewall rule needed):

```python
import urllib.request, json

req = urllib.request.Request(
    "http://127.0.0.1:9000/alice/deploy/my-app",
    data=json.dumps({"ref": "refs/heads/main"}).encode(),
    headers={"Content-Type": "application/json"},
)
r = urllib.request.urlopen(req)
print(r.status, r.read().decode())
```

Logs are written to the file configured under `log.file` and also to the
systemd journal when running as a service:

```bash
sudo journalctl -u hookd -f
```
