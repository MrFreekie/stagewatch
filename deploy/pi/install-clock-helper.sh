#!/usr/bin/env bash
# Set up (or remove) the "Set the clock from this device" button on a Raspberry Pi / Debian install.
#
#   sudo bash /opt/stagewatch/deploy/pi/install-clock-helper.sh [--user stagewatch] [--uninstall]
#
# Run as root (put sudo in front). It installs, from this source folder:
#   /usr/local/sbin/stagewatch-set-time           the tiny helper (root-owned, mode 0755)
#   /etc/sudoers.d/stagewatch-set-time            lets the Stagewatch service user run THAT ONE FILE as root
#   /etc/systemd/system/stagewatch.service.d/clock-helper.conf
#                                                 lets the service use sudo (see SECURITY.md), then restarts it
# The sudoers rule is checked with visudo before it is put in place. --uninstall removes all three.
set -euo pipefail

SVC_USER=stagewatch
UNINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) SVC_USER="${2:?--user needs a value}"; shift ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
  shift
done

HERE="$(cd "$(dirname "$0")" && pwd)"
HELPER=/usr/local/sbin/stagewatch-set-time
SUDOERS=/etc/sudoers.d/stagewatch-set-time
DROPIN_DIR=/etc/systemd/system/stagewatch.service.d
DROPIN="$DROPIN_DIR/clock-helper.conf"

if [[ "$(id -u)" != "0" ]]; then
  echo "Run this with sudo in front:  sudo bash $HERE/install-clock-helper.sh"
  exit 1
fi
[[ "$SVC_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || { echo "That is not a valid user name."; exit 1; }

restart_service() {
  systemctl daemon-reload
  if systemctl is-active --quiet stagewatch.service 2>/dev/null; then
    echo "Restarting Stagewatch so the change takes effect (about 10 seconds)."
    systemctl restart stagewatch.service
  fi
}

if [[ $UNINSTALL -eq 1 ]]; then
  rm -f "$HELPER" "$SUDOERS" "$DROPIN"
  rmdir "$DROPIN_DIR" 2>/dev/null || true
  restart_service
  echo "The clock button is switched off and its files are removed."
  exit 0
fi

[[ -f "$HERE/stagewatch-set-time" ]] || { echo "Cannot find stagewatch-set-time next to this script."; exit 1; }
id "$SVC_USER" >/dev/null 2>&1 || { echo "There is no user called $SVC_USER. Use --user <name> for the user the Stagewatch service runs as."; exit 1; }
command -v visudo >/dev/null 2>&1 || { echo "visudo not found (apt install sudo)."; exit 1; }

install -o root -g root -m 0755 "$HERE/stagewatch-set-time" "$HELPER"

# Write the sudoers rule to a hidden name first (sudo ignores files with a dot), check it, then rename.
TMP="$(mktemp)"
trap 'rm -f "$TMP" /etc/sudoers.d/.stagewatch-set-time.new' EXIT
echo "$SVC_USER ALL=(root) NOPASSWD: $HELPER" > "$TMP"
visudo -cf "$TMP" >/dev/null || { echo "The sudo rule did not pass the check. Nothing was changed."; exit 1; }
install -o root -g root -m 0440 "$TMP" /etc/sudoers.d/.stagewatch-set-time.new
mv -f /etc/sudoers.d/.stagewatch-set-time.new "$SUDOERS"

# The service is started with "no new privileges", which stops sudo working. Allow it for this service only.
install -d -o root -g root -m 0755 "$DROPIN_DIR"
cat > "$DROPIN" <<EOT
# Added by install-clock-helper.sh so the service can run /usr/local/sbin/stagewatch-set-time through sudo.
[Service]
NoNewPrivileges=no
RestrictSUIDSGID=no
EOT
chmod 0644 "$DROPIN"
restart_service
echo "Done. In Admin, the Clock card can now set this Pi's clock from your tablet or phone."
