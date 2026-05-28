"""Admin for exposure estimates."""
from django.contrib import admin
from .models import PopulationExposureEstimate


@admin.register(PopulationExposureEstimate)
class PopulationExposureEstimateAdmin(admin.ModelAdmin):
    list_display = ("district", "estimate_date", "pm25_mean_ugm3", "confidence", "pop_above_who_24h")
    list_filter = ("confidence", "estimate_date")
    search_fields = ("district",)
    readonly_fields = ("created_at", "updated_at")
