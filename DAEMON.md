# Running the Webhook Server as a systemd Daemon

This guide explains how to install and manage the webhook server as a persistent
background service on Rocky Linux 9 using systemd.

## Prerequisites

- Rocky Linux 9.x (or any RHEL 9-compatible distro)
- Python 3.9 or later (`/usr/bin/python3`)
- `sudo` access as the `rocky` user
- `webhook.py` and `config.yml` placed in `/home/rocky/webhook/`

## 1. Create the systemd Unit File

Copy `webhook.service` to `/etc/systemd/system/`:

```bash
sudo cp /home/rocky/webhook/webhook.service /etc/systemd/system/webhook.service
sudo chmod 644 /etc/systemd/system/webhook.service
```

The unit file configures the following behaviour:

| Directive            | Purpose                                                        |
|----------------------|----------------------------------------------------------------|
| `User=rocky`         | Run as an unprivileged user, never root                        |
| `Restart=on-failure` | Auto-restart if the process crashes                            |
| `RestartSec=5`       | Wait 5 seconds before each restart attempt                     |
| `StartLimitBurst=5`  | Stop retrying after 5 failures within 60 seconds               |
| `NoNewPrivileges=yes`| Prevent privilege escalation by child processes                |
| `ProtectSystem=strict` | Mount most of the filesystem read-only for this service      |
| `ReadWritePaths=`    | Allow write access only to `/home/rocky/webhook`               |

## 2. Enable and Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now webhook.service
```

`enable --now` both registers the service to start on boot and starts it immediately.

## 3. Open the Firewall Port

```bash
sudo firewall-cmd --permanent --add-port=9000/tcp
sudo firewall-cmd --reload
```

> **AWS note:** You must also add an inbound rule for TCP 9000 in the EC2 Security Group.
> This must be done from the AWS Console or AWS CLI — it cannot be done from inside the instance.

## 4. Verify the Service Is Running

```bash
sudo systemctl status webhook.service
sudo journalctl -u webhook -n 50 --no-pager
```

A healthy `systemctl status` output looks like:

```
● webhook.service - Webhook receiver server
     Loaded: loaded (/etc/systemd/system/webhook.service; enabled; ...)
     Active: active (running) since ...
   Main PID: 12345 (python3)
```

## 5. Common Management Commands

```bash
sudo systemctl stop webhook.service      # stop
sudo systemctl restart webhook.service   # restart (required after config changes)
sudo systemctl disable webhook.service   # remove from boot without stopping
sudo systemctl is-enabled webhook.service
```

## 6. Updating the Configuration

`webhook.py` reads `config.yml` once at startup. After editing the file, restart the service:

```bash
vi /home/rocky/webhook/config.yml
sudo systemctl restart webhook.service
```

## 7. Log Rotation

The server caps `webhook.log` at 10 MB and keeps 5 rotated copies via Python's
`RotatingFileHandler`. No additional `logrotate` configuration is needed.

systemd journal logs rotate automatically via `journald`.

## 8. Troubleshooting

**Service fails to start**

```bash
sudo journalctl -u webhook -n 100 --no-pager
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

systemd halts after `StartLimitBurst=5` failures in 60 seconds. Reset and restart manually:

```bash
sudo systemctl reset-failed webhook.service
sudo systemctl start webhook.service
```

**Script not executing**

Ensure the script exists and is executable:

```bash
ls -l /home/rocky/scripts/deploy.sh
chmod +x /home/rocky/scripts/deploy.sh
```
