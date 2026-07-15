# Running hookd as a systemd Daemon

This guide explains how to install and manage hookd as a persistent background
service on Rocky Linux 9 using systemd.

## Prerequisites

- Rocky Linux 9.x (or any RHEL 9-compatible distro)
- Python 3.9 or later (`/usr/bin/python3`)
- `sudo` access as the `rocky` user
- `hookd.py` and `config.yml` placed in `/home/rocky/hookd/`

## 1. Create the systemd Unit File

Copy `hookd.service` to `/etc/systemd/system/`:

```bash
sudo cp /home/rocky/hookd/hookd.service /etc/systemd/system/hookd.service
sudo chmod 644 /etc/systemd/system/hookd.service
```

The unit file configures the following behaviour:

| Directive             | Purpose                                                      |
|-----------------------|--------------------------------------------------------------|
| `User=rocky`          | Run as an unprivileged user, never root                      |
| `Restart=on-failure`  | Auto-restart if the process crashes                          |
| `RestartSec=5`        | Wait 5 seconds before each restart attempt                   |
| `StartLimitBurst=5`   | Stop retrying after 5 failures within 60 seconds             |
| `NoNewPrivileges=yes` | Prevent privilege escalation by child processes              |
| `ProtectSystem=strict`| Mount most of the filesystem read-only for this service      |
| `ReadWritePaths=`     | Allow write access only to `/home/rocky/hookd`               |

## 2. Enable and Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hookd.service
```

`enable --now` both registers the service to start on boot and starts it immediately.

## 3. Set Up routes.d Directory

Users register their routes and schedules via the `hookctl` command. The
`routes.d/` directory must be world-writable with the sticky bit:

```bash
mkdir -p /home/rocky/hookd/routes.d
python3 -c "import os; os.chmod('/home/rocky/hookd/routes.d', 0o1777)"
```

## 4. Install hookctl

```bash
sudo cp /home/rocky/hookd/hookctl /usr/local/bin/hookctl
sudo chmod 755 /usr/local/bin/hookctl
```

## 5. Verify the Service Is Running

```bash
sudo systemctl status hookd.service
sudo journalctl -u hookd -n 50 --no-pager
```

A healthy `systemctl status` output looks like:

```
● hookd.service - hookd — webhook and schedule dispatcher
     Loaded: loaded (/etc/systemd/system/hookd.service; enabled; ...)
     Active: active (running) since ...
   Main PID: 12345 (python3)
```

## 6. Common Management Commands

```bash
sudo systemctl stop hookd.service      # stop
sudo systemctl restart hookd.service   # restart (required after config changes)
sudo systemctl disable hookd.service   # remove from boot without stopping
sudo systemctl is-enabled hookd.service
```

## 7. Updating the Configuration

`hookd.py` reads `config.yml` once at startup. After editing the file, restart:

```bash
vi /home/rocky/hookd/config.yml
sudo systemctl restart hookd.service
```

User-registered routes and schedules reload automatically within 2 seconds of
a `hookctl` invocation — no restart needed.

## 8. Log Rotation

hookd caps `hookd.log` at 10 MB and keeps 5 rotated copies via Python's
`RotatingFileHandler`. No additional `logrotate` configuration is needed.

systemd journal logs rotate automatically via `journald`.

## 9. Troubleshooting

**Service fails to start**

```bash
sudo journalctl -u hookd -n 100 --no-pager
```

Common causes:

- `config.yml` syntax error — validate with:
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
sudo systemctl reset-failed hookd.service
sudo systemctl start hookd.service
```

**Script not executing**

Ensure the script exists and is executable:

```bash
ls -l /home/rocky/scripts/deploy.sh
python3 -c "import os, stat; os.chmod('/home/rocky/scripts/deploy.sh', stat.S_IRWXU|stat.S_IRGRP|stat.S_IXGRP|stat.S_IROTH|stat.S_IXOTH)"
```
