#!/usr/bin/env bash
# Install Stagewatch on Raspberry Pi OS (Bookworm, 64-bit) as a systemd service
# that starts at boot and restarts on failure. Optional HDMI kiosk display.
#
#   cd ~/stagewatch && bash deploy/pi/install.sh [--kiosk] [--port 8080] [--emulate]
#   bash deploy/pi/install.sh --managed [--channel stable|nightly] [--ref <tag-or-sha>]
#        [--source-url https://github.com/MrFreekie/stagewatch.git] [--kiosk] [--port 8080] [--emulate]
#
# --managed: *** UNTESTED ON HARDWARE *** Installs a hardened, updatable service in
#   /opt/stagewatch, run by a dedicated no-login system user "stagewatch" (no sudo), with data
#   in /var/lib/stagewatch (mode 0700). The service runs the launcher (crash restart, in-app
#   updates and rollback). The copy in /opt/stagewatch is a fresh clone; the checkout you run
#   this script from is not used by the service. The kiosk stays on the desktop user.
# Without --managed: the older mode (service runs from this checkout as you; no in-app updates).
#
# Needs uv (https://docs.astral.sh/uv/) and, for --managed, git and python3. Run as the normal
# desktop user (not root); it uses sudo only for the systemd unit, the system user and the
# data directory.
set -euo pipefail

PORT=8080
KIOSK=0
EMULATE=""
MANAGED=0
CHANNEL=stable
REF=""
SOURCE_URL="https://github.com/MrFreekie/stagewatch.git"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --managed) MANAGED=1 ;;
    --channel) CHANNEL="$2"; shift ;;
    --ref) REF="$2"; shift ;;
    --source-url) SOURCE_URL="$2"; shift ;;
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

install_managed() {
  local SVC_USER=stagewatch
  local CODE=/opt/stagewatch
  local GIT_BIN UV_SRC
  GIT_BIN="$(command -v git || true)"
  [[ -n "$GIT_BIN" ]] || { echo "git not found (apt install git)."; exit 1; }
  command -v python3 >/dev/null || { echo "python3 not found."; exit 1; }
  [[ "$CHANNEL" == "stable" || "$CHANNEL" == "nightly" ]] || { echo "--channel must be stable or nightly"; exit 1; }
  [[ "$SOURCE_URL" == https://* ]] || { echo "--source-url must be https://"; exit 1; }
  UV_SRC="$(command -v uv)"

  # dedicated system user: no login shell, no sudo, no password
  if ! id "$SVC_USER" >/dev/null 2>&1; then
    sudo useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
  fi
  # refuse a pre-existing code dir that someone else owns
  if [[ -e "$CODE" ]]; then
    local owner
    owner="$(stat -c %U "$CODE")"
    if [[ "$owner" != "$SVC_USER" && "$owner" != "root" ]]; then
      echo "$CODE exists and is owned by $owner; refusing."; exit 1
    fi
  fi
  sudo systemctl stop stagewatch.service 2>/dev/null || true
  sudo install -d -o "$SVC_USER" -g "$SVC_USER" -m 0700 "$DATA_DIR"
  sudo install -d -o "$SVC_USER" -g "$SVC_USER" -m 0755 "$CODE"

  # everything below runs as the service user
  local RUN=(sudo -u "$SVC_USER" env "HOME=$DATA_DIR" GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1
             GIT_CONFIG_GLOBAL=/dev/null)
  local G=("$GIT_BIN" -c "safe.directory=$CODE" -c core.hooksPath=/dev/null -c core.fsmonitor=false
           -c credential.helper= -c protocol.allow=never -c protocol.https.allow=always)
  if [[ ! -d "$CODE/.git" ]]; then
    "${RUN[@]}" "${G[@]}" clone --quiet "$SOURCE_URL" "$CODE"
  else
    [[ "$("${RUN[@]}" "${G[@]}" -C "$CODE" config --get remote.origin.url)" == "$SOURCE_URL" ]] \
      || { echo "existing clone has a different origin; refusing."; exit 1; }
    "${RUN[@]}" "${G[@]}" -C "$CODE" fetch --quiet --no-tags origin \
      +refs/heads/main:refs/remotes/origin/main +refs/heads/nightly:refs/remotes/origin/nightly 'refs/tags/v*:refs/tags/v*'
  fi
  local SHA
  if [[ -n "$REF" ]]; then
    SHA="$("${RUN[@]}" "${G[@]}" -C "$CODE" rev-parse --verify --quiet "$REF^{commit}")"
  elif [[ "$CHANNEL" == "nightly" ]]; then
    SHA="$("${RUN[@]}" "${G[@]}" -C "$CODE" rev-parse --verify --quiet "refs/remotes/origin/nightly^{commit}")"
  else
    local TAG
    TAG="$("${RUN[@]}" "${G[@]}" -C "$CODE" tag --list 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)"
    [[ -n "$TAG" ]] || { echo "no release tag found; pass --ref <tag-or-sha>"; exit 1; }
    echo "Latest release tag: $TAG"
    SHA="$("${RUN[@]}" "${G[@]}" -C "$CODE" rev-parse --verify --quiet "refs/tags/$TAG^{commit}")"
  fi
  [[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "could not resolve ref"; exit 1; }
  # must be reachable from origin/main (exit 0 = yes; anything else refuses)
  "${RUN[@]}" "${G[@]}" -C "$CODE" merge-base --is-ancestor "$SHA" refs/remotes/origin/main \
    || { echo "$SHA is not an ancestor of origin/main; refusing."; exit 1; }
  "${RUN[@]}" "${G[@]}" -C "$CODE" checkout --quiet --detach "$SHA"

  # self-contained toolchain inside the code dir
  local UV_DIR="$CODE/.uv"
  sudo -u "$SVC_USER" mkdir -p "$UV_DIR/bin" "$UV_DIR/python" "$UV_DIR/cache"
  sudo install -o "$SVC_USER" -g "$SVC_USER" -m 0755 "$UV_SRC" "$UV_DIR/bin/uv"
  local UVENV=("UV_PYTHON_INSTALL_DIR=$UV_DIR/python" "UV_CACHE_DIR=$UV_DIR/cache"
               UV_PYTHON_INSTALL_BIN=0 UV_MANAGED_PYTHON=1)
  (cd "$CODE" \
    && "${RUN[@]}" "${UVENV[@]}" "$UV_DIR/bin/uv" python install \
    && "${RUN[@]}" "${UVENV[@]}" UV_PYTHON_DOWNLOADS=never "$UV_DIR/bin/uv" sync --frozen --no-dev --no-install-project)

  # managed marker (python3 does the JSON escaping)
  local EMU=false
  [[ -n "$EMULATE" ]] && EMU=true
  sudo -u "$SVC_USER" env M_CODE="$CODE" M_ORIGIN="$SOURCE_URL" M_GIT="$GIT_BIN" M_UV="$UV_DIR/bin/uv" \
    M_DATA="$DATA_DIR" M_CHANNEL="$CHANNEL" M_PORT="$PORT" M_EMU="$EMU" M_UVDIR="$UV_DIR" python3 - <<'PY'
import json, os, time
e = os.environ
m = {"format": 1, "repo_path": e["M_CODE"], "origin_url": e["M_ORIGIN"], "git_path": e["M_GIT"],
     "uv_path": e["M_UV"], "data_dir": e["M_DATA"], "channel": e["M_CHANNEL"], "port": int(e["M_PORT"]),
     "emulate": e["M_EMU"] == "true", "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
     "uv_env": {"UV_PYTHON_INSTALL_DIR": e["M_UVDIR"] + "/python", "UV_CACHE_DIR": e["M_UVDIR"] + "/cache",
                "UV_PYTHON_INSTALL_BIN": "0", "UV_MANAGED_PYTHON": "1"}}
with open(e["M_CODE"] + "/.git/stagewatch-managed.json", "w", encoding="utf-8") as f:
    json.dump(m, f, indent=2)
PY
  sudo -u "$SVC_USER" mkdir -p "$CODE/.git/stagewatch/backups"
  sudo -u "$SVC_USER" touch "$CODE/.git/stagewatch/gitconfig"

  sudo tee /etc/systemd/system/stagewatch.service >/dev/null <<EOF
[Unit]
Description=Stagewatch show-site monitoring hub (launcher)
Wants=network-online.target
After=network-online.target

[Service]
User=$SVC_USER
Group=$SVC_USER
WorkingDirectory=$CODE
Environment=HOME=$DATA_DIR
ExecStart=$CODE/.venv/bin/python -P $CODE/src/stagewatch/launcher.py --marker $CODE/.git/stagewatch-managed.json
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=$CODE $DATA_DIR
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now stagewatch.service
  echo "Managed service enabled (UNTESTED ON HARDWARE): sudo systemctl status stagewatch"
  echo "Code: $CODE ($SHA, detached)   Data: $DATA_DIR (0700, user $SVC_USER)"
}

if [[ $MANAGED -eq 1 ]]; then
  install_managed
else
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
fi

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
