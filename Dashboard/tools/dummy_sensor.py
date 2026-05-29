#!/usr/bin/env python3
"""
dummy_sensor.py — live test sensor for the Nepal AQ Dashboard.

Emulates a Particles Plus sensor sending minute-by-minute measurements
to the live REST API, so you can watch data arrive on the dashboard in
real time without physical hardware.

Quick start
───────────
  # 1. One-time setup (server-side, creates sensor + API key):
  cd Dashboard
  python manage.py setup_test_sensor --serial TESTSENSOR001 --name "Test Outdoor"

  # 2. Run this emulator with the printed key:
  python tools/dummy_sensor.py \\
      --url http://localhost:8000 \\
      --key <your-key> \\
      --serial TESTSENSOR001 \\
      --interval 10

  # Indoor sensor (also emits CO₂, TVOC, CH₂O):
  python tools/dummy_sensor.py \\
      --url http://localhost:8000 \\
      --key <your-key> \\
      --serial TESTINDOOR001 \\
      --indoor \\
      --interval 10

  # Load 24h of historical data all at once (batch mode):
  python tools/dummy_sensor.py \\
      --url http://localhost:8000 \\
      --key <your-key> \\
      --serial TESTSENSOR001 \\
      --backfill --hours 24

Requirements: pip install requests
"""
from __future__ import annotations

import argparse
import json
import math
import random
import signal
import sys
import time
from datetime import datetime, timedelta, timezone as dt_tz

# ── ANSI colour helpers (disabled automatically when not a TTY) ───────────────
_USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def green(t):  return _c("32", t)
def red(t):    return _c("31", t)
def yellow(t): return _c("33", t)
def cyan(t):   return _c("36", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)


# ── Realistic sensor data generators ─────────────────────────────────────────

def _diurnal(hour: float, base: float, morning_amp: float, evening_amp: float,
             morning_peak: float = 8.0, evening_peak: float = 19.0,
             width: float = 4.0) -> float:
    """Gaussian double-peak diurnal profile."""
    m = morning_amp * math.exp(-((hour - morning_peak) ** 2) / (2 * width))
    e = evening_amp  * math.exp(-((hour - evening_peak) ** 2) / (2 * width))
    return base + m + e


def _outdoor_snapshot(ts: datetime) -> list[dict]:
    """Realistic outdoor pollutant values with Kathmandu-like diurnal pattern."""
    h = ts.hour + ts.minute / 60.0
    pm25 = max(0.0, _diurnal(h, 30, 40, 30, width=3) + random.gauss(0, 5))
    pm10 = pm25 * random.uniform(1.3, 1.7)
    pm1  = pm25 * random.uniform(0.55, 0.75)
    pm4  = pm25 * random.uniform(1.0, 1.15)
    temp = 18.0 + 10 * math.sin((h - 5) * math.pi / 12) + random.gauss(0, 0.5)
    rh   = 72.0 - 20 * math.sin((h - 5) * math.pi / 12) + random.gauss(0, 2)
    return [
        {"pollutant": "PM25", "value": round(pm25, 2),              "unit": "µg/m³"},
        {"pollutant": "PM10", "value": round(pm10, 2),              "unit": "µg/m³"},
        {"pollutant": "PM1",  "value": round(pm1,  2),              "unit": "µg/m³"},
        {"pollutant": "PM4",  "value": round(pm4,  2),              "unit": "µg/m³"},
        {"pollutant": "TEMP", "value": round(temp, 1),              "unit": "°C"},
        {"pollutant": "RH",   "value": round(max(0, min(100, rh)), 1), "unit": "%"},
        {"pollutant": "BARO", "value": round(29.5 + random.gauss(0, 0.05), 2), "unit": "inHg"},
    ]


def _indoor_snapshot(ts: datetime) -> list[dict]:
    """Indoor profile — higher CO₂, lower PM (sheltered), cooking peaks."""
    h = ts.hour + ts.minute / 60.0
    # Indoor PM often lower than outdoor but spikes at cooking hours
    pm25 = max(0.0, _diurnal(h, 18, 50, 40, morning_peak=7.5, evening_peak=18.5, width=1.5)
               + random.gauss(0, 4))
    pm10 = pm25 * random.uniform(1.1, 1.4)
    pm1  = pm25 * random.uniform(0.6, 0.8)
    temp = 22.0 + 5 * math.sin((h - 6) * math.pi / 12) + random.gauss(0, 0.3)
    rh   = 60.0 - 10 * math.sin((h - 6) * math.pi / 12) + random.gauss(0, 1.5)
    # CO₂ rises during occupied hours
    co2  = 420 + 500 * math.exp(-((h - 13) ** 2) / 18) + random.gauss(0, 15)
    tvoc = max(0.0, 0.15 + 0.6 * math.exp(-((h - 19) ** 2) / 4) + random.gauss(0, 0.05))
    ch2o = max(0.0, 0.02 + 0.08 * math.exp(-((h - 19) ** 2) / 6) + random.gauss(0, 0.01))
    return [
        {"pollutant": "PM25", "value": round(pm25, 2),                 "unit": "µg/m³"},
        {"pollutant": "PM10", "value": round(pm10, 2),                 "unit": "µg/m³"},
        {"pollutant": "PM1",  "value": round(pm1,  2),                 "unit": "µg/m³"},
        {"pollutant": "TEMP", "value": round(temp, 1),                 "unit": "°C"},
        {"pollutant": "RH",   "value": round(max(0, min(100, rh)), 1), "unit": "%"},
        {"pollutant": "CO2",  "value": round(max(350, co2), 1),        "unit": "ppm"},
        {"pollutant": "TVOC", "value": round(tvoc, 3),                 "unit": "mg/m³"},
        {"pollutant": "CH2O", "value": round(ch2o, 3),                 "unit": "µg/m³"},
    ]


def generate_snapshot(ts: datetime, indoor: bool) -> list[dict]:
    return _indoor_snapshot(ts) if indoor else _outdoor_snapshot(ts)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _headers(key: str) -> dict:
    return {
        "X-API-Key":     key,
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


def _fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def post_single(base_url: str, key: str, serial: str, ts: datetime,
                indoor: bool, session) -> tuple[int, dict]:
    """POST /api/v1/readings/ — single timestamp. Returns (status_code, body)."""
    payload = {
        "serial_number": serial,
        "timestamp":     _fmt_ts(ts),
        "readings":      generate_snapshot(ts, indoor),
    }
    resp = session.post(
        f"{base_url}/api/v1/readings/",
        headers=_headers(key),
        data=json.dumps(payload),
        timeout=15,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:200]}
    return resp.status_code, body


def post_batch(base_url: str, key: str, serial: str,
               snapshots: list[dict], session) -> tuple[int, dict]:
    """POST /api/v1/readings/batch/ — multiple timestamps."""
    payload = {"serial_number": serial, "readings": snapshots}
    resp = session.post(
        f"{base_url}/api/v1/readings/batch/",
        headers=_headers(key),
        data=json.dumps(payload),
        timeout=120,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:200]}
    return resp.status_code, body


# ── Terminal display helpers ──────────────────────────────────────────────────

def _reading_summary(measurements: list[dict]) -> str:
    """Compact one-line summary of pollutant values."""
    order = ["PM25", "PM10", "PM1", "TEMP", "RH", "CO2", "TVOC", "CH2O"]
    names = {"PM25": "PM2.5", "PM10": "PM10", "PM1": "PM1",
             "TEMP": "Temp", "RH": "RH", "CO2": "CO2",
             "TVOC": "TVOC", "CH2O": "CH2O"}
    units = {"PM25": "µg", "PM10": "µg", "PM1": "µg",
             "TEMP": "°C", "RH": "%", "CO2": "ppm",
             "TVOC": "mg", "CH2O": "µg"}
    lookup = {m["pollutant"]: m["value"] for m in measurements}
    parts = []
    for p in order:
        if p in lookup:
            parts.append(f"{names[p]}={lookup[p]}{units[p]}")
    return "  ".join(parts)


def _print_header(serial: str, url: str, indoor: bool, interval: int) -> None:
    sensor_type = "Indoor" if indoor else "Outdoor"
    line = "━" * 64
    print(bold(line))
    print(bold(f"  Nepal AQ Dummy Sensor"))
    print(f"  Serial   : {cyan(serial)}   [{sensor_type}]")
    print(f"  Server   : {url}")
    print(f"  Interval : {interval}s    Press {yellow('Ctrl+C')} to stop")
    print(bold(line))


# ── Live streaming mode ───────────────────────────────────────────────────────

def run_live(args) -> None:
    import requests

    _print_header(args.serial, args.url, args.indoor, args.interval)

    session = requests.Session()
    total_sent = total_saved = total_dup = total_err = 0
    consecutive_failures = 0

    def _graceful_exit(sig, frame):
        print(f"\n\n  Stopped.  total_sent={total_sent}  saved={total_saved}"
              f"  dup={total_dup}  err={total_err}")
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_exit)

    while True:
        ts = datetime.now(dt_tz.utc)
        ts_str = ts.strftime("%H:%M:%S")
        measurements = generate_snapshot(ts, args.indoor)

        try:
            code, body = post_single(
                args.url, args.key, args.serial, ts, args.indoor, session,
            )
            total_sent += 1

            if code == 201:
                consecutive_failures = 0
                saved = body.get("saved", 0)
                dups  = body.get("duplicates", 0)
                errs  = body.get("errors", 0)
                total_saved += saved
                total_dup   += dups
                total_err   += errs

                summary = _reading_summary(measurements)
                status  = green("✓") if (saved > 0 or dups > 0) else yellow("·")
                stats   = dim(f"[sent={total_sent} saved={total_saved} dup={total_dup} err={total_err}]")
                print(f"{dim(ts_str)}  {status}  {summary}  {stats}")

            elif code == 404:
                consecutive_failures += 1
                print(f"{dim(ts_str)}  {red('✗')}  {red('Sensor not registered.')}  "
                      f"Run: python manage.py setup_test_sensor --serial {args.serial}")
                if consecutive_failures >= 3:
                    print(red("  Stopping — sensor not found on server."))
                    sys.exit(1)

            elif code == 403:
                print(f"{dim(ts_str)}  {red('✗')}  {red('API key rejected (403).')}  "
                      "Check the key has SENSOR role and is active.")
                sys.exit(1)

            else:
                consecutive_failures += 1
                total_err += 1
                print(f"{dim(ts_str)}  {yellow('!')}  HTTP {code}  {body}")

        except Exception as exc:
            consecutive_failures += 1
            total_err += 1
            print(f"{dim(ts_str)}  {red('✗')}  {red(str(exc)[:80])}")
            if consecutive_failures >= 5:
                print(yellow(f"  {consecutive_failures} consecutive failures — "
                             f"retrying in {args.interval}s"))

        if args.max_count and total_sent >= args.max_count:
            print(f"\n  Reached --max-count {args.max_count}. Done.")
            break

        time.sleep(args.interval)


# ── Backfill (batch) mode ─────────────────────────────────────────────────────

def run_backfill(args) -> None:
    import requests

    interval_s = args.interval
    hours = args.hours
    count = int(hours * 3600 / interval_s)
    start_ts = datetime.now(dt_tz.utc) - timedelta(hours=hours)
    chunk = args.chunk_size

    print(bold("━" * 64))
    print(bold("  Nepal AQ Dummy Sensor — Backfill mode"))
    print(f"  Serial   : {cyan(args.serial)}")
    print(f"  Range    : {start_ts.strftime('%Y-%m-%d %H:%M')} UTC → now")
    print(f"  Readings : {count:,}  ({hours}h × {interval_s}s interval)")
    print(f"  Chunks   : {chunk} readings/request")
    print(bold("━" * 64))

    session = requests.Session()
    snapshots = []
    ts = start_ts
    total_pushed = 0
    chunk_num = 0

    for i in range(count):
        measurements = generate_snapshot(ts, args.indoor)
        snapshots.append({
            "timestamp":    _fmt_ts(ts),
            "measurements": measurements,
        })
        ts += timedelta(seconds=interval_s)

        if len(snapshots) >= chunk:
            chunk_num += 1
            _flush_batch(args, session, snapshots, chunk_num)
            total_pushed += len(snapshots)
            snapshots = []
            pct = round(total_pushed / count * 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"  [{bar}] {pct}%  {total_pushed:,}/{count:,}", end="\r")

    if snapshots:
        chunk_num += 1
        _flush_batch(args, session, snapshots, chunk_num)
        total_pushed += len(snapshots)

    print(f"\n\n  Backfill complete — {total_pushed:,} readings pushed in {chunk_num} chunks.")
    print(f"  Aggregation is running in the background on the server.")


def _flush_batch(args, session, snapshots: list, chunk_num: int) -> None:
    try:
        code, body = post_batch(
            args.url, args.key, args.serial, snapshots, session,
        )
        icon = green("✓") if code in (200, 201) else red("✗")
        saved = body.get("saved", "?")
        dups  = body.get("duplicates", "?")
        print(f"\n  Chunk {chunk_num:3d}  {icon}  HTTP {code}  "
              f"saved={saved}  dup={dups}  ({len(snapshots)} snapshots)")
    except Exception as exc:
        print(f"\n  Chunk {chunk_num:3d}  {red('✗')}  ERROR: {exc}")


# ── Connectivity check ────────────────────────────────────────────────────────

def check_server(url: str, key: str) -> bool:
    """Quick health check — GET /api/v1/sensors/ with the API key."""
    try:
        import requests
        resp = requests.get(
            f"{url}/api/v1/sensors/",
            headers=_headers(key),
            timeout=8,
        )
        if resp.status_code == 200:
            print(green(f"  ✓ Server reachable at {url}"))
            return True
        print(yellow(f"  ! Server replied {resp.status_code} — continuing anyway"))
        return True
    except Exception as exc:
        print(red(f"  ✗ Cannot reach server: {exc}"))
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Emulate a sensor sending live data to the Nepal AQ dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Live mode — send every 10 seconds:
  python tools/dummy_sensor.py --url http://localhost:8000 --key <key> --serial TESTSENSOR001 --interval 10

  # Indoor sensor with CO2/TVOC:
  python tools/dummy_sensor.py --url http://localhost:8000 --key <key> --serial TESTINDOOR01 --indoor --interval 10

  # Backfill 12 hours of historical data:
  python tools/dummy_sensor.py --url http://localhost:8000 --key <key> --serial TESTSENSOR001 --backfill --hours 12
        """,
    )

    p.add_argument("--url",      required=True,
                   help="Dashboard base URL, e.g. http://localhost:8000")
    p.add_argument("--key",      required=True,
                   help="SENSOR-role API key (from setup_test_sensor)")
    p.add_argument("--serial",   required=True,
                   help="Sensor serial number (must already be registered)")
    p.add_argument("--indoor",   action="store_true",
                   help="Emit CO₂, TVOC, CH₂O (indoor sensor profile)")
    p.add_argument("--interval", type=int, default=10,
                   help="Seconds between readings in live mode (default: 10)")

    # Live mode options
    p.add_argument("--max-count", dest="max_count", type=int, default=0,
                   help="Stop after N readings in live mode (0 = run forever)")

    # Backfill options
    p.add_argument("--backfill", action="store_true",
                   help="Push historical data instead of live streaming")
    p.add_argument("--hours",    type=float, default=24.0,
                   help="Hours of historical data to backfill (default: 24)")
    p.add_argument("--chunk-size", dest="chunk_size", type=int, default=500,
                   help="Readings per batch request in backfill mode (default: 500)")

    return p


def main() -> None:
    try:
        import requests  # noqa: F401
    except ImportError:
        print(red("ERROR: 'requests' package not installed."))
        print("       Run: pip install requests")
        sys.exit(1)

    args = build_parser().parse_args()
    args.url = args.url.rstrip("/")

    print()
    if not check_server(args.url, args.key):
        sys.exit(1)
    print()

    if args.backfill:
        run_backfill(args)
    else:
        run_live(args)


if __name__ == "__main__":
    main()
