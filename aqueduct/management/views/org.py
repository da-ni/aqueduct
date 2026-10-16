# management/views/org.py

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView, View

from management.models import Org
from management.team_overview import team_rows
from management.views.base import BaseAqueductView


class OrgView(BaseAqueductView, TemplateView):
    """The teams page: the teams the user may open, with their size, limit, and usage."""

    template_name = "management/org.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        context.update(
            is_org_admin=self.is_org_admin(),
            query=query,
            rows=team_rows(self.get_teams_for_user(), self.profile, query, timezone.now()),
            org_object=self.org,
            org_limit=self.org.requests_per_minute,
            can_edit_in_admin=self.request.user.is_staff,
        )
        if self.profile.group == "admin":
            context["all_orgs"] = Org.objects.order_by("name")
        return context


class OrgSwitchView(BaseAqueductView, View):
    """
    Allows admin users to switch their current organization.
    """

    def post(self, request, *args, **kwargs) -> HttpResponse:
        # Only allow if user is a global admin
        if self.profile.group != "admin":
            messages.error(request, "Only admin users can switch organizations.")
            return redirect(reverse("org"))

        org_id = request.POST.get("org_id")
        if not org_id:
            messages.error(request, "No organization selected.")
            return redirect(reverse("org"))

        org = get_object_or_404(Org, pk=org_id)
        self.profile.org = org
        self.profile.save(update_fields=["org"])
        messages.success(request, f"Switched to organization: {org.name}")
        return redirect(reverse("org"))
