"""
Standalone dummy data generators for testing and demo.

These generate in-memory data (not DB) in the formats expected by each ingestion pathway:
  1. live_api_payload()    — JSON for POST /api/v1/readings/
  2. csv_batch()           — pandas DataFrame in H1/H2 export format
  3. indoor_outdoor_pair() — two DataFrames (indoor + outdoor) for same timestamp range
  4. openaq_format()       — DataFrame mimicking OpenAQ locations CSV
  5. manual_entry()        — list[dict] in canonical reading format

Usage:
    from dummy_data.generators import live_api_payload, csv_batch
    payload = live_api_payload("81432434001")
    df = csv_batch("81432434001", days=7)
"""
import random
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytz

NEPAL_TZ = pytz.timezone("Asia/Kathmandu")


# ── Seasonal PM2.5 generator ──────────────────────────────────────────────────

def _pm25(dt: datetime, is_indoor: bool = False) -> float:
    """Realistic Nepal PM2.5 value for given datetime."""
    month = dt.month
    hour = dt.hour

    if month in (11, 12, 1, 2):
        base = max(5.0, random.gauss(120, 40))
    elif month in (10, 3):
        base = max(5.0, random.gauss(70, 25))
    elif month in (4, 5):
        base = max(5.0, random.gauss(50, 20))
    else:
        base = max(5.0, random.gauss(15, 8))

    # Diurnal cooking peaks
    if 6 <= hour <= 9 or 17 <= hour <= 21:
        base *= 1.4
    elif 0 <= hour <= 5:
        base *= 0.8

    if is_indoor:
        base *= random.uniform(0.55, 0.80) if not (6 <= hour <= 9 or 17 <= hour <= 21) else random.uniform(0.90, 1.20)

    return round(base, 2)


# ── 1. Live API payload ───────────────────────────────────────────────────────

def live_api_payload(serial_number: str, is_indoor: bool = False) -> dict:
    """
    Generate a realistic JSON payload for POST /api/v1/readings/.

    Example:
        payload = live_api_payload("81432434001")
        # → {"serial_number": "81432434001", "timestamp": "2026-...", "readings": [...]}
    """
    now = datetime.now(tz=timezone.utc)
    pm25_val = _pm25(now, is_indoor)

    readings = [
        {"pollutant": "PM25", "value": pm25_val, "unit": "µg/m³"},
        {"pollutant": "PM10", "value": round(pm25_val * random.uniform(1.1, 1.35), 2), "unit": "µg/m³"},
        {"pollutant": "PM1",  "value": round(pm25_val * random.uniform(0.65, 0.75), 2), "unit": "µg/m³"},
        {"pollutant": "TEMP", "value": round(random.gauss(22 if is_indoor else 18, 5), 1), "unit": "°C"},
        {"pollutant": "RH",   "value": round(min(98, max(20, random.gauss(65, 15))), 1), "unit": "%"},
    ]
    if is_indoor:
        readings += [
            {"pollutant": "CO2",  "value": round(max(400, random.gauss(800, 200))), "unit": "ppm"},
            {"pollutant": "TVOC", "value": round(max(0.1, random.gauss(0.8, 0.3)), 3), "unit": "mg/m³"},
            {"pollutant": "BARO", "value": round(random.gauss(29.3, 0.15), 2), "unit": "inHg"},
        ]

    return {
        "serial_number": serial_number,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "readings": readings,
    }


# ── 2. CSV batch (H1/H2 export format) ───────────────────────────────────────

def csv_batch(
    serial_number: str,
    days: int = 7,
    interval_min: int = 15,
    is_indoor: bool = False,
    lat: float = 28.6844,
    lon: float = 80.3646,
) -> pd.DataFrame:
    """
    Generate a DataFrame in the Particles Plus H1/H2 export CSV format.
    Includes units row (row index 1) as in the real files.

    Args:
        serial_number: device serial (e.g. "81432434001")
        days:          number of days of historical data
        interval_min:  minutes between readings

    Returns:
        pd.DataFrame with same column structure as 81432434001-2025-H2.csv
    """
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days)
    timestamps = []
    current = start
    while current <= end:
        timestamps.append(current)
        current += timedelta(minutes=interval_min)

    model = "8144" if is_indoor else "8143"
    rows = []
    for ts in timestamps:
        pm25 = _pm25(ts, is_indoor)
        pm10 = round(pm25 * random.uniform(1.1, 1.35), 2)
        pm1 = round(pm25 * random.uniform(0.65, 0.75), 2)
        pm4 = round(pm25 * random.uniform(1.02, 1.08), 2)
        temp = round(random.gauss(22 if is_indoor else 18, 5), 1)
        rh = round(min(98, max(20, random.gauss(65, 15))), 1)
        baro = round(random.gauss(29.3, 0.15), 2) if is_indoor else ""
        co2 = round(max(400, random.gauss(800, 200))) if is_indoor else ""
        tvoc = round(max(0.1, random.gauss(0.8, 0.3)), 3) if is_indoor else ""

        row = {
            "Timestamp": ts.strftime("%m/%d/%Y %H:%M:%S"),
            "Device ID": f"dummy-{serial_number[-6:]}",
            "Serial Number": serial_number,
            "Model": model,
            "Sub Model": "",
            "Friendly Name": "Dummy Sensor",
            "Latitude": lat if not is_indoor else "",
            "Longitude": lon if not is_indoor else "",
            "Is Indoor": "true" if is_indoor else "false",
            "Is Public": "false",
            "PM2.5 AQI": "",
            "PM10 AQI": "",
            "PM1.0": pm1,
            "PM2.5": pm25,
            "Applied PM2.5 Custom Calibration Setting - Multiplication Factor": "<nil>",
            "Applied PM2.5 Custom Calibration Setting - Offset": "<nil>",
            "PM4.0": pm4,
            "PM10": pm10,
            "Applied PM10 Custom Calibration Setting - Multiplication Factor": "<nil>",
            "Applied PM10 Custom Calibration Setting - Offset": "<nil>",
            "PM0.5 NC": round(pm25 * random.uniform(7, 12)),
            "PM1.0 NC": round(pm25 * random.uniform(5, 9)),
            "PM2.5 NC": round(pm25 * random.uniform(4, 7)),
            "PM4.0 NC": round(pm25 * random.uniform(4, 7)),
            "PM10 NC": round(pm25 * random.uniform(4, 7)),
            "Typical Particle Size": round(random.uniform(0.4, 0.7), 2),
            "PM Sensor Status": 0,
            "CO2": co2,
            "Applied CO2 Custom Calibration Setting - Multiplication Factor": "",
            "Applied CO2 Custom Calibration Setting - Offset": "",
            "CO2 Sensor Status": "",
            "VOC tVOC measurement": tvoc,
            "Applied TVOC Custom Calibration Setting - Multiplication Factor": "",
            "Applied TVOC Custom Calibration Setting - Offset": "",
            "VOC Sensor Status": "",
            "Barometric Pressure": baro,
            "Applied Barometric Pressure Custom Calibration Setting - Offset": "",
            "Barometric Sensor Status": "",
            "Temperature": temp,
            "Applied Temperature Custom Calibration Setting - Offset": "",
            "Relative Humidity": rh,
            "Applied Relative Humidity Custom Calibration Setting - Offset": "",
            "Temperature/Humidity Sensor Status": 0,
            "System Status": 0,
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    # Insert units row at position 0 (after header)
    units_row = {col: "UTC" if col == "Timestamp" else "" for col in df.columns}
    units_row.update({
        "PM1.0": "ug/m3", "PM2.5": "ug/m3", "PM4.0": "ug/m3", "PM10": "ug/m3",
        "PM0.5 NC": "#/cm3", "PM1.0 NC": "#/cm3", "PM2.5 NC": "#/cm3",
        "CO2": "ppm", "Barometric Pressure": "inHg",
        "Temperature": "Celsius", "Relative Humidity": "%",
    })
    df = pd.concat([pd.DataFrame([units_row]), df], ignore_index=True)
    return df


# ── 3. Indoor/Outdoor pair ────────────────────────────────────────────────────

def indoor_outdoor_pair(
    outdoor_serial: str = "81432434001",
    indoor_serial: str = "81442406076",
    days: int = 7,
    interval_min: int = 15,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Generate matched indoor/outdoor DataFrames for the same time window.

    Returns:
        (outdoor_df, indoor_df) in H1/H2 export format
    """
    outdoor_df = csv_batch(outdoor_serial, days=days, interval_min=interval_min, is_indoor=False)
    indoor_df  = csv_batch(indoor_serial,  days=days, interval_min=interval_min, is_indoor=True)
    return outdoor_df, indoor_df


# ── 4. OpenAQ format ──────────────────────────────────────────────────────────

def openaq_format(n_locations: int = 5) -> pd.DataFrame:
    """
    Generate a DataFrame mimicking the OpenAQ Nepal locations CSV.

    Columns match: /Data/open-aq/openaq_nepal_locations.csv
    """
    locations = [
        (3459, "Embassy Kathmandu",     27.7387, 85.3362),
        (3460, "Phora Durbar",          27.7124, 85.3157),
        (9999, "Pokhara Airport",       28.1997, 83.9823),
        (9998, "Chitwan AQS",           27.5291, 84.3542),
        (9997, "Butwal Reference",      27.7005, 83.4536),
    ][:n_locations]

    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    for loc_id, name, lat, lon in locations:
        sensors_json = (
            f"[{{'id': {loc_id*2}, 'name': 'pm25 µg/m³', "
            f"'parameter': {{'id': 2, 'name': 'pm25', 'units': 'µg/m³', 'displayName': 'PM2.5'}}}}]"
        )
        rows.append({
            "location_id": loc_id,
            "name": name,
            "locality": "N/A",
            "country": "NP",
            "latitude": lat,
            "longitude": lon,
            "timezone": "Asia/Kathmandu",
            "datetime_first": "2020-01-01T00:00:00Z",
            "datetime_last": now_iso,
            "is_monitor": True,
            "provider": "DummyProvider",
            "owner": "Dummy Owner",
            "sensors": sensors_json,
        })
    return pd.DataFrame(rows)


# ── 5. Manual entry format ────────────────────────────────────────────────────

def manual_entry(
    sensor_id: int,
    site_id: int,
    is_indoor: bool = False,
    n_readings: int = 24,
) -> list[dict]:
    """
    Generate canonical reading dicts for manual entry (no file/API involved).

    Returns:
        list[dict] compatible with BaseDataConverter.save_to_canonical_schema()
    """
    now = datetime.now(NEPAL_TZ)
    records = []
    for i in range(n_readings):
        ts = now - timedelta(hours=i)
        pm25 = _pm25(ts, is_indoor)
        for pollutant, value, unit in [
            ("PM25", pm25, "µg/m³"),
            ("PM10", round(pm25 * 1.2, 2), "µg/m³"),
            ("TEMP", round(random.gauss(22, 5), 1), "°C"),
            ("RH",   round(min(98, max(20, random.gauss(65, 15))), 1), "%"),
        ]:
            records.append({
                "original_ts": ts,
                "timezone": "Asia/Kathmandu",
                "interval_seconds": 3600,
                "pollutant": pollutant,
                "unit": unit,
                "raw_value": value,
                "cleaned_value": None,
                "sensor_id": sensor_id,
                "site_id": site_id,
                "is_indoor": is_indoor,
                "source_type": "MANUAL",
                "quality_flag": "UNVALIDATED",
                "flag_reason": "Manually entered dummy data.",
            })
    return records
