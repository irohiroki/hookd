# Deploying hookd on a New Host

This guide covers installation and day-to-day operations.

## Prerequisites

| Requirement | Check |
|---|---|
| Rocky Linux 9 / RHEL 9-compatible | `cat /etc/os-release` |
| Python 3.9+ with PyYAML | `python3 -c 'import yaml'` |
| curl | `curl --version` |
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
| `HOOKD_ROUTES_DIR` | `/var/lib/hookd/routes.d` | Per-user config directory |
| `HOOKD_DIR` | `~/hookd` | Install directory |

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
3. Downloads and installs `hookctl` to `/usr/local/bin/`
4. Downloads and installs `hookd.service` to `/etc/systemd/system/` with all
   paths and the user name substituted
5. Downloads the six Python modules to `HOOKD_DIR`
6. Generates `config.yml` in `HOOKD_DIR` (skipped if already present)
7. Enables the service to persist across logins and starts it

When run without root, steps 1–4 and 7 are skipped.

## Management

```bash
sudo systemctl stop hookd
sudo systemctl restart hookd
sudo systemctl disable hookd
sudo systemctl is-enabled hookd
```

## Updating the admin config

`hookd.py` reads `config.yml` once at startup. After editing, restart:

```bash
sudo systemctl restart hookd
```

User-registered routes and schedules reload automatically within 2 seconds of a
`hookctl` invocation — no restart needed.

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
python3 -c "import os, stat; os.chmod('/path/to/script.sh', stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)"
```

