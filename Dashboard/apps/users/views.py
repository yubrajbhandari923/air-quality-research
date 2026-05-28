"""User authentication and profile views."""
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import DetailView

from .models import CustomUser


class LoginView(View):
    """Standard login page."""

    template_name = "users/login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("observatory:home")
        form = AuthenticationForm(request)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f"Welcome back, {user.get_short_name() or user.username}!")
            return redirect(request.GET.get("next", "observatory:home"))
        return render(request, self.template_name, {"form": form})


class LogoutView(View):
    """Log out and redirect to home."""

    def post(self, request):
        logout(request)
        messages.info(request, "You have been signed out.")
        return redirect("/")


@method_decorator(login_required, name="dispatch")
class ProfileView(DetailView):
    """User profile page."""

    model = CustomUser
    template_name = "users/profile.html"
    context_object_name = "profile_user"

    def get_object(self):
        return self.request.user
