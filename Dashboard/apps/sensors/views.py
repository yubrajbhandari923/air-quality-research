"""Views for sensor browsing (used by dashboard URLs)."""
from django.views.generic import DetailView, ListView

from .models import Sensor, Site


class SensorListView(ListView):
    model = Sensor
    template_name = "dashboard/sensor_list.html"
    context_object_name = "sensors"
    paginate_by = 30

    def get_queryset(self):
        qs = Sensor.objects.select_related("site").order_by("site__name", "serial_number")
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        indoor = self.request.GET.get("indoor")
        if indoor in ("true", "false"):
            qs = qs.filter(is_indoor=(indoor == "true"))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = Sensor.Status.choices
        ctx["selected_status"] = self.request.GET.get("status", "")
        return ctx


class SensorDetailView(DetailView):
    model = Sensor
    template_name = "dashboard/sensor_detail.html"
    context_object_name = "sensor"

    def get_queryset(self):
        return Sensor.objects.select_related("site").prefetch_related(
            "maintenance_logs", "calibration_records"
        )
