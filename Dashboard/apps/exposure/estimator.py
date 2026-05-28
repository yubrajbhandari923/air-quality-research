"""
Population exposure estimator for Nepal districts.

Method: Nearest-sensor assignment with confidence classification.

Confidence tiers:
  HIGH   — sensor within 20 km, data completeness > 80%
  MEDIUM — sensor within 50 km, completeness 50–80%
  LOW    — sensor > 50 km away OR completeness < 50%
  NONE   — no active sensor within 200 km

Limitation note (always include in outputs):
  This is a simplified spatial proxy. Actual population exposure depends on
  indoor time, microenvironments, local emission sources, and meteorology that
  are not captured by the nearest outdoor sensor.

Data:
  District populations are from Nepal 2021 National Population and Housing Census.
  Only major districts are included here. Extend as sensor network grows.
"""
import logging
import math
from datetime import date, timedelta
from typing import Optional

from django.db.models import Avg, Count
from django.utils import timezone

logger = logging.getLogger(__name__)

WHO_24H = 15.0  # µg/m³

# ── Nepal district population data (2021 census) ──────────────────────────────
# format: {district_name: (population, centroid_lat, centroid_lon)}
NEPAL_DISTRICTS = {
    "Kathmandu":        (2017532, 27.7172, 85.3240),
    "Lalitpur":         (709892,  27.6588, 85.3247),
    "Bhaktapur":        (439014,  27.6710, 85.4298),
    "Kaski":            (492098,  28.2096, 83.9856),
    "Chitwan":          (579984,  27.5291, 84.3542),
    "Rupandehi":        (880196,  27.5970, 83.4295),
    "Morang":           (967641,  26.6635, 87.3481),
    "Sunsari":          (745655,  26.6376, 87.1685),
    "Banke":            (491313,  28.0500, 81.6119),
    "Kanchanpur":       (451248,  28.8459, 80.3344),
    "Dang":             (553116,  27.9952, 82.2956),
    "Kailali":          (775709,  28.6000, 80.5667),
    "Mahottari":        (627580,  26.6400, 85.9300),
    "Sarlahi":          (769729,  26.9500, 85.5700),
    "Parsa":            (601017,  27.0200, 84.8500),
    "Bara":             (756416,  27.0400, 85.0400),
    "Rautahat":         (612700,  27.0100, 85.3200),
    "Makwanpur":        (425482,  27.3500, 84.9700),
    "Nuwakot":          (277466,  27.9200, 85.1700),
    "Kavrepalanchok":   (381937,  27.6200, 85.5300),
    "Sindhupalchok":    (288478,  27.9500, 85.6800),
    "Dolakha":          (186557,  27.6700, 86.0700),
    "Solukhumbu":       (105886,  27.6800, 86.7000),
    "Jumla":            (108921,  29.2700, 82.1900),
    "Humla":            (50858,   29.9800, 81.8400),
    "Mustang":          (13452,   28.9800, 83.8700),
    "Manang":           (5645,    28.6700, 84.0200),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in km."""
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def _get_confidence(distance_km: float, completeness: float) -> str:
    if distance_km <= 20 and completeness >= 0.8:
        return "HIGH"
    if distance_km <= 50 and completeness >= 0.5:
        return "MEDIUM"
    if distance_km <= 200:
        return "LOW"
    return "NONE"


def estimate_district_exposure(target_date: Optional[date] = None) -> list[dict]:
    """
    Compute per-district PM2.5 exposure estimates for a given date.

    Args:
        target_date: date to estimate. Defaults to yesterday (to have complete data).

    Returns:
        List of dicts with exposure data for each district.

    Important: always display the confidence level and methodology note alongside results.
    """
    from apps.readings.models import CanonicalReading
    from apps.sensors.models import Sensor

    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    start_dt = timezone.datetime.combine(target_date, timezone.datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    end_dt = start_dt + timedelta(days=1)

    # Get active outdoor sensors with readings that day
    active_sensors = list(
        Sensor.objects.filter(status="ACTIVE", is_indoor=False).select_related("site")
    )

    sensor_data = {}
    for sensor in active_sensors:
        readings = CanonicalReading.objects.filter(
            sensor=sensor,
            pollutant="PM25",
            is_duplicate=False,
            quality_flag__in=["GOOD", "UNVALIDATED"],
            original_ts__gte=start_dt,
            original_ts__lt=end_dt,
        )
        agg = readings.aggregate(avg=Avg("raw_value"), count=Count("id"))
        if agg["count"]:
            # Assume 96 expected readings per day at 15-min intervals
            completeness = min(1.0, agg["count"] / 96)
            sensor_data[sensor.pk] = {
                "sensor": sensor,
                "pm25_mean": agg["avg"],
                "completeness": completeness,
                "lat": sensor.site.latitude,
                "lon": sensor.site.longitude,
                "site_name": sensor.site.name,
            }

    if not sensor_data:
        logger.warning("No sensor data available for %s", target_date)
        return []

    results = []
    for district, (population, d_lat, d_lon) in NEPAL_DISTRICTS.items():
        # Find nearest sensor
        nearest_pk = None
        nearest_dist = float("inf")
        for pk, sdata in sensor_data.items():
            dist = haversine_km(d_lat, d_lon, sdata["lat"], sdata["lon"])
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_pk = pk

        if nearest_pk is None:
            results.append({
                "district": district,
                "population": population,
                "pm25_mean_ugm3": None,
                "confidence": "NONE",
                "pop_above_who_24h": None,
            })
            continue

        sdata = sensor_data[nearest_pk]
        confidence = _get_confidence(nearest_dist, sdata["completeness"])
        pm25 = sdata["pm25_mean"]

        # Estimate exposed population (only for HIGH/MEDIUM confidence)
        pop_above_who = None
        if confidence in ("HIGH", "MEDIUM") and pm25 and pm25 > WHO_24H:
            pop_above_who = population  # Whole district if mean > WHO threshold

        results.append({
            "district": district,
            "population": population,
            "nearest_sensor_site": sdata["site_name"],
            "distance_km": round(nearest_dist, 1),
            "pm25_mean_ugm3": round(pm25, 1) if pm25 else None,
            "data_completeness": round(sdata["completeness"], 2),
            "confidence": confidence,
            "pop_above_who_24h": pop_above_who,
            "methodology_note": (
                f"Nearest-sensor assignment from {sdata['site_name']} "
                f"({round(nearest_dist, 1)} km away, {round(sdata['completeness']*100)}% data completeness). "
                "This is a spatial proxy only. See methodology for limitations."
            ),
        })

    return results


def save_exposure_estimates(target_date: Optional[date] = None) -> int:
    """Compute and save estimates to the DB. Returns number saved."""
    from .models import PopulationExposureEstimate
    from apps.sensors.models import Site

    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    estimates = estimate_district_exposure(target_date)
    saved = 0

    for est in estimates:
        # Find site FK
        site = None
        if est.get("nearest_sensor_site"):
            site = Site.objects.filter(name=est["nearest_sensor_site"]).first()

        obj, created = PopulationExposureEstimate.objects.update_or_create(
            district=est["district"],
            estimate_date=target_date,
            defaults={
                "nearest_site": site,
                "distance_km": est.get("distance_km"),
                "pm25_mean_ugm3": est.get("pm25_mean_ugm3"),
                "pm25_completeness": est.get("data_completeness"),
                "population": est.get("population"),
                "pop_above_who_24h": est.get("pop_above_who_24h"),
                "confidence": est.get("confidence", "NONE"),
                "methodology_note": est.get("methodology_note", ""),
            },
        )
        if created:
            saved += 1

    logger.info("Saved %d exposure estimates for %s", saved, target_date)
    return saved
