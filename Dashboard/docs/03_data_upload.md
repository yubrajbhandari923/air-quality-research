# Data Upload & Ingestion Pipeline

## Ingestion pathways

| Pathway | When to use | How |
|---------|-------------|-----|
| CSV upload (portal) | Historical batch data from Sensirion CSV export | Upload via `/portal/upload-csv/` |
| Live API POST | Real-time sensor push | Sensor firmware → `POST /api/v1/readings/` |
| Management command | Bulk re-ingestion or testing | `python manage.py run_ingestion` |
| OpenAQ converter | External reference data | `apps/ingestion/converters/openaq_converter.py` |

---

## CSV upload workflow (most common)

### 1. Prepare your CSV

The CSV must match the Sensirion/Airgradient export format:

```
Timestamp,Serial Number,Friendly Name,Is Indoor,PM2.5,PM10,PM1,CO2,Temperature,Relative Humidity,...
Units,,,,,µg/m³,µg/m³,µg/m³,ppm,°C,%,...
2025-11-24 00:01:00+05:45,81432434001,Belauri Outdoor,FALSE,42.3,55.1,...
```

Key requirements:
- Row 1: column headers (exactly matching expected names)
- Row 2: units row — **skip this row** (the converter does it automatically via `skiprows=[1]`)
- Timestamps must be timezone-aware (ISO 8601 with offset, or UTC)
- `Serial Number` must match a registered sensor in the DB

### 2. Upload via portal

1. Sign in as RESEARCHER or ADMIN
2. Go to `/portal/upload-csv/`
3. Select your CSV file and the target sensor
4. Click Upload — the converter runs synchronously for small files, or queues a Celery task for large ones
5. Check `/portal/ingestion-logs/` for results

### 3. What the converter does

The `CSVConverter` in `apps/ingestion/converters/csv_converter.py`:

1. **Validate source** — checks file exists, sensor registered, column presence
2. **Parse metadata** — extracts serial number, timestamps, interval
3. **Normalize timestamps** — converts all times to UTC (preserving the original tz-aware value in `original_ts`)
4. **Map columns** → `CanonicalReading` fields:
   - `PM2.5` → pollutant=`PM25`, unit=`µg/m³`
   - `CO2` → pollutant=`CO2`, unit=`ppm`
   - `Temperature` → pollutant=`TEMP`, unit=`°C`
   - etc.
5. **Assign quality flags** — `UNVALIDATED` by default; spikes flagged `SUSPECT`
6. **Duplicate detection** — uses `(sensor, pollutant, original_ts)` as the unique key; duplicates set `is_duplicate=True` and kept for audit
7. **Write to DB** — bulk inserts via `bulk_create(ignore_conflicts=True)`
8. **Log result** — creates `IngestionLog` entry whether success or failure

### 4. Writing a custom converter

Subclass `BaseDataConverter` in `apps/ingestion/base.py`:

```python
from apps.ingestion.base import BaseDataConverter

class MyDeviceConverter(BaseDataConverter):
    source_name = "MyDevice CSV"

    def validate_source(self):
        # raise ValueError if source is invalid
        ...

    def parse_metadata(self):
        # return dict: {serial_number, site_id, interval_seconds}
        ...

    def normalize_timestamps(self, df):
        # return df with 'ts' column as UTC-aware datetime
        ...

    def map_columns(self, df):
        # return list of dicts with keys matching CanonicalReading fields
        ...

    def assign_quality_flags(self, records):
        # return list of dicts, each with 'quality_flag' set
        ...
```

The base class handles DB write, duplicate check, and ingestion log automatically.

---

## Live API submission (sensor firmware)

Sensors push readings via:

```bash
POST /api/v1/readings/
X-API-Key: <sensor-api-key>
Content-Type: application/json

[
  {
    "serial_number": "81432434001",
    "timestamp": "2026-01-15T14:32:00Z",
    "readings": [
      {"pollutant": "PM25", "value": 38.5},
      {"pollutant": "PM10", "value": 52.1},
      {"pollutant": "TEMP", "value": 18.3},
      {"pollutant": "RH",   "value": 67.0}
    ]
  }
]
```

Valid pollutant codes: `PM1`, `PM25`, `PM4`, `PM10`, `CO2`, `TVOC`, `TEMP`, `RH`, `BARO`, `NC05`, `NC1`, `NC25`.

---

## Quality flags

Quality flags are set during ingestion and can be updated via admin:

| Flag | Meaning | When set |
|------|---------|----------|
| GOOD | Validated reading | After manual or automated QC |
| UNVALIDATED | Not yet checked | Default for all new readings |
| SUSPECT | Potentially problematic | Spike detection, out-of-range |
| BAD | Known bad reading | Manual flag, sensor malfunction |
| MISSING | Expected but absent | Gap-fill placeholder |

To re-flag readings in bulk:

```python
from apps.readings.models import CanonicalReading
# Flag all PM2.5 > 500 as SUSPECT (likely sensor error)
CanonicalReading.objects.filter(
    pollutant='PM25', raw_value__gt=500
).update(quality_flag='SUSPECT', flag_reason='PM2.5 > 500 µg/m³ — likely sensor error')
```

---

## Ingestion logs

Every upload attempt creates an `IngestionLog` entry visible at `/portal/ingestion-logs/`. It records:
- Records attempted, saved, duplicated, errored
- Error details if any records failed
- Who triggered the ingestion

Never delete ingestion logs — they are the audit trail.

---

## DailyAggregate population

After uploading historical data, populate the pre-computed daily aggregate table for faster dashboard queries:

```bash
python manage.py shell -c "
from apps.readings.tasks import compute_daily_aggregates
compute_daily_aggregates()
"
```

Or run the Celery task if the worker is running:
```bash
python manage.py shell -c "
from apps.readings.tasks import compute_daily_aggregates
compute_daily_aggregates.delay()
"
```
