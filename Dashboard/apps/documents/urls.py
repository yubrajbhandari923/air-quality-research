from django.urls import path
from .views import DocumentLibraryView

urlpatterns = [
    path("", DocumentLibraryView.as_view(), name="document_library"),
]
