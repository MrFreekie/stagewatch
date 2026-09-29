#!/usr/bin/env bash
# Install Stagewatch on Raspberry Pi OS (Bookworm, 64-bit) as a systemd service
# that starts at boot and restarts on failure. Optional HDMI kiosk display.
#
#   cd ~/stagewatch && bash deploy/pi/install.sh [--kiosk] [--port 8080] [--emulate]
#
# Needs uv (https://docs.astral.sh/uv/). Run as the normal desktop user (not root);
# it uses sudo only for the systemd unit and data directory.
set -euo pipefail

PORT=8080
KIOSK=0
EMULATE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kiosk) KIOSK=1 ;;
    --port) PORT="$2"; shift ;;
    --emulate) EMULATE="--emulate" ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
  shift
done

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
USER_NAME="$(id -un)"
DATA_DIR=/var/lib/stagewatch

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

echo "Repository: $REPO"
(cd "$REPO" && uv sync --frozen --no-dev)

sudo mkdir -p "$DATA_DIR"
sudo chown "$USER_NAME":"$USER_NAME" "$DATA_DIR"

sudo tee /etc/systemd/system/stagewatch.service >/dev/null <<EOF
[Unit]
Description=Stagewatch show-site monitoring hub
Wants=network-online.target
After=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$REPO
ExecStart=$REPO/.venv/bin/python -m stagewatch --port $PORT --data-dir $DATA_DIR $EMULATE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now stagewatch.service
echo "Service enabled: sudo systemctl status stagewatch"

if [[ $KIOSK -eq 1 ]]; then
  install -m 755 "$REPO/deploy/pi/kiosk.sh" "$HOME/.local/bin/stagewatch-kiosk" 2>/dev/null || {
    mkdir -p "$HOME/.local/bin"; install -m 755 "$REPO/deploy/pi/kiosk.sh" "$HOME/.local/bin/stagewatch-kiosk"; }
  # Bookworm desktop (labwc / Wayland) autostart
  mkdir -p "$HOME/.config/labwc"
  AUTOSTART="$HOME/.config/labwc/autostart"
  grep -q stagewatch-kiosk "$AUTOSTART" 2>/dev/null || \
    echo "$HOME/.local/bin/stagewatch-kiosk http://localhost:$PORT/d/wall &" >> "$AUTOSTART"
  # Older X11/LXDE desktop autostart
  mkdir -p "$HOME/.config/autostart"
  cat > "$HOME/.config/autostart/stagewatch-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Stagewatch kiosk
Exec=$HOME/.local/bin/stagewatch-kiosk http://localhost:$PORT/d/wall
EOF
  echo "Kiosk enabled: the 'wall' dashboard opens full screen after login."
  echo "Enable desktop auto-login in raspi-config (System Options > Boot / Auto Login)."
fi

echo "Done. Open http://$(hostname).local:$PORT from a tablet on the same network."
