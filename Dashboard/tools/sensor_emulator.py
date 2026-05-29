#!/usr/bin/env python3
"""
sensor_emulator.py — test tool for the Nepal AQ REST API.

Emulates a Particles Plus sensor sending minute-by-minute measurements.
Useful for testing the live API endpoint and batch upload endpoint.

Usage examples
──────────────
# Stream readings every 60 seconds (live mode):
    python sensor_emulator.py \\
        --url http://localhost:8000 \\
        --key your-api-key-here \\
        --serial 81432434001 \\
        --interval 60

# Push 1 440 readings at once (24 h of catch-up data):
    python sensor_emulator.py \\
        --url http://localhost:8000 \\
        --key your-api-key-here \\
        --serial 81432434001 \\
        --batch \\
        --count 1440 \\
        --start "2026-05-28T00:00:00Z"

# Override interval so readings arrive faster for debugging:
    python sensor_emulator.py \\
        --url http://localhost:8000 \\
        --key your-api-key-here \\
        --serial 81432434001 \\
        --interval 2

Requirements: requests  (pip install requests)
"""
import argparse
import json
import math
import random
import sys
import time
from datetime import datetime, timedelta, timezone as dt_tz


# ── Synthetic measurement generators ─────────────────────────────────────────

def _diurnal_pm25(hour: int, noise: float = 5.0) -> float:
    """Realistic PM2.5 diurnal profile (µg/m³) — morning and evening peaks."""
    base = 25.0
    morning_peak = 30 * math.exp(-((hour - 8) ** 2) / 8)
    evening_peak = 25 * math.exp(-((hour - 19) ** 2) / 8)
    return max(0.0, base + morning_peak + evening_peak + random.gauss(0, noise))


def generate_snapshot(ts: datetime) -> list[dict]:
    """Return a realistic list of sensor measurements for a given timestamp."""
    hour = ts.hour
    pm25 = _diurnal_pm25(hour)
    pm10 = pm25 * random.uniform(1.3, 1.6)
    pm1  = pm25 * random.uniform(0.6, 0.8)
    pm4  = pm25 * random.uniform(1.0, 1.2)
    temp = 18.0 + 8 * math.sin((hour - 6) * math.pi / 12) + random.gauss(0, 0.5)
    rh   = 65.0 - 15 * math.sin((hour - 6) * math.pi / 12) + random.gauss(0, 2)
    co2  = 420.0 + 80 * math.sin((hour - 7) * math.pi / 10) + random.gauss(0, 10)

    return [
        {"pollutant": "PM25", "value": round(pm25, 2), "unit": "µg/m³"},
        {"pollutant": "PM10", "value": round(pm10, 2), "unit": "µg/m³"},
        {"pollutant": "PM1",  "value": round(pm1,  2), "unit": "µg/m³"},
        {"pollutant": "PM4",  "value": round(pm4,  2), "unit": "µg/m³"},
        {"pollutant": "TEMP", "value": round(temp, 1), "unit": "°C"},
        {"pollutant": "RH",   "value": round(max(0, min(100, rh)), 1), "unit": "%"},
        {"pollutant": "CO2",  "value": round(co2, 1), "unit": "ppm"},
    ]


# ── API helpers ───────────────────────────────────────────────────────────────

def _headers(api_key: str) -> dict:
    return {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def post_single(base_url: str, api_key: str, serial: str, ts: datetime) -> dict:
    """POST /api/v1/readings/ — single timestamp."""
    import requests

    payload = {
        "serial_number": serial,
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "readings": generate_snapshot(ts),
    }
    resp = requests.post(
        f"{base_url}/api/v1/readings/",
        headers=_headers(api_key),
        data=json.dumps(payload),
        timeout=10,
    )
    return resp


def post_batch(base_url: str, api_key: str, serial: str, snapshots: list[dict]) -> dict:
    """POST /api/v1/readings/batch/ — multiple timestamps."""
    import requests

    payload = {"serial_number": serial, "readings": snapshots}
    resp = requests.post(
        f"{base_url}/api/v1/readings/batch/",
        headers=_headers(api_key),
        data=json.dumps(payload),
        timeout=60,
    )
    return resp


# ── Modes ─────────────────────────────────────────────────────────────────────

def run_live(args):
    """Stream single readings at the configured interval."""
    print(f"[emulator] Live mode — serial={args.serial}, interval={args.interval}s")
    print(f"[emulator] Endpoint: {args.url}/api/v1/readings/")
    print(f"[emulator] Press Ctrl+C to stop.\n")

    count = 0
    while True:
        ts = datetime.now(dt_tz.utc)
        try:
            resp = post_single(args.url, args.key, args.serial, ts)
            status_icon = "✓" if resp.status_code == 201 else "✗"
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            print(f"[{ts.strftime('%H:%M:%S')}] {status_icon} HTTP {resp.status_code} — {body}")
        except Exception as exc:
            print(f"[{ts.strftime('%H:%M:%S')}] ERROR — {exc}")

        count += 1
        if args.max_count and count >= args.max_count:
            print(f"\n[emulator] Sent {count} readings — stopping.")
            break
        time.sleep(args.interval)


def run_batch(args):
    """Generate N readings starting from --start and push as a batch."""
    start = datetime.fromisoformat(args.start.rstrip("Z")).replace(tzinfo=dt_tz.utc)
    interval = timedelta(seconds=args.interval)

    print(f"[emulator] Batch mode — serial={args.serial}, count={args.count}")
    print(f"[emulator] From {start.isoformat()} with {args.interval}s interval")
    print(f"[emulator] Endpoint: {args.url}/api/v1/readings/batch/\n")

    snapshots = []
    ts = start
    for i in range(args.count):
        snapshots.append({
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "measurements": generate_snapshot(ts),
        })
        ts += interval

        if len(snapshots) >= args.chunk_size:
            _flush_batch(args, snapshots)
            snapshots = []

    if snapshots:
        _flush_batch(args, snapshots)

    print(f"\n[emulator] Done. Pushed {args.count} snapshots.")


def _flush_batch(args, snapshots):
    try:
        resp = post_batch(args.url, args.key, args.serial, snapshots)
        icon = "✓" if resp.status_code in (200, 201) else "✗"
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
        print(f"  {icon} Pushed {len(snapshots)} snapshots — HTTP {resp.status_code}: {body}")
    except Exception as exc:
        print(f"  ✗ ERROR pushing {len(snapshots)} snapshots: {exc}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Emulate a Particles Plus sensor sending data to the Nepal AQ API."
    )
    parser.add_argument("--url",      required=True,                 help="Base server URL, e.g. http://localhost:8000")
    parser.add_argument("--key",      required=True,                 help="API key (SENSOR role)")
    parser.add_argument("--serial",   required=True,                 help="Sensor serial number (must be registered)")
    parser.add_argument("--interval", type=int,   default=60,        help="Seconds between readings (default: 60)")
    parser.add_argument("--batch",    action="store_true",           help="Use batch endpoint instead of live single-reading")
    parser.add_argument("--count",    type=int,   default=60,        help="Number of readings to send in batch mode (default: 60)")
    parser.add_argument("--start",    default="",                    help="Batch start timestamp ISO 8601 UTC, e.g. 2026-05-01T00:00:00Z (default: 24h ago)")
    parser.add_argument("--chunk-size", dest="chunk_size", type=int, default=500, help="Max snapshots per batch request (default: 500)")
    parser.add_argument("--max-count", dest="max_count",  type=int, default=0,   help="Stop live mode after N readings (0 = run forever)")

    args = parser.parse_args()

    try:
        import requests
    except ImportError:
        print("ERROR: 'requests' package required. Install with: pip install requests")
        sys.exit(1)

    if not args.start:
        args.start = (datetime.now(dt_tz.utc) - timedelta(hours=args.count * args.interval / 3600)).strftime("%Y-%m-%dT%H:%M:%SZ")

    if args.batch:
        run_batch(args)
    else:
        run_live(args)


if __name__ == "__main__":
    main()
