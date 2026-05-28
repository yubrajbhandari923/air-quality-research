from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone

from .models import AnalysisScript, DownloadConfig


@admin.register(AnalysisScript)
class AnalysisScriptAdmin(admin.ModelAdmin):
    list_display = ["name", "run_mode", "chart_type", "is_active", "last_run_at", "status_badge", "sort_order"]
    list_editable = ["is_active", "sort_order"]
    list_filter = ["run_mode", "chart_type", "is_active"]
    search_fields = ["name", "description"]
    ordering = ["sort_order", "name"]
    readonly_fields = ["last_run_at", "last_error", "run_duration_ms", "cached_result", "run_now_hint"]

    fieldsets = [
        ("Script", {
            "fields": ["name", "description", "code", "is_active", "sort_order"],
        }),
        ("Execution", {
            "fields": ["run_mode", "interval_minutes", "chart_type"],
        }),
        ("Result (read-only)", {
            "fields": ["run_now_hint", "last_run_at", "run_duration_ms", "last_error", "cached_result"],
            "classes": ["collapse"],
        }),
    ]

    @admin.display(description="Status")
    def status_badge(self, obj):
        if obj.last_error:
            return format_html('<span style="color:#dc2626">✗ Error</span>')
        if obj.cached_result:
            return format_html('<span style="color:#16a34a">✓ OK</span>')
        return format_html('<span style="color:#9ca3af">— Not run</span>')

    @admin.display(description="")
    def run_now_hint(self, obj):
        if obj.pk:
            return format_html(
                '<a href="/portal/analysis/run/{}/" class="button" style="padding:4px 12px">'
                "Run now</a>",
                obj.pk,
            )
        return "Save first"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)


@admin.register(DownloadConfig)
class DownloadConfigAdmin(admin.ModelAdmin):
    list_display = ["__str__", "max_rows_public", "max_rows_researcher", "max_rows_admin", "public_days_limit"]

    def has_add_permission(self, request):
        return not DownloadConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
