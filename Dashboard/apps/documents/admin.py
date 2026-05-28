"""Admin configuration for the documents app."""
from django.contrib import admin
from .models import Document, DocumentCategory


@admin.register(DocumentCategory)
class DocumentCategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "access_level", "is_featured", "download_count", "created_at")
    list_filter = ("category", "access_level", "is_featured")
    search_fields = ("title", "description")
