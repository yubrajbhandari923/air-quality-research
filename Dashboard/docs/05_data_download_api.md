# Data Download & REST API

## Permission tiers

The API has four privilege levels. Every endpoint is annotated with the level it requires.

| Tier | How to authenticate | Capabilities |
|------|---------------------|--------------|
| **Public** | No auth | Read sensors, sites, chart data |
| **Sensor** | `X-API-Key` with role `SENSOR` or `ADMIN` | POST readings (live & batch) |
| **Researcher** | `X-API-Key` with role `RESEARCHER` or `ADMIN`, or session login with researcher+ role | Query readings, export data |
| **Admin** | `X-API-Key` with role `ADMIN`, or session login with admin role | Create / update / delete sensors and sites; trigger aggregation |

Obtain API keys from `/portal/api-keys/` (researcher+ required). ADMIN keys can only be issued by a Django admin.

---

## Authentication

### Session auth (browser)

Sign in at `/users/login/`. Subsequent requests carry the session cookie automatically.

### API key auth (programmatic)

Pass the key in the `X-API-Key` request header:

```bash
curl -H "X-API-Key: <your-64-char-key>" \
     "https://your-domain/api/v1/export/?sensors=1&format=csv"
```

---

## Access tiers for data export

| Tier | Who | Date range | Max rows | Format |
|------|-----|-----------|----------|--------|
| Public | Unauthenticated or PUBLIC role | Last 7 days | 500 | CSV, JSON |
| Researcher | RESEARCHER role | Any | 50,000 | CSV, JSON |
| Admin/Maintainer | ADMIN or MAINTAINER role | Any | 500,000 | CSV, JSON |

Admins can adjust these limits in **Django admin → Analysis → Download Configuration**.

---

## Endpoints

### Public — no auth required

#### List sensors

```
GET /api/v1/sensors/
GET /api/v1/sensors/?status=ACTIVE
GET /api/v1/sensors/?site=3
```

Returns all registered sensors with site metadata.

#### Sensor detail

```
GET /api/v1/sensors/{id}/
```

Returns sensor metadata + last 7-day PM₂.₅ stats (`avg`, `min_val`, `max_val`, `count`).

#### List monitoring sites / locations

```
GET /api/v1/sites/
GET /api/v1/sites/{id}/
```

Returns all monitoring locations.

#### Chart data

```
GET /api/v1/charts/sensor/{id}/timeseries/?pollutant=PM25&window=7d
GET /api/v1/charts/sensor/{id}/timeseries/?pollutant=PM25&start=2025-12-01&end=2026-04-01
GET /api/v1/charts/sensor/{id}/completeness/
GET /api/v1/charts/sensor/{id}/diurnal/?pollutant=PM25
GET /api/v1/charts/sensor/{id}/monthly/?pollutant=PM25
GET /api/v1/charts/sensor/{id}/date-range/?pollutant=PM25
GET /api/v1/charts/site/{site_id}/io-comparison/?pollutant=PM25
GET /api/v1/charts/national/summary/
```

Resolution is chosen automatically from the requested span:
- ≤ 2 days → raw minute data (max 2,000 points)
- ≤ 60 days → `HourlyAggregate`
- > 60 days → `DailyAggregate`

---

### Sensor — `SENSOR` or `ADMIN` API key

#### Submit live reading (single timestamp)

```
POST /api/v1/readings/
X-API-Key: <sensor-key>
Content-Type: application/json

{
  "serial_number": "81432434001",
  "timestamp": "2026-05-29T08:00:00Z",
  "readings": [
    {"pollutant": "PM25", "value": 45.2, "unit": "µg/m³"},
    {"pollutant": "TEMP", "value": 18.5, "unit": "°C"}
  ]
}
```

`serial_number` can be omitted when the key is bound to a specific sensor.

#### Submit batch readings (offline catch-up)

```
POST /api/v1/readings/batch/
X-API-Key: <sensor-key>
Content-Type: application/json

{
  "serial_number": "81432434001",
  "readings": [
    {
      "timestamp": "2026-05-29T08:00:00Z",
      "measurements": [{"pollutant": "PM25", "value": 45.2, "unit": "µg/m³"}]
    },
    {
      "timestamp": "2026-05-29T08:01:00Z",
      "measurements": [{"pollutant": "PM25", "value": 47.1, "unit": "µg/m³"}]
    }
  ]
}
```

Maximum 10,000 snapshots per request. Duplicate timestamps are handled gracefully (skipped with a count in the response).

#### Self-register a sensor

```
POST /api/v1/sensors/register/
X-API-Key: <sensor-key>
Content-Type: application/json

{
  "serial_number": "81432434001",
  "friendly_name": "Belauri Outdoor",
  "model": "8143",
  "site": 1,
  "is_indoor": false,
  "power_type": "SOLAR",
  "connectivity_type": "WIFI"
}
```

Returns 201 if created, 200 if already registered.

---

### Researcher — `RESEARCHER` or `ADMIN` API key, or session login

#### Query readings

```
GET /api/v1/readings/?sensor=1&pollutant=PM25&start=2026-01-01&end=2026-04-01&quality=GOOD
```

Query parameters:

| Param | Required | Description |
|-------|----------|-------------|
| `sensor` | No | Filter by sensor ID |
| `pollutant` | No | PM25, PM10, PM1, CO2, TVOC, TEMP, RH, … |
| `start` | No | ISO 8601 datetime |
| `end` | No | ISO 8601 datetime |
| `quality` | No | GOOD, SUSPECT, BAD, UNVALIDATED (default: all) |
| `indoor` | No | `true` or `false` |
| `page` | No | Page number (default: 1) |
| `page_size` | No | Rows per page, max 10,000 (default: 100) |

#### Export readings (CSV / JSON)

```
GET /api/v1/export/?sensors=1,2&pollutants=PM25,PM10&data_type=raw&output=csv
GET /api/v1/export/?sensors=1&data_type=hourly&start=2026-01-01&end=2026-04-01&output=json
```

Export parameters:

| Param | Required | Description |
|-------|----------|-------------|
| `sensors` | Yes | Comma-separated sensor IDs, e.g. `1,2,3` |
| `pollutants` | No | Comma-separated codes (default: all) |
| `data_type` | No | `raw` (default) / `hourly` / `daily` |
| `start` | No | ISO 8601 datetime — researcher+ only |
| `end` | No | ISO 8601 datetime |
| `quality` | No | GOOD (default) / ALL / SUSPECT / BAD |
| `output` | No | `csv` (default) / `json` |
| `columns` | No | Comma-separated column names to include |

---

### Admin — `ADMIN` API key or admin session only

These endpoints create, update, and delete sensors and monitoring sites. They require the highest privilege tier and are intended for programmatic provisioning.

#### Create a monitoring site

```
POST /api/v1/sites/
X-API-Key: <admin-key>
Content-Type: application/json

{
  "name": "Belauri",
  "district": "Dadeldhura",
  "municipality": "Ajaymeru",
  "province": "Sudurpashchim",
  "latitude": 29.4931,
  "longitude": 80.5741,
  "elevation_m": 1250,
  "population_estimate": 4800,
  "land_use_type": "RESIDENTIAL",
  "description": "Village site near primary school."
}
```

`name` must be unique. Returns the created site as JSON with `201 Created`.

#### Update a site

```
PUT   /api/v1/sites/{id}/     — full replacement
PATCH /api/v1/sites/{id}/     — partial update (only provided fields change)
```

Same body shape as POST; PATCH accepts a subset of fields.

#### Delete a site

```
DELETE /api/v1/sites/{id}/
```

Returns `204 No Content`. **Blocked with `409 Conflict` if any sensors are linked** — decommission or relocate all sensors first.

#### Create a sensor

```
POST /api/v1/sensors/
X-API-Key: <admin-key>
Content-Type: application/json

{
  "serial_number": "81432434001",
  "friendly_name": "Belauri Outdoor",
  "model": "8143",
  "manufacturer": "Particles Plus",
  "site": 1,
  "is_indoor": false,
  "power_type": "SOLAR",
  "connectivity_type": "WIFI",
  "status": "ACTIVE",
  "installed_at": "2025-11-01T00:00:00Z"
}
```

`serial_number` must be unique. Returns `201 Created`.

#### Update a sensor

```
PUT   /api/v1/sensors/{id}/
PATCH /api/v1/sensors/{id}/
```

Useful for changing site assignment, power type, connectivity, status, notes.

#### Decommission / delete a sensor

```
DELETE /api/v1/sensors/{id}/
```

**Default (soft):** sets `status=DECOMMISSIONED` and records `decommissioned_at`, preserving all historical readings. Returns the updated sensor.

**Hard delete** (irreversible — removes sensor record):

```
DELETE /api/v1/sensors/{id}/?hard=true
```

Returns `204 No Content`. Will fail with a DB error if readings exist (Django `PROTECT` constraint on `CanonicalReading.sensor`).

#### Trigger aggregation recompute

```
POST /api/v1/aggregate/
X-API-Key: <admin-key>
Content-Type: application/json

{
  "sensor_id": 1,
  "since": "2026-01-01"
}
```

Both fields are optional. Omit `sensor_id` to recompute all sensors; omit `since` to recompute everything.

---

## Pollutant codes

| Code | Full name | Typical unit |
|------|-----------|-------------|
| `PM1` | PM₁.₀ | µg/m³ |
| `PM25` | PM₂.₅ | µg/m³ |
| `PM4` | PM₄.₀ | µg/m³ |
| `PM10` | PM₁₀ | µg/m³ |
| `CO2` | Carbon dioxide | ppm |
| `CO` | Carbon monoxide | ppm |
| `SO2` | Sulfur dioxide | µg/m³ |
| `O3` | Ozone | µg/m³ |
| `NO2` | Nitrogen dioxide | µg/m³ |
| `CH2O` | Formaldehyde | ppm |
| `TVOC` | Total VOC | ppm |
| `TEMP` | Temperature | °C |
| `RH` | Relative humidity | % |
| `BARO` | Barometric pressure | inHg |
| `NC05` – `NC10` | Number concentrations | #/cm³ |

---

## Quality flags

| Flag | Meaning |
|------|---------|
| `GOOD` | Passes all automated QC checks |
| `UNVALIDATED` | Not yet checked (default for new readings) |
| `SUSPECT` | Passes range check but flagged by anomaly detector |
| `BAD` | Fails physical bounds or is a confirmed outlier |
| `MISSING` | Expected timestamp with no measurement |

Raw values are **never overwritten**. QC corrections are written to `cleaned_value`. The effective value used for charts is `cleaned_value ?? raw_value`.

---

## Sensor status values

| Status | Meaning |
|--------|---------|
| `ACTIVE` | Collecting and transmitting data |
| `OFFLINE` | Not sending data, cause unknown |
| `MAINTENANCE` | Under servicing; data may be unreliable |
| `DECOMMISSIONED` | Retired; historical data preserved |

---

## Python example

```python
import requests

BASE = "https://your-domain"
HEADERS = {"X-API-Key": "your-key-here"}

# List all active sensors
sensors = requests.get(f"{BASE}/api/v1/sensors/", params={"status": "ACTIVE"}).json()

# Create a new monitoring site (admin key required)
site = requests.post(
    f"{BASE}/api/v1/sites/",
    headers=HEADERS,
    json={
        "name": "New Site",
        "district": "Kathmandu",
        "latitude": 27.7172,
        "longitude": 85.3240,
        "land_use_type": "URBAN",
    },
).json()

# Create a sensor at that site (admin key required)
sensor = requests.post(
    f"{BASE}/api/v1/sensors/",
    headers=HEADERS,
    json={
        "serial_number": "99999999001",
        "friendly_name": "KTM Outdoor",
        "model": "8143",
        "site": site["id"],
        "is_indoor": False,
        "power_type": "GRID",
        "connectivity_type": "WIFI",
        "status": "ACTIVE",
    },
).json()

# Download PM2.5 CSV for the new sensor
resp = requests.get(
    f"{BASE}/api/v1/export/",
    params={"sensors": sensor["id"], "pollutants": "PM25", "quality": "GOOD",
            "start": "2026-01-01", "output": "csv"},
    headers=HEADERS,
)
with open("pm25.csv", "wb") as f:
    f.write(resp.content)
```

---

## Common error responses

| HTTP status | Meaning |
|-------------|---------|
| `400 Bad Request` | Missing required field or invalid value in request body |
| `401 Unauthorized` | No API key / session provided |
| `403 Forbidden` | Key provided but insufficient privilege level |
| `404 Not Found` | Sensor or site ID does not exist |
| `409 Conflict` | Cannot delete site because sensors are still linked |
| `422 Unprocessable Entity` | Payload valid JSON but ingestion logic rejected it |

---

## CSV field reference (raw export)

| Field | Type | Description |
|-------|------|-------------|
| `Timestamp` | datetime | Local time (MM/DD/YYYY HH:MM:SS) |
| `Device ID` | str | Sensor serial number |
| `Serial Number` | str | Same as Device ID |
| `Friendly Name` | str | Human-readable label |
| `Latitude` / `Longitude` | float | WGS 84 decimal degrees |
| `Is Indoor` | bool | True = indoor deployment |
| `PM2.5` | float | PM₂.₅ concentration µg/m³ |
| `PM2.5 AQI` | int | US EPA AQI computed from PM₂.₅ |
| `PM10` | float | PM₁₀ concentration µg/m³ |
| `Temperature` | float | °C |
| `Relative Humidity` | float | % |

Full column list matches the native Particles Plus CSV export format.

---

## Citation

When using this data in publications, please cite:

```
Nepal Air Observatory (2026). Air quality sensor data from Belauri, Nepal.
Open dataset. CC BY 4.0. https://[your-domain]/data/
```
