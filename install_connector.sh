#!/usr/bin/env bash
# Install hermes-channel-chija binary into ~/.config/chija/hermes-channel/bin/
# Usage: bash install_connector.sh [release-tag]
set -euo pipefail
TAG="${1:-v0.2.9}"
BASE="${HERMES_CHIJA_CONNECTOR_RELEASE_BASE:-https://github.com/chija-packages/hermes/releases/download}"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH_RAW="$(uname -m | tr '[:upper:]' '[:lower:]')"
case "$ARCH_RAW" in
  x86_64|amd64) ARCH=amd64 ;;
  arm64|aarch64) ARCH=arm64 ;;
  *) echo "unsupported arch: $ARCH_RAW" >&2; exit 1 ;;
esac
case "$OS" in
  darwin|linux) ;;
  mingw*|msys*|cygwin*) OS=windows ;;
  *) echo "unsupported OS: $OS" >&2; exit 1 ;;
esac

BIN_NAME="hermes-channel-chija"
ASSET="hermes-channel-chija-${OS}-${ARCH}"
if [[ "$OS" == "windows" ]]; then
  BIN_NAME="${BIN_NAME}.exe"
  ASSET="${ASSET}.exe"
fi

DEST_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/chija/hermes-channel/bin"
DEST="$DEST_DIR/$BIN_NAME"
URL="$BASE/$TAG/$ASSET"
mkdir -p "$DEST_DIR"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
echo "Downloading $URL"
curl -fsSL "$URL" -o "$TMP"
chmod 755 "$TMP"
mv "$TMP" "$DEST"
trap - EXIT
echo "Installed $DEST"
"$DEST" 2>/dev/null | head -1 || true
