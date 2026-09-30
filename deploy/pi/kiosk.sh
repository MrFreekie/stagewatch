#!/usr/bin/env bash
# Open a Stagewatch dashboard full screen once the server is up.
# Usage: stagewatch-kiosk [URL]   (default http://localhost:8080/d/wall)
URL="${1:-http://localhost:8080/d/wall}"

# Keep the display awake (X11/LXDE; on Wayland/labwc set Screen Blanking to Off in raspi-config).
if [[ -n "${DISPLAY:-}" ]] && command -v xset >/dev/null 2>&1; then
  xset s off; xset s noblank; xset -dpms
fi

for _ in $(seq 1 120); do
  curl -fs -o /dev/null "${URL%%/d/*}/api/info" && break
  sleep 1
done

BROWSER="$(command -v chromium-browser || command -v chromium || true)"
if [[ -z "$BROWSER" ]]; then
  echo "stagewatch-kiosk: no chromium-browser/chromium found (sudo apt install chromium)" >&2
  exit 1
fi
# --disable-session-crashed-bubble: after a power cut Chromium would otherwise show a
# "restore pages?" bubble over the dashboard.
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run \
  --disable-session-crashed-bubble --autoplay-policy=no-user-gesture-required \
  --check-for-update-interval=31536000 "$URL"
