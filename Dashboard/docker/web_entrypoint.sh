#!/usr/bin/env bash
# Entrypoint for the Django web service in docker-compose.
# Runs once on container start: migrate → superuser → dev sensors → runserver.
set -euo pipefail

cd /app

# ── Wait for Postgres to accept connections ────────────────────────────────────
echo "[web] Waiting for Postgres..."
until python - <<'PY' 2>/dev/null
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nepal_aq.settings.development")
django.setup()
from django.db import connection
connection.ensure_connection()
PY
do
  sleep 1
done
echo "[web] Postgres is ready."

# ── Migrations ────────────────────────────────────────────────────────────────
echo "[web] Running migrations..."
python manage.py migrate --noinput

# ── Superuser (admin / admin) ─────────────────────────────────────────────────
echo "[web] Ensuring superuser exists..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@localhost', 'admin')
    print('[web] Superuser created: admin / admin')
else:
    print('[web] Superuser already exists.')
"

# ── Dev sensors + API keys ────────────────────────────────────────────────────
echo "[web] Setting up emulator sensors..."
python manage.py setup_dev_sensors --key-file /shared/dev_keys.env

echo "[web] ──────────────────────────────────────────────────────────"
echo "[web] Dashboard: http://localhost:8000"
echo "[web] Admin:     http://localhost:8000/admin  (admin / admin)"
echo "[web] API docs:  http://localhost:8000/data/"
echo "[web] ──────────────────────────────────────────────────────────"

# ── Start development server ──────────────────────────────────────────────────
exec python manage.py runserver 0.0.0.0:8000
