# Deploying hookd on a New Host

This guide covers installation and day-to-day operations.

## Prerequisites

| Requirement | Check |
|---|---|
| Rocky Linux 9 / RHEL 9-compatible | `cat /etc/os-release` |
| Python 3.9+ with PyYAML | `python3 -c 'import yaml'` |
| pip3 | `pip3 --version` |
| git | `git --version` |
| systemd | `systemctl --version` |

## Install

Run `install.sh` as root. It downloads the Python modules, generates
`config.yml`, installs `hookctl` and the systemd service, and starts hookd.

```bash
HOOKD_USER=<user> bash <(curl -fsSL https://raw.githubusercontent.com/irohiroki/hookd/main/install.sh)
```

Replace `<user>` with the Linux account hookd will run as. The account must
already exist.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `HOOKD_USER` | — | Service user account (required when root) |
| `HOOKD_PORT` | `9000` | Listening port |
| `HOOKD_ROUTES_DIR` | `/var/lib/hookd/routes.d` | Per-user config directory; recorded in `/etc/hookd/routes_dir` at install time |
| `HOOKD_DIR` | `~/hookd` | Install directory |

These are read by `install.sh` only. Nothing reads the environment at runtime:
`hookd` takes its config path from `ExecStart` in the unit, and `hookctl` reads
the per-user config directory from `/etc/hookd/routes_dir` (falling back to
`/var/lib/hookd/routes.d` if that file is missing).

### Split roles

If the person with root access and the service user are different people, run
`install.sh` twice.

**Admin** (as root):

```bash
HOOKD_USER=<user> bash <(curl -fsSL https://raw.githubusercontent.com/irohiroki/hookd/main/install.sh)
```

**Service user** (without root, to install or update user files independently):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/irohiroki/hookd/main/install.sh)
```

When run without root, `install.sh` installs the Python modules and generates
`config.yml` (skipped if already present). It prints the admin command needed
to complete the system-level setup.

## Verification

```bash
curl http://localhost:9000/up
```

Expected response: `{"status": "ok"}`

## What install.sh does

When run as root with `HOOKD_USER` set:

1. Creates the service user's home directory if it does not exist
2. Creates `HOOKD_ROUTES_DIR` so any user can write their own config file
3. Runs `pip3 install git+https://github.com/irohiroki/hookd.git@$HOOKD_REF`,
   which installs `hookd` and `hookctl` to `/usr/local/bin/`
4. Writes `HOOKD_ROUTES_DIR` to `/etc/hookd/routes_dir` (mode 644) so `hookd` and
   `hookctl` resolve the same directory, and removes the obsolete
   `/etc/profile.d/hookd.sh`
5. Installs `hookd.service` to `/etc/systemd/system/` with paths and user name substituted
6. Creates `HOOKD_DIR` and generates `config.yml` there (skipped if already present)
7. Enables the service to persist across logins and starts it

When run without root, steps 1–5 and 7 are skipped — only `HOOKD_DIR` and
`config.yml` are created.

## Management

```bash
sudo systemctl stop hookd
sudo systemctl restart hookd
sudo systemctl disable hookd
sudo systemctl is-enabled hookd
```

## Updating the admin config

`routes`, `schedules`, `daemons` and `env_groups` in `config.yml` are re-read on reload:

```bash
sudo systemctl reload hookd     # or: kill -HUP $(cat /path/to/hookd.pid)
```

`server`, `log`, `routes_dir` and `pidfile` are read once at startup and need a
restart:

```bash
sudo systemctl restart hookd
```

User-registered routes and schedules reload automatically within 2 seconds of a
`hookctl` invocation — no restart needed.

## Deploying code updates

Use `deploy.sh` from the repository root to push the current working tree to
an existing installation:

```bash
./deploy.sh <hostname>
```

The script archives the current git HEAD with `git archive`, pipes it to the
remote host, installs via `sudo pip3 install`, and restarts hookd. The host must
be reachable via SSH without extra options, sudo must be available, and pip3 must
be installed.

Alternatively, on the remote host directly:

```bash
git clone https://github.com/irohiroki/hookd.git
sudo pip3 install hookd/
sudo systemctl restart hookd
```

Note: re-running `install.sh` as root regenerates
`/etc/systemd/system/hookd.service`, discarding any manual edits to the unit.

## Upgrading from a server-local clone

`upgrade.sh` updates an installation from a clone kept on the host itself — no
SSH round trip, and no path to choose. The repository path is fixed at
`/var/lib/hookd/repo`.

One-time setup, as root:

```bash
sudo install -d -o root -g root -m 755 /var/lib/hookd
sudo git clone -b main https://github.com/irohiroki/hookd.git /var/lib/hookd/repo
```

Every later update:

```bash
sudo /var/lib/hookd/repo/upgrade.sh
```

The script fetches the upstream of the currently checked-out branch, force-syncs
the worktree to it (`reset --hard` plus `clean -fd`), runs `pip3 install .`, and
restarts hookd. When the upstream tip is already checked out and the worktree is
clean it prints `Already up to date` and leaves the service alone.

To follow a different branch, check it out in the clone as root first:

```bash
sudo git -C /var/lib/hookd/repo checkout -B <branch> --track origin/<branch>
```

`pip3 install .` runs the project's build backend as root, so write access to the
clone is equivalent to root access. The script therefore refuses to run unless
`/var/lib/hookd/repo`, every ancestor directory, and every file in the tree are
owned by root and not writable by group or other — clone it as root with the
default `umask 022`, and keep it out of the service user's reach. hookd runs
user-supplied scripts as that user, so a clone it could write to would turn a
compromise of that account into root at the next upgrade. `/var/lib/hookd/routes.d`
being mode `1777` does not weaken this: `repo` is a sibling directory, and
`/var/lib/hookd` itself must stay `root:root` `755`.

Unlike `install.sh`, `upgrade.sh` never rewrites
`/etc/systemd/system/hookd.service` or `/etc/hookd/routes_dir`, so manual edits
to those survive an upgrade. Local edits and local commits inside the clone do
not — they are discarded on every run.

## Log rotation

hookd caps `hookd.log` at 10 MB and keeps 5 rotated copies via Python's
`RotatingFileHandler`. No additional `logrotate` configuration is needed.

systemd journal logs rotate automatically via `journald`.

## Troubleshooting

**Service fails to start**

```bash
sudo journalctl -u hookd -n 100 --no-pager
```

Common causes:

- `config.yml` syntax error:
  ```bash
  python3 -c "import yaml; yaml.safe_load(open('config.yml'))" && echo OK
  ```
- Port already in use:
  ```bash
  ss -tlnp | grep 9000
  ```

**Service enters a restart loop and stops**

systemd halts after `StartLimitBurst=5` failures in 60 seconds. Reset and restart:

```bash
sudo systemctl reset-failed hookd
sudo systemctl start hookd
```

**Script not executing**

Ensure the script exists and is executable:

```bash
ls -l /path/to/script.sh
chmod 755 /path/to/script.sh
```

## Bedrock credential proxy

`bedrock-proxy` is a companion daemon that holds `AWS_BEARER_TOKEN_BEDROCK`
and intercepts HTTPS traffic from the `claude` binary to the Bedrock endpoint
via a local HTTPS MITM proxy. User scripts call `claude -p prompt` as usual;
no code changes are required. The real token is never in user script environments.

### How it works

The proxy variables are defined once as a named env group in hookd's
`config.yml`, and each route or schedule that uses Bedrock opts in with
`env_group: bedrock`. Jobs that do not opt in get none of these variables —
scripts that call `claude` with a personal account keep working untouched.

| Variable | Value |
|---|---|
| `CLAUDE_CODE_USE_BEDROCK` | `1` (tells `claude` to use Bedrock) |
| `AWS_BEARER_TOKEN_BEDROCK` | `dummy` (any non-empty string) |
| `HTTPS_PROXY` | `http://127.0.0.1:8888` |
| `NODE_EXTRA_CA_CERTS` | `/etc/bedrock-proxy/ca.crt` |
| `AWS_DEFAULT_REGION` | AWS region, e.g. `ap-northeast-1` |
| `NO_PROXY` / `no_proxy` | every non-AWS destination, comma-separated (see [NO_PROXY maintenance](#no_proxy-maintenance)) |

When `claude` makes a request to Bedrock, it routes through the proxy. The
proxy intercepts the TLS session using a certificate signed by a local CA
(trusted via `NODE_EXTRA_CA_CERTS`). The certificate covers `*.amazonaws.com`
and `*.<region>.amazonaws.com` (e.g. `*.us-east-1.amazonaws.com`), which is
required because the actual Bedrock runtime endpoint is
`bedrock-runtime.<region>.amazonaws.com` — a two-level subdomain not covered
by `*.amazonaws.com` alone. The region is derived from `BEDROCK_BASE_URL` at
cert generation time.

### Install

Run `install-proxy.sh` as root:

```bash
BEDROCK_TOKEN=<token> bash <(curl -fsSL https://raw.githubusercontent.com/irohiroki/hookd/main/install-proxy.sh)
```

`BEDROCK_REGION` defaults to `ap-northeast-1`. Set it to override the region:

```bash
BEDROCK_TOKEN=<token> BEDROCK_REGION=us-east-1 bash <(curl -fsSL ...)
```

`BEDROCK_BASE_URL` and `BEDROCK_PROXY_PORT` can also be set explicitly to bypass the defaults.

The script prints the `env_groups:` block to add to hookd's `config.yml`.

### Hook into hookd

Add an env group to hookd's `config.yml` (the host-specific values are printed
by `install-proxy.sh`):

```yaml
env_groups:
  bedrock:
    CLAUDE_CODE_USE_BEDROCK: "1"
    HTTPS_PROXY: "http://127.0.0.1:8888"
    NODE_EXTRA_CA_CERTS: /etc/bedrock-proxy/ca.crt
    AWS_BEARER_TOKEN_BEDROCK: dummy
    AWS_DEFAULT_REGION: ap-northeast-1
    NO_PROXY: "googleapis.com,.googleapis.com,google.com,.google.com,chatwork.com,.chatwork.com,slack.com,.slack.com,.slack-edge.com,anthropic.com,.anthropic.com"
    no_proxy: "googleapis.com,.googleapis.com,google.com,.google.com,chatwork.com,.chatwork.com,slack.com,.slack.com,.slack-edge.com,anthropic.com,.anthropic.com"
```

Then reload hookd:

```bash
sudo systemctl reload hookd
```

Each route or schedule that uses Bedrock opts in with `env_group: bedrock`:

```yaml
schedules:
  - name: nightly-report
    cron: '0 3 * * *'
    script: /home/alice/scripts/report.sh
    env_group: bedrock
```

Per-route/per-schedule `env:` entries and exports inside the script override
group values, so an existing job that already sets any of these variables
keeps working during migration.

**Migrating from the unit-based setup**: earlier deployments injected these
variables into every job via `Environment=` lines in
`/etc/systemd/system/hookd.service`. Once the env group is in place and every
Bedrock job carries `env_group: bedrock`, remove those `Environment=` lines
and run `sudo systemctl daemon-reload && sudo systemctl restart hookd`.
Until they are removed, non-Bedrock jobs still receive the proxy variables.

### NO_PROXY maintenance

The proxy intercepts **all** HTTPS CONNECT traffic from a job, but its
certificate only covers `*.amazonaws.com`. Any other destination reached
through the proxy fails TLS validation. `NO_PROXY` is therefore a list of
every non-AWS destination a Bedrock job talks to — it cannot be written as
"everything except AWS".

- **When a job gains a new external integration, append `host,.host` to BOTH
  `NO_PROXY` and `no_proxy`** in the env group, then
  `sudo systemctl reload hookd`. A missing entry fails silently — that
  destination breaks with a certificate error while everything else works.
- Both spellings are required because clients disagree on which one they
  read: Python `httplib2` reads only `no_proxy`, `requests`/`urllib` read
  both, `claude` (Node) and `curl` read `NO_PROXY`.
- **Never set `NO_PROXY: "*"`** — Bedrock traffic would bypass the proxy too,
  the `dummy` token would never be replaced with the real one, and every
  Bedrock job would fail authentication.

Check that a host bypasses the proxy:

```bash
NO_PROXY=<list> no_proxy=<list> \
  python3 -c 'import urllib.request as u; print(u.proxy_bypass("oauth2.googleapis.com"))'
# → True
```

### Management

```bash
sudo systemctl stop bedrock-proxy
sudo systemctl restart bedrock-proxy
sudo journalctl -u bedrock-proxy -f
```

Health check:

```bash
curl -s http://127.0.0.1:8888/up
```

### What install-proxy.sh does

1. Creates the `bedrock-proxy` system account
2. Writes the token to `/etc/bedrock-proxy/env` (mode 400, root-owned)
3. Installs `bedrock-proxy.py` and `generate_certs.py` to `/opt/bedrock-proxy/`
4. Generates a local CA and a wildcard `*.amazonaws.com` certificate via `generate_certs.py`
5. Installs and starts `bedrock-proxy.service`
