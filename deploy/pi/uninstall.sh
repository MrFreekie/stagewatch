#!/usr/bin/env bash
# Remove the Stagewatch service and kiosk autostart. Data in /var/lib/stagewatch (and the program
# in /opt/stagewatch) is kept. Works for the current install and for an older one (service run
# from your own checkout): both used the same service name.
set -euo pipefail
# the clock button's helper, sudo rule and service drop-in (if they were set up)
if [[ -f "$(dirname "$0")/install-clock-helper.sh" ]]; then
  sudo bash "$(dirname "$0")/install-clock-helper.sh" --uninstall >/dev/null 2>&1 || true
fi
sudo systemctl disable --now stagewatch.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/stagewatch.service
sudo systemctl daemon-reload
rm -f "$HOME/.config/autostart/stagewatch-kiosk.desktop" "$HOME/.local/bin/stagewatch-kiosk"
[[ -f "$HOME/.config/labwc/autostart" ]] && sed -i '/stagewatch-kiosk/d' "$HOME/.config/labwc/autostart"
echo "Stagewatch service removed."
