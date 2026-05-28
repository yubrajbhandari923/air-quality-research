# Sensor Registration & Site Management

## Concepts

- **Site** — a physical monitoring location (e.g. "Belauri Village"). Holds coordinates, district, land-use type. Multiple sensors can share a site.
- **Sensor** — a specific physical device at a site. Has a serial number, model, indoor/outdoor flag, and operational status.

One site can have an indoor+outdoor pair (enabling I/O ratio analysis). The dashboard auto-detects paired sensors at the same site.

---

## Adding a site

### Via Django admin (simplest)

1. Go to `/django-admin/sensors/site/add/`
2. Fill in: **Name** (unique), **District**, **Latitude**, **Longitude** (decimal degrees, WGS 84)
3. Optional: Municipality, Province, Elevation, Population estimate, Land use type
4. Save

### Via API (automated deployment)

```bash
# POST is not available for sites — use admin or the registration script below
python manage.py shell -c "
from apps.sensors.models import Site
Site.objects.create(
    name='Belauri Village',
    district='Kailali',
    municipality='Belauri',
    province='Sudurpashchim',
    latitude=28.6844,
    longitude=80.3646,
    elevation_m=185,
    land_use_type='RESIDENTIAL',
)
"
```

---

## Adding a sensor

### Via Django admin

1. Go to `/django-admin/sensors/sensor/add/`
2. Fill in:
   - **Serial number** — e.g. `81432434001` (must match the CSV export from the device)
   - **Friendly name** — e.g. `Belauri Outdoor (Bluesky)`
   - **Model** — e.g. `8143` (Bluesky) or `8144` (Air Assure)
   - **Site** — select from the dropdown
   - **Is indoor** — check for indoor sensors
   - **Status** — ACTIVE/OFFLINE/MAINTENANCE/DECOMMISSIONED
   - **Installed at** — deployment date

### Via API (sensor self-registration)

Sensors can register themselves on first boot using their API key:

```bash
curl -X POST http://localhost:8000/api/v1/sensors/register/ \
  -H "X-API-Key: <sensor-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "serial_number": "81432434001",
    "friendly_name": "Belauri Outdoor",
    "model": "8143",
    "manufacturer": "Sensirion / Airgradient",
    "site": 1,
    "is_indoor": false,
    "power_type": "MAINS",
    "connectivity_type": "WIFI"
  }'
```

---

## Creating an API key for a sensor

1. Django admin → API → API Keys → Add
2. Set **Role** = `SENSOR`, **Name** = sensor identifier
3. Copy the key — it is shown once
4. Paste into the sensor firmware's config

Alternatively, from the portal: `/portal/api-keys/`.

---

## Sensor operational states

| Status | Meaning |
|--------|---------|
| ACTIVE | Normal operation, readings expected |
| OFFLINE | Not transmitting — investigate |
| MAINTENANCE | Intentionally offline for service |
| DECOMMISSIONED | Permanently retired |

When a sensor goes offline, update its status in admin. The dashboard homepage shows the active/offline count and the last data timestamp.

---

## Maintenance logs

Record physical interventions (cleaning, relocation, filter change) in Django admin → Sensors → Maintenance Logs. These appear on the sensor detail page and help explain gaps or step changes in data.

---

## Belauri sensor pair (reference)

| Sensor | Serial | Model | Type |
|--------|--------|-------|------|
| Bluesky Outdoor | `81432434001` | 8143 | Outdoor |
| Air Assure Indoor | `81442406076` | 8144 | Indoor |

Both deployed at: **28.6844°N, 80.3646°E** — Belauri Village, Kailali district.
Overlap period (both running): November 24 2025 – April 1 2026.
