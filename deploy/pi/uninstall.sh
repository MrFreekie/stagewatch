#!/usr/bin/env bash
# Remove the Stagewatch service and kiosk autostart. Data in /var/lib/stagewatch is kept.
set -euo pipefail
sudo systemctl disable --now stagewatch.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/stagewatch.service
sudo systemctl daemon-reload
rm -f "$HOME/.config/autostart/stagewatch-kiosk.desktop" "$HOME/.local/bin/stagewatch-kiosk"
[[ -f "$HOME/.config/labwc/autostart" ]] && sed -i '/stagewatch-kiosk/d' "$HOME/.config/labwc/autostart"
echo "Stagewatch service removed."
