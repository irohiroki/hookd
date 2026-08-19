#!/bin/bash
# deploy.sh — deploy hookd to a remote host via pip.
#
# Prerequisites:
#   - SSH to the host given as the first argument requires no extra options
#   - sudo is available on the target host
#   - pip3 is available on the target host
#
# The per-user config directory is not touched here: install.sh records it in
# /etc/hookd/routes_dir, and both hookd and hookctl read it from there.

set -euo pipefail

HOST="${1:?Usage: $0 <hostname>}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[$HOST] Uploading package..."
git -C "$DIR" archive HEAD | ssh "$HOST" "cat > /tmp/hookd-src.tar"

echo "[$HOST] Installing via pip and restarting hookd..."
ssh "$HOST" bash -s <<'REMOTE'
set -euo pipefail
tmp=$(mktemp -d)
trap 'rm -rf "$tmp" /tmp/hookd-src.tar' EXIT
tar x -C "$tmp" < /tmp/hookd-src.tar
sudo pip3 install --quiet "$tmp"
sudo systemctl restart hookd
REMOTE

echo "[$HOST] Done — hookd restarted"
