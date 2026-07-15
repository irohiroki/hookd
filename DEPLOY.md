# Deploying hookd on a New Host

This guide covers the one-time setup to run hookd on a host where the service
account has no pre-existing home directory and limited sudo access. Two roles
are involved:

- **admin** — a user with sudo or root access
- **service user** — the Linux account hookd will run as (e.g., `hookd`)

Replace `<user>` and `<group>` throughout with the actual account name and
primary group. For day-to-day operations after setup, see `DAEMON.md`.

## Prerequisites

| Requirement | Check |
|---|---|
| Rocky Linux 9 / RHEL 9-compatible | `cat /etc/os-release` |
| Python 3.9+ | `python3 --version` |
| PyYAML | `python3 -c 'import yaml'` |
| systemd | `systemctl --version` |

## File Layout

| Path | Mode | Created by |
|---|---|---|
| `/home/<user>/` | `0700` | admin |
| `/home/<user>/hookd/*.py` | `0644` | service user |
| `/home/<user>/hookd/config.yml` | `0600` | service user |
| `/home/<user>/hookd/hookd.log` | — | runtime |
| `/home/<user>/hookd/hookd.pid` | — | runtime |
| `/var/lib/hookd/routes.d/` | `1777` | admin |
| `/etc/systemd/system/hookd.service` | `0644` | admin |
| `/usr/local/bin/hookctl` | `0755` | admin |

## Admin Steps

### 1. Create the service user's home directory

If the account exists but has no home directory:

```bash
sudo mkdir -p /home/<user>
sudo chown <user>:<group> /home/<user>
sudo chmod 700 /home/<user>
```

### 2. Create routes.d

The sticky bit allows each user to write only their own file.

```bash
sudo mkdir -p /var/lib/hookd/routes.d
sudo chmod 1777 /var/lib/hookd/routes.d
```

### 3. Install the system service

Start from `hookd.service` in the repository and substitute the rocky-specific
paths with values for this host:

| Line in repository file | Replace with |
|---|---|
| `User=rocky` | `User=<user>` |
| `Group=rocky` | `Group=<group>` |
| `WorkingDirectory=/home/rocky/hookd` | `WorkingDirectory=/home/<user>/hookd` |
| `ExecStart=... /home/rocky/hookd/...` | `... /home/<user>/hookd/...` |
| `ReadWritePaths=/home/rocky/hookd` | `ReadWritePaths=/home/<user>/hookd` |

The `AmbientCapabilities` and `CapabilityBoundingSet` lines in the repository
file let hookd drop privileges to each route owner's UID when running their
scripts. Keep them unless per-user script execution is not required.

Install the adapted file and start the service:

```bash
sudo cp hookd.service /etc/systemd/system/hookd.service
sudo systemctl daemon-reload
sudo systemctl enable --now hookd
```

### 4. Install hookctl

`hookctl` is the CLI that users run to register their routes and schedules.
The `ROUTES_DIR` constant must match `routes_dir` in `config.yml`.

Copy `hookctl` from the repository:

```bash
sudo install -m 755 /path/to/hookd/hookctl /usr/local/bin/hookctl
```

Then edit `ROUTES_DIR` in `/usr/local/bin/hookctl` to point to the routes
directory created in step 2:

```python
ROUTES_DIR = '/var/lib/hookd/routes.d'
```

### 5. Enable linger

Without linger, systemd stops user-session processes when the service account's
last login session ends. Enable it so hookd persists:

```bash
sudo loginctl enable-linger <user>
```

## Service User Steps

Log in as the service user and run these commands.

### 1. Create the hookd directory and copy the Python modules

```bash
mkdir -p /home/<user>/hookd
```

From the machine that holds the hookd repository, copy the six Python modules
to the host. If SSH config has an alias for the host (e.g., `hive`):

```bash
scp {hookd.py,cron.py,config.py,handler.py,runner.py,user.py} \
    hive:/home/<user>/hookd/
```

### 2. Create config.yml

Create `/home/<user>/hookd/config.yml`:

```yaml
server:
  host: "0.0.0.0"
  port: 9000

log:
  file: /home/<user>/hookd/hookd.log
  level: INFO
  max_bytes: 10485760
  backup_count: 5

pidfile: /home/<user>/hookd/hookd.pid

routes_dir: /var/lib/hookd/routes.d

routes: []
schedules: []
```

Adjust `port` if 9000 is already in use on the host.

## Verification

Check that the service is running:

```bash
systemctl status hookd
```

Confirm the HTTP listener responds:

```bash
curl -s -o /dev/null -w '%{http_code}' \
    -X POST http://localhost:9000/nonexistent \
    -H 'Content-Type: application/json' -d '{}'
# Expected: 404
```

A 404 response confirms hookd is serving requests. No routes are registered yet.
Users can now register their routes with `hookctl <config.yml>`.
