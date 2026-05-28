# Data Download & REST API

## Access tiers

| Tier | Who | Date range | Max rows | Format |
|------|-----|-----------|----------|--------|
| Public | Unauthenticated or PUBLIC role | Last 7 days | 500 | CSV, JSON |
| Researcher | RESEARCHER role | Any | 50,000 | CSV, JSON |
| Admin/Maintainer | ADMIN or MAINTAINER role | Any | 500,000 | CSV, JSON |

Admins can adjust these limits in **Django admin → Analysis → Download Configuration**.

---

## Download builder (web UI)

Go to `/data/` and use the **Download Data** form:

1. Select a sensor from the dropdown
2. Choose a pollutant (PM₂.₅, PM₁₀, CO₂, Temperature, etc.)
3. Choose quality filter (GOOD / UNVALIDATED / All)
4. Set a date range (locked to last 7 days for public users)
5. Choose format: CSV or JSON
6. Click **Generate & Download**

---

## REST API

All API responses are JSON unless `format=csv` is requested.

### List sensors (public)

```
GET /api/v1/sensors/
GET /api/v1/sensors/?status=ACTIVE&indoor=false
```

### Sensor detail

```
GET /api/v1/sensors/{id}/
```

Returns metadata + last 7-day PM₂.₅ stats.

### Export readings (CSV / JSON)

```
GET /api/v1/export/?sensor=1&pollutant=PM25&quality=GOOD&format=csv
GET /api/v1/export/?sensor=1&pollutant=PM25&start=2025-12-01&end=2026-01-01&format=json
```

Query parameters:

| Param | Required | Description |
|-------|----------|-------------|
| `sensor` | Yes | Sensor ID (integer) |
| `pollutant` | No | PM25, PM10, PM1, CO2, TVOC, TEMP, RH (default: PM25) |
| `quality` | No | GOOD, UNVALIDATED, ALL (default: GOOD) |
| `start` | No | ISO 8601 datetime — researcher+ only |
| `end` | No | ISO 8601 datetime |
| `format` | No | csv or json (default: csv) |

### Chart data (public — no auth needed)

```
GET /api/v1/charts/sensor/{id}/timeseries/?pollutant=PM25&start=2025-12-01
GET /api/v1/charts/sensor/{id}/completeness/
GET /api/v1/charts/sensor/{id}/diurnal/?pollutant=PM25&start=2025-12-01&end=2026-04-01
GET /api/v1/charts/sensor/{id}/monthly/?pollutant=PM25
GET /api/v1/charts/site/{site_id}/io-comparison/?start=2025-11-24&end=2026-04-01
GET /api/v1/charts/national/summary/
```

---

## Authentication

### Session auth (browser)

Sign in at `/users/login/`. Subsequent requests carry the session cookie.

### API key auth (programmatic)

```bash
curl -H "X-API-Key: your-64-char-key" \
  "http://localhost:8000/api/v1/export/?sensor=1&format=csv"
```

Obtain an API key from `/portal/api-keys/` (RESEARCHER+ required).

---

## Python example

```python
import requests

BASE = "http://localhost:8000"
HEADERS = {"X-API-Key": "your-key-here"}

# Get sensor list
sensors = requests.get(f"{BASE}/api/v1/sensors/", headers=HEADERS).json()

# Download PM2.5 CSV for sensor 1
resp = requests.get(
    f"{BASE}/api/v1/export/",
    params={"sensor": 1, "pollutant": "PM25", "quality": "GOOD",
            "start": "2025-12-01", "end": "2026-04-01", "format": "csv"},
    headers=HEADERS,
)
with open("pm25_outdoor.csv", "wb") as f:
    f.write(resp.content)
```

---

## CSV field reference

| Field | Type | Description |
|-------|------|-------------|
| `timestamp_utc` | datetime | UTC timestamp (ISO 8601) |
| `sensor_id` | int | Database sensor ID |
| `sensor_name` | str | Human-readable sensor name |
| `site` | str | Site name |
| `is_indoor` | bool | True = indoor deployment |
| `pollutant` | str | Pollutant code (PM25, CO2, etc.) |
| `unit` | str | Measurement unit |
| `raw_value` | float | Raw sensor value — never overwritten |
| `cleaned_value` | float | QC-corrected value (null if not yet cleaned) |
| `quality_flag` | str | GOOD / UNVALIDATED / SUSPECT / BAD |

---

## Citation

When using this data in publications, please cite:

```
Nepal Air Observatory (2026). Air quality sensor data from Belauri, Nepal.
Open dataset. CC BY 4.0. https://[your-domain]/data/
```
