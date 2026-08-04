#!/usr/bin/env bash
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/irohiroki/hookd/main"
INSTALL_DIR="/opt/bedrock-proxy"
CONFIG_DIR="/etc/bedrock-proxy"
PROXY_PORT="${BEDROCK_PROXY_PORT:-8888}"

die() { echo "error: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"

# Token
if [ -z "${BEDROCK_TOKEN:-}" ]; then
    read -rsp "AWS_BEARER_TOKEN_BEDROCK: " BEDROCK_TOKEN
    echo
fi
[ -n "$BEDROCK_TOKEN" ] || die "token is required"

BEDROCK_REGION="${BEDROCK_REGION:-ap-northeast-1}"
BEDROCK_BASE_URL="${BEDROCK_BASE_URL:-https://bedrock.${BEDROCK_REGION}.amazonaws.com/v1}"

# System user
if ! id -u bedrock-proxy &>/dev/null; then
    useradd -r -s /sbin/nologin bedrock-proxy
fi

# Directories
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR"

# Token file (root-owned, unreadable by service user — systemd reads it before drop)
cat > "$CONFIG_DIR/env" <<EOF
AWS_BEARER_TOKEN_BEDROCK=$BEDROCK_TOKEN
BEDROCK_REGION=$BEDROCK_REGION
BEDROCK_BASE_URL=$BEDROCK_BASE_URL
BEDROCK_PROXY_PORT=$PROXY_PORT
EOF
chmod 400 "$CONFIG_DIR/env"
chown root:root "$CONFIG_DIR/env"

# Python files
curl -fsSL "$REPO_RAW/bedrock-proxy.py" -o "$INSTALL_DIR/bedrock-proxy.py"
curl -fsSL "$REPO_RAW/generate_certs.py" -o "$INSTALL_DIR/generate_certs.py"
chmod 755 "$INSTALL_DIR/bedrock-proxy.py" "$INSTALL_DIR/generate_certs.py"

# Certificates
python3 -m pip install --quiet cryptography
BEDROCK_PROXY_CONFIG_DIR="$CONFIG_DIR" BEDROCK_PROXY_PORT="$PROXY_PORT" BEDROCK_BASE_URL="$BEDROCK_BASE_URL" \
    python3 "$INSTALL_DIR/generate_certs.py"
chown -R bedrock-proxy:bedrock-proxy "$CONFIG_DIR"
chmod 400 "$CONFIG_DIR/ca.key" "$CONFIG_DIR/server.key"

# Systemd service
curl -fsSL "$REPO_RAW/bedrock-proxy.service" -o /etc/systemd/system/bedrock-proxy.service

systemctl daemon-reload
systemctl enable --now bedrock-proxy

echo
echo "bedrock-proxy is running on 127.0.0.1:$PROXY_PORT"
echo
echo "Add to /etc/systemd/system/hookd.service [Service]:"
echo "  Environment=AWS_BEARER_TOKEN_BEDROCK=dummy"
echo "  Environment=HTTPS_PROXY=http://127.0.0.1:$PROXY_PORT"
echo "  Environment=NODE_EXTRA_CA_CERTS=$CONFIG_DIR/ca.crt"
echo "  Environment=AWS_DEFAULT_REGION=$BEDROCK_REGION"
echo
echo "Then: systemctl daemon-reload && systemctl restart hookd"
