# Getting Started — Nepal Air Observatory

## Overview

The Nepal Air Observatory is a Django-based dashboard for collecting, storing, and visualising air quality data from low-cost sensors deployed across Nepal. It is not a CMS-first project — all data flows through the REST API and CSV ingestion pipeline, with the Django admin and portal for day-to-day management.

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | 3.13 tested |
| uv or pip | any | `uv` preferred |
| Redis | 7+ | For Celery background tasks |
| PostgreSQL | 14+ | Production only; SQLite works locally |
| Node / npm | optional | Only if editing Tailwind config locally |

---

## Local setup (no Docker)

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd air-quality-research/Dashboard

# 2. Create virtual env and install deps
uv venv
source .venv/bin/activate
uv pip install -e .          # reads pyproject.toml

# 3. Configure environment
cp .env.example .env
# Edit .env — at minimum set DJANGO_SECRET_KEY and DEBUG=true

# 4. Run migrations (creates SQLite by default)
python manage.py migrate --settings=nepal_aq.settings.development

# 5. Create superuser
python manage.py createsuperuser --settings=nepal_aq.settings.development

# 6. Load sample data (30 days, 3 sensors)
python manage.py load_dummy_data --settings=nepal_aq.settings.development

# 7. Start the server
python manage.py runserver --settings=nepal_aq.settings.development
```

Visit `http://localhost:8000`. Default dummy-data credentials: `admin/admin`, `researcher/research123`, `maintainer/maintain123`.

---

## Local setup with Docker

```bash
cp .env.example .env          # edit as needed
docker compose up --build     # starts Django + PostgreSQL + Redis + Celery
```

The compose file wires `DATABASE_URL=postgres://...` and `CELERY_BROKER_URL=redis://...` automatically.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | insecure dev key | **Must** be overridden in production |
| `DJANGO_DEBUG` | `false` | Set `true` for local dev |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated list |
| `DATABASE_URL` | SQLite | e.g. `postgres://user:pass@host:5432/dbname` |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | |
| `BELAURI_DATA_DIR` | `../Data/Belauri` | Path to raw Belauri CSV files |
| `STATIC_ROOT` | `staticfiles/` | Where `collectstatic` writes |
| `MEDIA_ROOT` | `mediafiles/` | Uploaded files |

---

## Running Celery (background tasks)

Celery handles periodic ingestion and daily aggregate computation. Start it alongside the dev server:

```bash
# Worker
.venv/bin/celery -A nepal_aq worker -l info

# Beat scheduler (periodic tasks)
.venv/bin/celery -A nepal_aq beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

If Redis is not running, the site still works — Celery tasks simply won't execute. The dashboard will fall back to computing stats on the fly.

---

## Directory structure

```
Dashboard/
├── apps/
│   ├── analysis/      — custom analysis scripts (admin-editable Python)
│   ├── api/           — DRF endpoints (readings, sensors, charts, export)
│   ├── dashboard/     — public views + portal views
│   ├── exposure/      — district exposure estimator
│   ├── ingestion/     — CSV + API + OpenAQ converters
│   ├── readings/      — CanonicalReading, DailyAggregate models
│   ├── sensors/       — Sensor, Site, MaintenanceLog models
│   └── users/         — CustomUser with role-based access
├── docs/              — this directory (workflow guides)
├── static/            — CSS, JS, images
├── templates/         — Django HTML templates (Tailwind CDN)
├── nepal_aq/          — Django project settings + URL root
└── manage.py
```

---

## Key URLs

| URL | Description |
|-----|-------------|
| `/` | Homepage — sensor overview, latest readings |
| `/map/` | Leaflet map of all sensors |
| `/sensors/` | Sensor list |
| `/analysis/` | Interactive charts and custom analyses |
| `/data/` | Data access and CSV download |
| `/methods/` | Methodology documentation |
| `/portal/` | Researcher/maintainer portal (login required) |
| `/django-admin/` | Django admin |
| `/api/v1/` | REST API root |

---

## Role system

| Role | What they can do |
|------|-----------------|
| PUBLIC | View all public pages, download sample data (500 rows, last 7 days) |
| RESEARCHER | Full data download (50k rows), API key access |
| MAINTAINER | Upload CSVs, run ingestion, write analysis scripts, run portal |
| ADMIN | Everything + user management, sensor/site creation |

Change a user's role in Django admin → Users → select user → Role field.
