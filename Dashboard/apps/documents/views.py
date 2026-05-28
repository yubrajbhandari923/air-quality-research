"""Document library views."""
from django.views.generic import ListView
from .models import Document, DocumentCategory


class DocumentLibraryView(ListView):
    model = Document
    template_name = "documents/library.html"
    context_object_name = "documents"
    paginate_by = 20

    def get_queryset(self):
        qs = Document.objects.select_related("category", "uploaded_by").order_by("-is_featured", "-created_at")

        # Filter by access level based on user role
        user = self.request.user
        if not user.is_authenticated:
            qs = qs.filter(access_level="PUBLIC")
        elif hasattr(user, "is_researcher_or_above") and not user.is_researcher_or_above:
            qs = qs.filter(access_level="PUBLIC")

        category = self.request.GET.get("category")
        if category:
            qs = qs.filter(category__slug=category)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["categories"] = DocumentCategory.objects.all()
        ctx["selected_category"] = self.request.GET.get("category", "")
        return ctx
