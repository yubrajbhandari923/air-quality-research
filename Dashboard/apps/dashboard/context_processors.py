"""Global template context injected into every request."""
from apps.sensors.models import Sensor


def dashboard_context(request):
    """Inject site-wide context needed by every page."""
    active_count = Sensor.objects.filter(status="ACTIVE").count()
    offline_count = Sensor.objects.filter(status="OFFLINE").count()

    return {
        "active_sensor_count": active_count,
        "offline_sensor_count": offline_count,
        "WHO_24H_GUIDELINE": 15.0,
        "NEPAL_NAAQS_24H": 40.0,
        "SITE_NAME": "Nepal Air Quality Dashboard",
    }
