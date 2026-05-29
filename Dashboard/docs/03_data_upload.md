# Data Upload & Ingestion

Three pathways exist for getting measurement data into the system.
All three write to the same `CanonicalReading` table with full provenance tracking.

---

## Pathway 1 — CSV Upload (portal)

**Best for:** manual uploads, offline sensors, scraped data imported as CSV.

### Prerequisites
1. The sensor must be **registered** at `/portal/sensors/register/`.
   The `Serial Number` in the CSV must exactly match the registered serial.
2. Log in as RESEARCHER, MAINTAINER, or ADMIN.
3. Go to `/portal/upload-csv/`.

### Accepted formats

#### Format A — Particles Plus full export
Timestamp format: `MM/DD/YYYY HH:MM:SS` (UTC).
A second "units" row containing `UTC` is auto-skipped.

Required columns (bold):

| Column | Notes |
|---|---|
| **Timestamp** | `MM/DD/YYYY HH:MM:SS`, UTC |
| **Serial Number** | Must match a registered sensor |
| PM1.0 | µg/m³ |
| PM2.5 | µg/m³ |
| PM4.0 | µg/m³ |
| PM10 | µg/m³ |
| PM0.5 NC … PM10 NC | #/cm³ particle counts |
| CO2 | ppm |
| CO | ppm |
| SO2 | µg/m³ |
| O3 | µg/m³ |
| NO2 | µg/m³ |
| CH2O | µg/m³ (formaldehyde) |
| VOC tVOC measurement | mg/m³ |
| Barometric Pressure | inHg |
| Temperature | °C |
| Relative Humidity | % |

Columns matching `"Applied … Calibration"`, `"… AQI"`, `"… Sensor Status"`,
`"System Status"`, `"Is Indoor"`, `"Is Public"`, `"Device ID"`, `"Friendly Name"`,
`"Latitude"`, `"Longitude"` are **ignored**.

#### Format B — Telemetry (low-bandwidth export)
Auto-detected when header has lowercase `timestamp` AND `PM 1.0`.

| Column | Notes |
|---|---|
| **timestamp** | ISO 8601 UTC, e.g. `2026-03-01T00:00:26Z` |
| **device_serial** | Must match a registered sensor |
| PM 1.0 / PM 2.5 / PM 4.0 / PM 10 | µg/m³ |
| NC 0.5 / NC 1.0 / NC 2.5 / NC 4.0 / NC 10 | #/cm³ |
| Temperature / Relative Humidity | °C / % |

### Duplicate and conflict handling

| Scenario | Action |
|---|---|
| Same sensor + pollutant + timestamp, **same value** | Silent skip; counted in "Duplicates" |
| Same sensor + pollutant + timestamp, **different value** | Existing row kept; new row stored with `is_duplicate=True` + `flag_reason` |
| Unregistered serial number | Row skipped; serial listed in `skipped_serials` |
| Missing timestamp | Row skipped |

### Auto-assigned quality flags

| Pollutant | GOOD range | Outside range |
|---|---|---|
| PM2.5 | 0–500 µg/m³ | < 0 → BAD; > 500 → SUSPECT |
| PM1, PM4, PM10 | ≥ 0 | < 0 → BAD |
| Temperature | −10 to 55 °C | SUSPECT |
| Relative Humidity | 0–100 % | SUSPECT |
| CO2 | 300–5 000 ppm | SUSPECT |
| Missing value | — | MISSING |

---

## Pathway 2 — REST API (live sensor / scraper)

**Best for:** sensors with internet connectivity, or scrapers pushing data programmatically.

### Authentication
Add `X-API-Key: <key>` header. Create keys at `/portal/api-keys/`.

### Single reading — one timestamp

```http
POST /api/v1/readings/
X-API-Key: <your-key>
Content-Type: application/json

{
  "serial_number": "81432434001",
  "timestamp": "2026-05-29T08:00:00Z",
  "readings": [
    {"pollutant": "PM25", "value": 45.2, "unit": "µg/m³"},
    {"pollutant": "PM10", "value": 62.1, "unit": "µg/m³"},
    {"pollutant": "TEMP", "value": 18.5, "unit": "°C"},
    {"pollutant": "RH",   "value": 72.0, "unit": "%"}
  ]
}
```

`serial_number` can be omitted when the API key is bound to a specific sensor.

Response `201 Created`:
```json
{"saved": 4, "duplicates": 0, "errors": 0, "status": "SUCCESS"}
```

### Batch upload — multiple timestamps (offline catch-up / scrapers)

```http
POST /api/v1/readings/batch/
X-API-Key: <your-key>
Content-Type: application/json

{
  "serial_number": "81432434001",
  "readings": [
    {
      "timestamp": "2026-05-29T08:00:00Z",
      "measurements": [
        {"pollutant": "PM25", "value": 45.2, "unit": "µg/m³"},
        {"pollutant": "TEMP", "value": 18.5, "unit": "°C"}
      ]
    },
    {
      "timestamp": "2026-05-29T08:01:00Z",
      "measurements": [
        {"pollutant": "PM25", "value": 47.1, "unit": "µg/m³"},
        {"pollutant": "TEMP", "value": 18.6, "unit": "°C"}
      ]
    }
  ]
}
```

Limits: up to **10 000 snapshots** per request. Split larger catch-ups into chunks.

### Valid pollutant codes

| Code | Measurement | Unit |
|---|---|---|
| PM1 / PM25 / PM4 / PM10 | Mass concentrations | µg/m³ |
| NC05 / NC1 / NC25 / NC4 / NC10 | Particle number counts | #/cm³ |
| CO2 | Carbon dioxide | ppm |
| CO | Carbon monoxide | ppm |
| SO2 | Sulfur dioxide | µg/m³ |
| O3 | Ozone | µg/m³ |
| NO2 | Nitrogen dioxide | µg/m³ |
| CH2O | Formaldehyde | µg/m³ |
| TVOC | Total VOC | mg/m³ |
| TEMP | Temperature | °C |
| RH | Relative humidity | % |
| BARO | Barometric pressure | inHg |

---

## Pathway 3 — Web scraping (upcoming)

Scrapers can use either pathway above:

- **CSV**: scrape → save as CSV → upload via portal or automate the multipart POST.
- **Batch API**: scrape → POST to `/api/v1/readings/batch/` with a SENSOR key.

For a fully automated scraper on a separate server, the batch API is recommended:
1. Register a sensor for the scraped source.
2. Create a SENSOR API key for it.
3. Scrape and buffer readings locally.
4. POST batches of ≤ 500 snapshots every few minutes.

---

## Writing a custom converter

Subclass `BaseDataConverter` in [apps/ingestion/base.py](../apps/ingestion/base.py):

```python
from apps.ingestion.base import BaseDataConverter

class MyDeviceConverter(BaseDataConverter):
    source_name = "MyDevice CSV"
    source_type = "CSV_UPLOAD"

    def validate_source(self, source): ...
    def parse_metadata(self, source): ...
    def normalize_timestamps(self, df): ...   # must produce df["original_ts"] (tz-aware)
    def map_columns(self, df): ...            # returns list[dict] in canonical schema
    def assign_quality_flags(self, records): ...
```

The base class handles DB write, duplicate check, and `IngestionLog` creation.

---

## Aggregation

After a large upload, refresh chart data by running aggregation.

**Portal**: `/portal/aggregate/` → sensor + optional start date → Run.

**API** (MAINTAINER/ADMIN key):
```http
POST /api/v1/aggregate/
X-API-Key: <maintainer-key>
Content-Type: application/json

{"sensor_id": 1, "since": "2026-01-01"}
```

**Scheduled**: configure a Celery periodic task in Django admin → Periodic Tasks.

---

## Testing with the sensor emulator

```bash
pip install requests

# Live mode — sends one reading per second:
python tools/sensor_emulator.py \
  --url http://localhost:8000 \
  --key your-api-key \
  --serial 81432434001 \
  --interval 1

# Batch mode — push 24 h of historical data at once:
python tools/sensor_emulator.py \
  --url http://localhost:8000 \
  --key your-api-key \
  --serial 81432434001 \
  --batch \
  --count 1440 \
  --start "2026-05-28T00:00:00Z"
```

---

## Raw Parquet archive (optional)

Set `RAW_DATA_DIR` in `.env` to enable file-based raw storage alongside the database:

```
RAW_DATA_DIR=/data/raw/nepal_aq
```

Files are written to `RAW_DATA_DIR/sensor_{serial}/{YYYY}/{YYYY-MM-DD}.parquet`.
Trigger from the aggregation portal page or programmatically:

```python
from apps.ingestion.tasks import write_parquet
write_parquet(sensor_id=1)          # all dates
write_parquet(sensor_id=1, date_str="2026-05-29")  # single day
```

Requires `pyarrow` (already in `requirements.txt`).
