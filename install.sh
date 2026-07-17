#!/bin/bash
# hookd installer
#
# Full install (as root):
#   HOOKD_USER=<user> bash install.sh
#
# User files only (admin handles system parts separately):
#   bash install.sh
#
# Environment variables:
#   HOOKD_USER       Service user account (required when running as root)
#   HOOKD_PORT       Listening port (default: 9000)
#   HOOKD_ROUTES_DIR Per-user config directory (default: /var/lib/hookd/routes.d)
#   HOOKD_DIR        Install directory (default: ~/hookd)

set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/irohiroki/hookd/main"
MODULES="hookd.py cron.py config.py handler.py runner.py user.py"

HOOKD_PORT="${HOOKD_PORT:-9000}"
HOOKD_ROUTES_DIR="${HOOKD_ROUTES_DIR:-/var/lib/hookd/routes.d}"

die() { echo "error: $*" >&2; exit 1; }

command -v curl    > /dev/null 2>&1 || die "curl is required"
command -v python3 > /dev/null 2>&1 || die "python3 is required"

if [[ "$EUID" -eq 0 ]]; then
    [[ -n "${HOOKD_USER:-}" ]] || die "HOOKD_USER must be set when running as root"
    id "$HOOKD_USER" > /dev/null 2>&1 || die "user '$HOOKD_USER' does not exist"
    eval HOOKD_HOME="~$HOOKD_USER"
    HOOKD_GROUP=$(id -gn "$HOOKD_USER")
    IS_ROOT=1
else
    HOOKD_USER="$(id -un)"
    HOOKD_HOME="$HOME"
    HOOKD_GROUP="$(id -gn)"
    IS_ROOT=0
fi

HOOKD_DIR="${HOOKD_DIR:-$HOOKD_HOME/hookd}"

# System-level setup (root only)
if [[ "$IS_ROOT" -eq 1 ]]; then
    if [[ ! -d "$HOOKD_HOME" ]]; then
        echo "Creating home directory $HOOKD_HOME"
        mkdir -p "$HOOKD_HOME"
        chown "$HOOKD_USER:$HOOKD_GROUP" "$HOOKD_HOME"
        chmod 700 "$HOOKD_HOME"
    fi

    echo "Creating $HOOKD_ROUTES_DIR"
    mkdir -p "$HOOKD_ROUTES_DIR"
    python3 -c "import os; os.chmod('$HOOKD_ROUTES_DIR', 0o1777)"

    echo "Installing /usr/local/bin/hookctl"
    curl -fsSL "$REPO_RAW/hookctl" \
        | sed "s|ROUTES_DIR = '/home/rocky/hookd/routes.d'|ROUTES_DIR = '$HOOKD_ROUTES_DIR'|" \
        > /usr/local/bin/hookctl
    chmod 755 /usr/local/bin/hookctl

    echo "Installing /etc/systemd/system/hookd.service"
    curl -fsSL "$REPO_RAW/hookd.service" \
        | sed \
            -e "s|User=rocky|User=$HOOKD_USER|" \
            -e "s|Group=rocky|Group=$HOOKD_GROUP|" \
            -e "s|/home/rocky/hookd|$HOOKD_DIR|g" \
        > /etc/systemd/system/hookd.service
fi

# User-level setup
echo "Installing hookd to $HOOKD_DIR"
mkdir -p "$HOOKD_DIR"

for mod in $MODULES; do
    curl -fsSL "$REPO_RAW/$mod" -o "$HOOKD_DIR/$mod"
done

if [[ ! -f "$HOOKD_DIR/config.yml" ]]; then
    cat > "$HOOKD_DIR/config.yml" << CONF
server:
  host: "0.0.0.0"
  port: $HOOKD_PORT

log:
  file: $HOOKD_DIR/hookd.log
  level: INFO
  max_bytes: 10485760
  backup_count: 5

pidfile: $HOOKD_DIR/hookd.pid

routes_dir: $HOOKD_ROUTES_DIR

routes: []
schedules: []
CONF
    chmod 600 "$HOOKD_DIR/config.yml"
fi

if [[ "$IS_ROOT" -eq 1 ]]; then
    chown -R "$HOOKD_USER:$HOOKD_GROUP" "$HOOKD_DIR"
    loginctl enable-linger "$HOOKD_USER"
    systemctl daemon-reload
    if systemctl is-active --quiet hookd; then
        systemctl restart hookd
    else
        systemctl enable --now hookd
    fi
    echo ""
    echo "hookd installed and running on port $HOOKD_PORT"
    echo "  install dir : $HOOKD_DIR"
    echo "  routes dir  : $HOOKD_ROUTES_DIR"
    echo "  health check: curl http://127.0.0.1:$HOOKD_PORT/up"
else
    echo ""
    echo "hookd user files installed to $HOOKD_DIR"
    echo "To complete setup, an admin must run:"
    echo "  HOOKD_USER=$HOOKD_USER HOOKD_PORT=$HOOKD_PORT HOOKD_ROUTES_DIR=$HOOKD_ROUTES_DIR bash install.sh"
fi
