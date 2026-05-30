#!/usr/bin/env bash
# Entrypoint for sensor emulator containers.
#
# Environment variables (set in docker-compose):
#   SENSOR_TYPE   outdoor | indoor
#   INTERVAL      seconds between readings (default: 10)
#   BATCH_ON_START  if "1", push 24h of historical data first, then go live
set -euo pipefail

SENSOR_TYPE="${SENSOR_TYPE:-outdoor}"
INTERVAL="${INTERVAL:-10}"
BATCH_ON_START="${BATCH_ON_START:-0}"
KEY_FILE="/shared/dev_keys.env"

# ── Wait for the key file written by the web entrypoint ───────────────────────
echo "[emulator:${SENSOR_TYPE}] Waiting for dev_keys.env..."
until [ -f "$KEY_FILE" ]; do sleep 2; done

# shellcheck source=/dev/null
source "$KEY_FILE"

if [ "$SENSOR_TYPE" = "indoor" ]; then
  API_KEY="$INDOOR_API_KEY"
  SERIAL="$INDOOR_SERIAL"
else
  API_KEY="$OUTDOOR_API_KEY"
  SERIAL="$OUTDOOR_SERIAL"
fi

echo "[emulator:${SENSOR_TYPE}] Serial: ${SERIAL}  Key prefix: ${API_KEY:0:12}..."

# ── Wait for the web server ────────────────────────────────────────────────────
echo "[emulator:${SENSOR_TYPE}] Waiting for Django at http://web:8000..."
until python - <<PY 2>/dev/null
import urllib.request, sys
try:
    urllib.request.urlopen("http://web:8000/api/v1/sensors/", timeout=3)
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
do
  sleep 3
done
echo "[emulator:${SENSOR_TYPE}] Server ready."

# ── Optional: push 24h of historical data via batch endpoint ──────────────────
if [ "$BATCH_ON_START" = "1" ]; then
  echo "[emulator:${SENSOR_TYPE}] Pushing 24h of backfill data..."
  python /app/tools/sensor_emulator.py \
    --url http://web:8000 \
    --key "$API_KEY" \
    --serial "$SERIAL" \
    --batch \
    --count 1440 \
    --interval 60
  echo "[emulator:${SENSOR_TYPE}] Backfill complete. Switching to live mode."
fi

# ── Live streaming mode ────────────────────────────────────────────────────────
echo "[emulator:${SENSOR_TYPE}] Starting live mode (interval=${INTERVAL}s)..."
exec python /app/tools/sensor_emulator.py \
  --url http://web:8000 \
  --key "$API_KEY" \
  --serial "$SERIAL" \
  --interval "$INTERVAL"
