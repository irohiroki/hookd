#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/irohiroki/hookd/main"
INSTALL_DIR="/opt/bedrock-proxy"
CONFIG_DIR="/etc/bedrock-proxy"
SERVICE_NAME="bedrock-proxy"

die() { echo "error: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"

# Token
if [ -z "${BEDROCK_TOKEN:-}" ]; then
    read -rsp "AWS_BEARER_TOKEN_BEDROCK: " BEDROCK_TOKEN
    echo
fi
[ -n "$BEDROCK_TOKEN" ] || die "token is required"

BEDROCK_BASE_URL="${BEDROCK_BASE_URL:-https://bedrock.us-east-1.amazonaws.com/v1}"

# System user
if ! id -u bedrock-proxy &>/dev/null; then
    useradd -r -s /sbin/nologin bedrock-proxy
fi

# Directories
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"

# Token file
cat > "$CONFIG_DIR/env" <<EOF
AWS_BEARER_TOKEN_BEDROCK=$BEDROCK_TOKEN
BEDROCK_BASE_URL=$BEDROCK_BASE_URL
EOF
chmod 400 "$CONFIG_DIR/env"
chown root:root "$CONFIG_DIR/env"

# Python daemon
curl -fsSL "$REPO_RAW/bedrock-proxy.py" -o "$INSTALL_DIR/bedrock-proxy.py"
chmod 755 "$INSTALL_DIR/bedrock-proxy.py"

# Systemd service
curl -fsSL "$REPO_RAW/bedrock-proxy.service" -o /etc/systemd/system/bedrock-proxy.service

systemctl daemon-reload
systemctl enable --now bedrock-proxy

echo "bedrock-proxy is running. socket: /run/bedrock-proxy/proxy.sock"
echo "add to hookd.service: Environment=BEDROCK_PROXY_SOCK=/run/bedrock-proxy/proxy.sock"
