# Nepal Air Quality Dashboard

A research and public monitoring platform for Nepal air quality — built with Django + Wagtail, Chart.js, and Leaflet.

---

## Table of Contents

- [Nepal Air Quality Dashboard](#nepal-air-quality-dashboard)
  - [Table of Contents](#table-of-contents)
  - [1. Local development (fast path with uv)](#1-local-development-fast-path-with-uv)
  - [2. Local development with Docker Compose](#2-local-development-with-docker-compose)
    - [Useful Docker commands](#useful-docker-commands)
    - [Makefile shortcuts](#makefile-shortcuts)
  - [3. Loading real data](#3-loading-real-data)
  - [4. Deploy to Render (free)](#4-deploy-to-render-free)
    - [Prerequisites](#prerequisites)
    - [One-command deploy](#one-command-deploy)
    - [Manual deploy via Render dashboard (no script)](#manual-deploy-via-render-dashboard-no-script)
    - [After first deploy — seed data](#after-first-deploy--seed-data)
    - [Environment variables set automatically by render.yaml](#environment-variables-set-automatically-by-renderyaml)
  - [5. User accounts and roles](#5-user-accounts-and-roles)
  - [6. Key URLs](#6-key-urls)
  - [7. Adding a new data source](#7-adding-a-new-data-source)

---

## 1. Local development (fast path with uv)

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).  
Uses SQLite — no PostgreSQL or Docker needed.

```bash
# From the repo root
cd Dashboard

# Install all dependencies into an isolated virtualenv
uv sync

# Create the database and tables
uv run python manage.py migrate --settings=nepal_aq.settings.development

# Populate with 30 days of realistic dummy data (693K readings, ~30 seconds)
uv run python manage.py load_dummy_data --settings=nepal_aq.settings.development

# Start the dev server
uv run python manage.py runserver --settings=nepal_aq.settings.development
```

Open **http://localhost:8000/dashboard/** in your browser.

> **Note:** Every `manage.py` command needs `--settings=nepal_aq.settings.development`
> unless you export the variable once:
> ```bash
> export DJANGO_SETTINGS_MODULE=nepal_aq.settings.development
> ```

---

## 2. Local development with Docker Compose

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).  
Runs PostgreSQL + Redis + Celery — closest to the production environment.

```bash
cd Dashboard

# Copy the example env file (edit if needed)
cp .env.example .env

# Build images and start all services in the background
docker compose up -d --build

# Watch logs (Ctrl-C to stop watching, services keep running)
docker compose logs -f web
```

Wait for the web container to print `Watching for file changes` then visit **http://localhost:8000/dashboard/**.

### Useful Docker commands

```bash
# Run a management command inside the container
docker compose exec web python manage.py load_dummy_data

# Open a Django shell
docker compose exec web python manage.py shell

# Ingest real Belauri CSV files (mounted read-only at /data)
docker compose exec web python manage.py run_ingestion --source belauri_csv

# Stop everything (data is preserved in Docker volumes)
docker compose down

# Stop and delete all data
docker compose down -v
```

### Makefile shortcuts

```bash
make up            # docker compose up -d --build
make down          # stop all services
make shell         # Django shell inside web container
make load-dummy    # load 30 days of dummy data
make ingest-belauri  # ingest real Belauri CSVs
make lint          # flake8
```

---

## 3. Loading real data

The Belauri CSV files (outdoor sensor `81432434001` and indoor `81442406076`) live at
`../Data/Belauri/` relative to this directory.

```bash
# Without Docker
uv run python manage.py run_ingestion --source belauri_csv

# With Docker (data directory is mounted read-only at /data inside the container)
docker compose exec web python manage.py run_ingestion --source belauri_csv
```

Ingestion is idempotent — re-running marks duplicates instead of creating doubles.
Every run is logged to the `IngestionLog` table (visible in Django admin → Readings → Ingestion logs).

---

## 4. Deploy to Render (free)

[Render](https://render.com) offers a free tier that fits this project well:
free PostgreSQL (90-day expiry on free plan, $7/mo to keep), free Redis, and free web service.
**No credit card required to start.**

### Prerequisites

- A GitHub account with this repository pushed to it
- A [Render account](https://dashboard.render.com/register) (sign in with GitHub)

### One-command deploy

```bash
cd Dashboard
bash deploy.sh
```

The script will:
1. Check that the repo is pushed to GitHub
2. Ask for your Render API key (get one from **Render dashboard → Account → API Keys**)
3. Create all services defined in `render.yaml` (web, worker, PostgreSQL, Redis)
4. Print the live URL

### Manual deploy via Render dashboard (no script)

1. Push this repo to GitHub (the `Dashboard/` folder must be at the repo root, or adjust `rootDir`).
2. Go to **https://dashboard.render.com** → **New** → **Blueprint**.
3. Connect your GitHub repo.
4. Render reads `render.yaml` and creates all services automatically.
5. Wait ~3 minutes for the first deploy.
6. Visit the URL printed in the Render dashboard.

### After first deploy — seed data

```bash
# Open a shell on the Render web service
# Render dashboard → your web service → Shell tab
python manage.py load_dummy_data
```

Or set the `SEED_DUMMY_DATA=true` environment variable in the Render dashboard and
add `python manage.py load_dummy_data` to the build command in `render.yaml`.

### Environment variables set automatically by render.yaml

| Variable | Value |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `nepal_aq.settings.production` |
| `DJANGO_SECRET_KEY` | auto-generated secure random string |
| `DATABASE_URL` | linked to the free PostgreSQL instance |
| `REDIS_URL` / `CELERY_BROKER_URL` | linked to the free Redis instance |
| `DJANGO_ALLOWED_HOSTS` | `*.onrender.com` |

To add your own domain, add it to `DJANGO_ALLOWED_HOSTS` in the Render dashboard.

---

## 5. User accounts and roles

| Role | Username | Password | Can do |
|---|---|---|---|
| Admin | `admin` | `admin` | Everything — Django admin, Wagtail CMS, user management |
| Researcher | `researcher` | `research123` | Raw data, quality flags, CSV downloads, API docs |
| Maintainer | `maintainer` | `maintain123` | Sensor status, maintenance logs, calibration notes |
| Public | *(any visitor)* | — | National overview, charts, simplified health info |

Change passwords immediately after deploying publicly.

---

## 6. Key URLs

| URL | What's there |
|---|---|
| `/dashboard/` | National overview — Nepal map, sensor grid, WHO callouts |
| `/dashboard/sensors/` | All sensors with status and latest reading |
| `/dashboard/sensors/<id>/` | Sensor detail — time series, completeness, metadata |
| `/dashboard/indoor-outdoor/` | Indoor vs outdoor PM₂.₅ comparison (Belauri) |
| `/api/v1/sensors/` | REST API — sensor list |
| `/api/v1/readings/` | REST API — readings (researcher API key required) |
| `/api/v1/charts/national/summary/` | Chart.js-ready JSON for the national page |
| `/api/v1/charts/sensor/<id>/timeseries/` | Time-series JSON for a single sensor |
| `/django-admin/` | Django admin |
| `/cms-admin/` | Wagtail CMS — edit documentation and policy pages |
| `/api-docs/` | API documentation page (editable via Wagtail) |

---

## 7. Adding a new data source

Create a new converter class in `apps/ingestion/converters/`:

```python
# apps/ingestion/converters/my_new_source.py
from apps.ingestion.base import BaseDataConverter

class MyNewSourceConverter(BaseDataConverter):
    source_name = "MyNewSource"
    source_type = "CSV_UPLOAD"   # or LIVE_API / BATCH_UPLOAD / EXTERNAL

    def validate_source(self, source) -> bool:
        # return True if the file/URL/object looks usable
        ...

    def parse_metadata(self, source) -> dict:
        # return {"sensor_serial": "...", "is_indoor": False, ...}
        ...

    def normalize_timestamps(self, df):
        # make df["original_ts"] timezone-aware (Asia/Kathmandu)
        ...

    def map_columns(self, df) -> list[dict]:
        # return list of canonical reading dicts
        # each dict needs: original_ts, pollutant, unit, raw_value,
        #                  sensor_id, site_id, is_indoor, source_type
        ...

    def assign_quality_flags(self, records) -> list[dict]:
        # set records[i]["quality_flag"] and records[i]["flag_reason"]
        ...
```

Then call it from a management command or Celery task:

```python
from apps.ingestion.converters.my_new_source import MyNewSourceConverter

result = MyNewSourceConverter().run("/path/to/file.csv")
print(result)  # {"saved": 1234, "duplicates": 0, "errors": 0, "status": "SUCCESS"}
```

The base class handles DB persistence, duplicate detection, and ingestion logging automatically.
See `apps/ingestion/converters/csv_converter.py` for a fully worked example.
