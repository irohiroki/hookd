# webhook

An HTTP webhook receiver that dispatches shell scripts based on request path and
JSON payload conditions, inspired by GitHub Actions workflow triggers.

When a `POST` request arrives, the server looks for a matching route in
`config.yml`. If found, it verifies the optional HMAC-SHA256 signature, checks
any payload conditions, then executes the registered script with the payload
fields exposed as environment variables.

## Requirements

- Python 3.9 or later
- `python3-pyyaml` (installed by default on Rocky Linux 9)
- A writable directory for logs (default: the directory containing `webhook.py`)

No additional packages need to be installed via pip.

## Installation

```bash
mkdir -p /home/rocky/webhook /home/rocky/scripts
cp webhook.py config.yml /home/rocky/webhook/
```

To run the server in the foreground:

```bash
python3 /home/rocky/webhook/webhook.py --config /home/rocky/webhook/config.yml
```

To run it as a persistent systemd service, follow [DAEMON.md](DAEMON.md).

## Configuration

Edit `config.yml` to define the listening address and routes.

```yaml
server:
  host: "0.0.0.0"
  port: 9000

log:
  file: /home/rocky/webhook/webhook.log
  level: INFO       # DEBUG | INFO | WARNING | ERROR
  max_bytes: 10485760
  backup_count: 5

routes:
  - path: /deploy/my-app
    secret: ""                       # omit or leave empty to skip verification
    script: /home/rocky/scripts/deploy.sh
    async: true                      # true: respond immediately, run script in background
    timeout: 60                      # seconds; ignored when async is true
    match:                           # all conditions must match; omit to match any payload
      ref: "refs/heads/main"
    env:                             # extra environment variables forwarded to the script
      APP_ENV: production
```

A route matches a request when:
1. `path` equals the request path exactly.
2. Every key-value pair under `match` is present in the JSON body with the given value.

Routes are evaluated top to bottom; the first match wins.

### Route fields

| Field | Required | Description |
|---|---|---|
| `path` | yes | Request path to match (e.g. `/deploy/my-app`) |
| `script` | yes | Absolute path to the script to execute |
| `secret` | no | Shared passphrase for HMAC-SHA256 signature verification |
| `async` | no | `true` to fire and forget; default `false` |
| `timeout` | no | Execution timeout in seconds; default `30` |
| `match` | no | Key-value conditions on the JSON body |
| `env` | no | Extra environment variables passed to the script |

## Environment variables passed to scripts

For each top-level field in the JSON body, the server sets a corresponding
variable named `WEBHOOK_PAYLOAD_<KEY>` (uppercased). The following variables
are always set:

| Variable | Value |
|---|---|
| `WEBHOOK_PATH` | Request path |
| `WEBHOOK_METHOD` | `POST` |
| `WEBHOOK_BODY` | Raw request body as a string |
| `WEBHOOK_PAYLOAD_<KEY>` | Each top-level JSON field |

Example — a body of `{"ref": "refs/heads/main", "pusher": "alice"}` produces:

```
WEBHOOK_PATH=/deploy/my-app
WEBHOOK_METHOD=POST
WEBHOOK_BODY={"ref": "refs/heads/main", "pusher": "alice"}
WEBHOOK_PAYLOAD_REF=refs/heads/main
WEBHOOK_PAYLOAD_PUSHER=alice
```

## Signature verification

Set `secret` on a route to enable HMAC-SHA256 verification. The server checks
the `X-Hub-Signature-256` request header using the same algorithm as GitHub
webhooks. Requests without a valid signature receive `401 Unauthorized`.

The expected header format is:

```
X-Hub-Signature-256: sha256=<hex digest>
```

where the digest is computed as `HMAC-SHA256(secret, raw_body_bytes)`.

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
    "http://127.0.0.1:9000/deploy/my-app",
    data=json.dumps({"ref": "refs/heads/main"}).encode(),
    headers={"Content-Type": "application/json"},
)
r = urllib.request.urlopen(req)
print(r.status, r.read().decode())
```

Logs are written to the file configured under `log.file` and also to the
systemd journal when running as a service:

```bash
sudo journalctl -u webhook -f
```
