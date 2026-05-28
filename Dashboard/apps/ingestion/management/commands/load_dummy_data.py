"""
Management command: load_dummy_data

Creates:
  - 2 Sites: Belauri (Kanchanpur) and Kathmandu Reference
  - 3 Sensors: 1 indoor (Air Assure), 2 outdoor (Bluesky outdoor + Kathmandu)
  - 30 days of realistic readings (Nepal winter PM2.5 patterns)
  - Maintenance log entries
  - A superuser (admin/admin) for quick dev access

Usage:
    python manage.py load_dummy_data
    python manage.py load_dummy_data --days 7 --clear
"""
import random
from datetime import date, datetime, timedelta

import pytz
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.sensors.models import MaintenanceLog, Sensor, Site
from apps.readings.models import CanonicalReading, DailyAggregate, Dataset

User = get_user_model()
NEPAL_TZ = pytz.timezone("Asia/Kathmandu")

# ── Realistic seasonal PM2.5 patterns ─────────────────────────────────────────
# Nepal winter (Oct–Feb): 30–200 µg/m³ (crop burning, stagnant air)
# Pre-monsoon (Mar–May): 20–100 µg/m³
# Monsoon (Jun–Sep): 5–30 µg/m³ (rain washout)
# These are outdoor values; indoor is ~70% of outdoor in winter, ~80% in monsoon

def seasonal_pm25(dt: datetime, is_indoor: bool) -> float:
    """Return a plausible PM2.5 value for Nepal with diurnal and seasonal variation."""
    month = dt.month
    hour = dt.hour

    # Seasonal base
    if month in (11, 12, 1, 2):       # Winter
        base = random.gauss(120, 40)
    elif month in (10, 3):             # Shoulder
        base = random.gauss(70, 25)
    elif month in (4, 5):              # Pre-monsoon
        base = random.gauss(50, 20)
    elif month in (6, 7, 8, 9):       # Monsoon
        base = random.gauss(15, 8)
    else:
        base = random.gauss(40, 15)

    # Diurnal pattern: peaks at morning (7–9) and evening (18–21) cooking times
    if 6 <= hour <= 9:
        diurnal = 1.6 + 0.3 * random.random()
    elif 17 <= hour <= 21:
        diurnal = 1.4 + 0.2 * random.random()
    elif 0 <= hour <= 5:
        diurnal = 0.9 + 0.1 * random.random()
    else:
        diurnal = 1.0 + 0.1 * random.random()

    value = max(1.0, base * diurnal)

    # Indoor is lower due to filtration but has cooking peaks
    if is_indoor:
        if 6 <= hour <= 9 or 17 <= hour <= 21:
            value *= random.uniform(0.85, 1.15)  # cooking elevates indoor
        else:
            value *= random.uniform(0.50, 0.70)  # filtration effect

    return round(value, 2)


def derive_pollutants(pm25: float, is_indoor: bool) -> dict:
    """Generate correlated pollutant values from a PM2.5 base."""
    pm1 = round(pm25 * random.uniform(0.65, 0.75), 2)
    pm4 = round(pm25 * random.uniform(1.02, 1.08), 2)
    pm10 = round(pm25 * random.uniform(1.1, 1.35), 2)
    nc05 = round(pm25 * random.uniform(7, 12), 0)
    nc1 = round(pm25 * random.uniform(5, 9), 0)
    nc25 = round(pm25 * random.uniform(4, 7), 0)

    temp = round(random.gauss(22 if is_indoor else 18, 5), 1)
    rh = round(min(98, max(15, random.gauss(65, 15))), 1)
    baro = round(random.gauss(29.3, 0.15), 2)

    result = {
        "PM1": (pm1, "µg/m³"),
        "PM25": (pm25, "µg/m³"),
        "PM4": (pm4, "µg/m³"),
        "PM10": (pm10, "µg/m³"),
        "NC05": (nc05, "#/cm³"),
        "NC1": (nc1, "#/cm³"),
        "NC25": (nc25, "#/cm³"),
        "TEMP": (temp, "°C"),
        "RH": (rh, "%"),
    }

    if is_indoor:
        co2 = round(random.gauss(800, 200), 0)
        tvoc = round(random.gauss(0.8, 0.4), 3)
        result["CO2"] = (max(400, co2), "ppm")
        result["TVOC"] = (max(0.1, tvoc), "mg/m³")
        result["BARO"] = (baro, "inHg")

    return result


class Command(BaseCommand):
    help = "Load realistic dummy data for development and demo purposes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Number of days of readings to generate (default: 30)",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing dummy data before loading",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=15,
            help="Reporting interval in minutes (default: 15)",
        )

    def handle(self, *args, **options):
        days = options["days"]
        interval_min = options["interval"]
        interval_sec = interval_min * 60

        if options["clear"]:
            self.stdout.write("Clearing existing data…")
            CanonicalReading.objects.all().delete()
            DailyAggregate.objects.all().delete()
            Dataset.objects.all().delete()
            Sensor.objects.all().delete()
            Site.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            self.stdout.write(self.style.WARNING("Cleared."))

        # ── Users ──────────────────────────────────────────────────────────────
        admin, _ = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@example.com",
                "is_staff": True,
                "is_superuser": True,
                "role": "ADMIN",
            },
        )
        admin.set_password("admin")
        admin.save()

        researcher, _ = User.objects.get_or_create(
            username="researcher",
            defaults={
                "email": "researcher@example.com",
                "role": "RESEARCHER",
                "affiliation": "Duke University",
            },
        )
        researcher.set_password("research123")
        researcher.save()

        maintainer, _ = User.objects.get_or_create(
            username="maintainer",
            defaults={
                "email": "maintainer@example.com",
                "role": "MAINTAINER",
            },
        )
        maintainer.set_password("maintain123")
        maintainer.save()

        self.stdout.write(self.style.SUCCESS("Users: admin / researcher / maintainer created."))

        # ── Sites ──────────────────────────────────────────────────────────────
        belauri_site, _ = Site.objects.get_or_create(
            name="Belauri",
            defaults={
                "district": "Kanchanpur",
                "municipality": "Belauri Municipality",
                "province": "Sudurpashchim Pradesh",
                "latitude": 28.6844,
                "longitude": 80.3646,
                "elevation_m": 200,
                "population_estimate": 25000,
                "land_use_type": "RESIDENTIAL",
                "description": (
                    "Belauri municipality in Kanchanpur district, far-western Nepal. "
                    "Agricultural/residential area. Study site for indoor/outdoor PM2.5 gradient research."
                ),
            },
        )

        ktm_site, _ = Site.objects.get_or_create(
            name="Kathmandu Reference",
            defaults={
                "district": "Kathmandu",
                "municipality": "Kathmandu Metropolitan City",
                "province": "Bagmati Pradesh",
                "latitude": 27.7172,
                "longitude": 85.3240,
                "elevation_m": 1400,
                "population_estimate": 1000000,
                "land_use_type": "URBAN",
                "description": "Urban reference site in Kathmandu valley.",
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Sites: {belauri_site}, {ktm_site}"))

        # ── Sensors ────────────────────────────────────────────────────────────
        outdoor_sensor, _ = Sensor.objects.get_or_create(
            serial_number="81432434001",
            defaults={
                "friendly_name": "Bluesky Outdoor Belauri",
                "model": "8143",
                "manufacturer": "Particles Plus",
                "site": belauri_site,
                "is_indoor": False,
                "status": "ACTIVE",
                "power_type": "GRID",
                "connectivity_type": "WIFI",
                "calibration_field_notes": "Factory calibrated. No field calibration performed.",
            },
        )

        indoor_sensor, _ = Sensor.objects.get_or_create(
            serial_number="81442406076",
            defaults={
                "friendly_name": "Air Assure Indoor Belauri",
                "model": "8144",
                "manufacturer": "Particles Plus",
                "site": belauri_site,
                "is_indoor": True,
                "status": "ACTIVE",
                "power_type": "GRID",
                "connectivity_type": "WIFI",
                "calibration_field_notes": "Factory calibrated. CO2 sensor requires 30-day burn-in.",
            },
        )

        ktm_sensor, _ = Sensor.objects.get_or_create(
            serial_number="KTM-REF-001",
            defaults={
                "friendly_name": "Kathmandu Reference Outdoor",
                "model": "8143",
                "manufacturer": "Particles Plus",
                "site": ktm_site,
                "is_indoor": False,
                "status": "ACTIVE",
                "power_type": "SOLAR",
                "connectivity_type": "CELLULAR",
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Sensors: {outdoor_sensor}, {indoor_sensor}, {ktm_sensor}"))

        # ── Maintenance logs ───────────────────────────────────────────────────
        if not MaintenanceLog.objects.filter(sensor=outdoor_sensor).exists():
            MaintenanceLog.objects.create(
                sensor=outdoor_sensor,
                logged_by=maintainer,
                event_date=date.today() - timedelta(days=15),
                event_type="INSPECTION",
                description="Routine inspection. Sensor housing cleaned. Fan speed verified at 6000 RPM.",
            )
            MaintenanceLog.objects.create(
                sensor=indoor_sensor,
                logged_by=maintainer,
                event_date=date.today() - timedelta(days=10),
                event_type="CALIBRATION",
                description="CO2 sensor zero-point calibration performed against fresh outdoor air.",
                resolved_at=datetime.now(NEPAL_TZ) - timedelta(days=9),
            )

        # ── Dataset ────────────────────────────────────────────────────────────
        dataset = Dataset.objects.create(
            name="Dummy Data — Development",
            description=f"Synthetic {days}-day dataset for development and demo.",
            source_type="CSV_UPLOAD",
            uploaded_by=admin,
            record_count=0,
        )

        # ── Readings ───────────────────────────────────────────────────────────
        self.stdout.write(f"Generating {days} days of readings at {interval_min}-minute intervals…")

        sensors_config = [
            (outdoor_sensor, belauri_site, False),
            (indoor_sensor, belauri_site, True),
            (ktm_sensor, ktm_site, False),
        ]

        end_dt = datetime.now(NEPAL_TZ).replace(minute=0, second=0, microsecond=0)
        start_dt = end_dt - timedelta(days=days)

        total_records = 0
        batch = []
        BATCH_SIZE = 5000

        current = start_dt
        while current <= end_dt:
            for sensor, site, is_indoor in sensors_config:
                # Simulate occasional data gaps (2% chance per interval)
                if random.random() < 0.02:
                    current += timedelta(minutes=interval_min)
                    continue

                pm25 = seasonal_pm25(current, is_indoor)
                pollutants = derive_pollutants(pm25, is_indoor)

                for pollutant, (value, unit) in pollutants.items():
                    flag = "GOOD"
                    reason = ""
                    if pollutant == "PM25" and value > 500:
                        flag = "SUSPECT"
                        reason = "Exceeds 500 µg/m³ saturation threshold."
                    elif pollutant == "TEMP" and (value < -10 or value > 55):
                        flag = "SUSPECT"
                        reason = "Temperature out of expected range."

                    batch.append(CanonicalReading(
                        original_ts=current,
                        timezone="Asia/Kathmandu",
                        interval_seconds=interval_sec,
                        pollutant=pollutant,
                        unit=unit,
                        raw_value=value,
                        cleaned_value=None,
                        sensor=sensor,
                        site=site,
                        is_indoor=is_indoor,
                        source_type="CSV_UPLOAD",
                        dataset=dataset,
                        quality_flag=flag,
                        flag_reason=reason,
                        is_duplicate=False,
                    ))

                if len(batch) >= BATCH_SIZE:
                    CanonicalReading.objects.bulk_create(batch, ignore_conflicts=True)
                    total_records += len(batch)
                    batch = []
                    self.stdout.write(f"  … {total_records:,} records saved", ending="\r")

            current += timedelta(minutes=interval_min)

        if batch:
            CanonicalReading.objects.bulk_create(batch, ignore_conflicts=True)
            total_records += len(batch)

        dataset.record_count = total_records
        dataset.save(update_fields=["record_count"])

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"\nDone! Created {total_records:,} readings across {days} days."
        ))
        self.stdout.write(self.style.SUCCESS(
            "\nLogin credentials:\n"
            "  Admin:      admin / admin\n"
            "  Researcher: researcher / research123\n"
            "  Maintainer: maintainer / maintain123\n"
            "\nDjango admin: http://localhost:8000/django-admin/\n"
            "Wagtail CMS:  http://localhost:8000/cms-admin/\n"
        ))
