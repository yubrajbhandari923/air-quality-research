#!/usr/bin/env python3
"""
Fetch one day of OpenAQ v3 hourly data for selected Nepal locations and output
a Dashboard-compatible Format B CSV.

The OpenAQ v3 measurements endpoint doesn't support date range filtering, so we
estimate which result page contains the target date from the sensor's start date,
then fetch that page window and filter locally.

Usage:
    OPENAQ_API_KEY=your_key python fetch_oneday.py

Output:
    openaq_nepal_oneday_2026-05-18.csv   (Format B, ready for Dashboard CSV upload)

Before uploading to the Dashboard:
    Register each location_id as a sensor serial number at /portal/sensors/register/
    using the exact location_id string as the Serial Number field.
"""

import ast
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests

API_KEY = os.getenv("OPENAQ_API_KEY")
BASE_URL = "https://api.openaq.org/v3"
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

TARGET_DATE = "2026-05-18"
TARGET_DT = datetime(2026, 5, 18, tzinfo=timezone.utc)

HOURS_PER_PAGE = 1000  # API limit

# Diverse sample: Kathmandu valley + Terai + hills
# All are 2025-vintage AirGradient sensors — data fits within ~5 pages of /hours
SELECTED_IDS = [
    5506835,  # Gaushala Chowk SC-01 (GD Labs, KTM)
    5509788,  # Lagankhel SC-05 (GD Labs, KTM)
    5509787,  # Baluwatar SC-02 (GD Labs, KTM)
    6143672,  # Biratnagar ward 9 (CEN, Terai east)
    6138104,  # Janakpurdham (CEN, Terai)
    6135972,  # Birgunj City Office (CEN, Terai)
    5709621,  # Hetauda (CEN, mid-hills)
    6097220,  # Pokhara Ward 7 (CEN, hills)
    6092366,  # Dhangadhi (CEN, Terai far-west)
    6095642,  # Birendranagar Municipality (CEN, mid-west)
]

# OpenAQ parameter name → Format B column header
PARAM_TO_COL = {
    "pm1": "PM 1.0",
    "pm25": "PM 2.5",
    "pm10": "PM 10",
    "temperature": "Temperature",
    "relativehumidity": "Relative Humidity",
}


def fetch_hours_page(sensor_id: int, page: int, limit: int = HOURS_PER_PAGE) -> list:
    url = f"{BASE_URL}/sensors/{sensor_id}/hours"
    r = requests.get(url, headers=HEADERS, params={"limit": limit, "page": page}, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def estimate_start_page(sensor_start: datetime) -> int:
    """Estimate which page of hourly data contains TARGET_DT."""
    hours_from_start = max(0, (TARGET_DT - sensor_start).total_seconds() / 3600)
    # Sensors aren't always 100% online; use 0.85 as a coverage estimate
    effective_hours = hours_from_start * 0.85
    return max(1, int(effective_hours / HOURS_PER_PAGE))


def filter_to_target_day(results: list) -> list:
    """Keep only results where datetimeFrom falls on TARGET_DATE (UTC)."""
    out = []
    for r in results:
        ts = r.get("period", {}).get("datetimeFrom", {}).get("utc", "")
        if ts.startswith(TARGET_DATE):
            out.append(r)
    return out


def fetch_sensor_oneday(sensor_id: int, sensor_start: datetime) -> list[dict]:
    """
    Returns list of {ts, value} dicts for the target date.
    Searches a 3-page window around the estimated page.
    """
    start_page = estimate_start_page(sensor_start)
    pages_to_try = list(dict.fromkeys([start_page, start_page + 1, max(1, start_page - 1)]))

    for page in pages_to_try:
        results = fetch_hours_page(sensor_id, page)
        if not results:
            continue
        day_results = filter_to_target_day(results)
        if day_results:
            return [{"ts": r["period"]["datetimeFrom"]["utc"], "value": r.get("value")} for r in day_results]
        time.sleep(0.2)

    return []


def main():
    locs_df = pd.read_csv("openaq_nepal_locations.csv")
    locs_df = locs_df[locs_df["location_id"].isin(SELECTED_IDS)].copy()

    print(f"Fetching hourly data for {TARGET_DATE} across {len(locs_df)} locations...\n")

    all_rows: list[dict] = []

    for _, loc in locs_df.iterrows():
        loc_id = int(loc["location_id"])
        loc_name = loc["name"]

        # Parse sensor start date from location's datetime_first
        try:
            loc_start = datetime.fromisoformat(loc["datetime_first"].replace("Z", "+00:00"))
        except Exception:
            loc_start = datetime(2025, 1, 1, tzinfo=timezone.utc)

        try:
            sensors = ast.literal_eval(loc["sensors"]) if isinstance(loc["sensors"], str) else []
        except (ValueError, SyntaxError):
            sensors = []

        print(f"  {loc_name} (serial={loc_id})")

        # pivot: timestamp -> {col: value}
        pivot: dict[str, dict] = {}

        for sensor in sensors:
            param = sensor.get("parameter", {}).get("name")
            if param not in PARAM_TO_COL:
                continue
            sensor_id = sensor["id"]
            col = PARAM_TO_COL[param]

            try:
                day_data = fetch_sensor_oneday(sensor_id, loc_start)
                print(f"    {param:20s}: {len(day_data):3d} hourly readings")
                for entry in day_data:
                    pivot.setdefault(entry["ts"], {})[col] = entry["value"]
                time.sleep(0.2)
            except requests.HTTPError as e:
                print(f"    {param}: HTTP {e.response.status_code}")
            except Exception as e:
                print(f"    {param}: {e}")

        for ts, vals in sorted(pivot.items()):
            row = {"timestamp": ts, "device_serial": str(loc_id)}
            row.update(vals)
            all_rows.append(row)

    if not all_rows:
        print("\nNo data fetched — check OPENAQ_API_KEY and that sensors were active on target date.")
        return

    df = pd.DataFrame(all_rows)

    # Enforce Format B column order; add missing columns as empty
    format_b_cols = [
        "timestamp", "device_serial",
        "PM 1.0", "PM 2.5", "PM 10",
        "Temperature", "Relative Humidity",
    ]
    for c in format_b_cols:
        if c not in df.columns:
            df[c] = None
    df = df[format_b_cols].sort_values(["device_serial", "timestamp"])

    out_file = f"openaq_nepal_oneday_{TARGET_DATE}.csv"
    df.to_csv(out_file, index=False)

    print(f"\n{'='*60}")
    print(f"Saved {len(df)} rows → {out_file}")
    print(f"Unique locations: {df['device_serial'].nunique()}")
    print(f"Rows per location: {len(df) // df['device_serial'].nunique()} avg")

    print("\nRegister these serial numbers at /portal/sensors/register/ before uploading:")
    for s in sorted(df["device_serial"].unique(), key=int):
        name = locs_df[locs_df["location_id"] == int(s)]["name"].values
        label = name[0] if len(name) else "unknown"
        print(f"  Serial: {s:>10s}  |  {label}")

    print(f"\nThen upload {out_file} at /portal/upload-csv/")


if __name__ == "__main__":
    main()
