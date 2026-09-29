#!/usr/bin/env bash
# Open a Stagewatch dashboard full screen once the server is up.
# Usage: stagewatch-kiosk [URL]   (default http://localhost:8080/d/wall)
URL="${1:-http://localhost:8080/d/wall}"

for _ in $(seq 1 120); do
  curl -fs -o /dev/null "${URL%%/d/*}/api/info" && break
  sleep 1
done

BROWSER="$(command -v chromium-browser || command -v chromium)"
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run \
  --autoplay-policy=no-user-gesture-required --check-for-update-interval=31536000 \
  "$URL"
