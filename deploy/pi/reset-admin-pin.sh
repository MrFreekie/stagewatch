#!/usr/bin/env bash
# Forgotten the Stagewatch admin PIN, or the admin page says "Recovery required"?
# Run this on the Stagewatch Raspberry Pi (managed install):
#
#   bash /opt/stagewatch/deploy/pi/reset-admin-pin.sh
#
# It stops the service, clears the admin PIN in the data folder, and starts the service again.
# Devices, thresholds and history are not touched. Until you set a new PIN, anyone on the
# network can set it, so do it straight away.
#
# *** UNTESTED ON HARDWARE ***
set -uo pipefail

CODE=/opt/stagewatch
DATA_DIR=/var/lib/stagewatch
SVC_USER=stagewatch
EMULATE=""

# Read the real folders from the install's marker when it is there.
MARKER="$CODE/.git/stagewatch-managed.json"
if [[ -r "$MARKER" ]] && command -v python3 >/dev/null 2>&1; then
  eval "$(python3 - "$MARKER" <<'PY'
import json, shlex, sys
m = json.load(open(sys.argv[1], encoding="utf-8-sig"))
print("CODE=" + shlex.quote(str(m.get("repo_path", "/opt/stagewatch"))))
print("DATA_DIR=" + shlex.quote(str(m.get("data_dir", "/var/lib/stagewatch"))))
print("EMULATE=" + ("--emulate" if m.get("emulate") else ""))
PY
)"
fi

if [[ ! -x "$CODE/.venv/bin/python" ]]; then
  echo "Could not find Stagewatch at $CODE. Is this a managed install (install.sh --managed)?"
  exit 1
fi
if [[ ! -d "$DATA_DIR" ]]; then
  echo "Could not find the data folder $DATA_DIR."
  exit 1
fi

echo "Stopping Stagewatch ..."
sudo systemctl stop stagewatch.service
sleep 3

# cd / : the service user usually cannot enter your home folder.
# shellcheck disable=SC2086
(cd / && sudo -u "$SVC_USER" env "PYTHONPATH=$CODE/src" \
  "$CODE/.venv/bin/python" -P -m stagewatch reset-admin-pin --data-dir "$DATA_DIR" $EMULATE)
RC=$?

echo "Starting Stagewatch again ..."
sudo systemctl start stagewatch.service

echo
if [[ $RC -eq 0 ]]; then
  echo "Done. The admin PIN has been cleared."
  echo "Next: open Stagewatch in your browser (http://$(hostname).local:8080/admin) and set a new PIN now."
  echo "Until you do, anyone on the network could set it."
else
  echo "The reset did not work (see the message above). Stagewatch has been started again; nothing was changed."
  echo "If you are stuck, use 'Download diagnostics' on the admin page if you can, and ask for help with the message above."
fi
exit $RC
