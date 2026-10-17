# management/views/org.py

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView, UpdateView, View

from management.forms import OrgLimitsForm, OrgSwitchOffsForm
from management.models import LimitSet, Org
from management.org_overview import org_page
from management.team_overview import team_rows
from management.views.base import BaseAqueductView, OrgAdminRequiredMixin


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
            org_limits=LimitSet.from_objects(self.org, None),
            can_edit_in_admin=self.request.user.is_staff,
        )
        if self.profile.group == "admin":
            context["all_orgs"] = Org.objects.order_by("name")
        return context


class OrganizationView(BaseAqueductView, OrgAdminRequiredMixin, TemplateView):
    """The organization page: its size, default limits, and switch-offs."""

    template_name = "management/organization.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context.update(org_object=self.org, page=org_page(self.org))
        if self.is_admin():
            context["all_orgs"] = Org.objects.order_by("name")
        return context


class OrgEditView(BaseAqueductView, UpdateView):
    """Edits an org's settings. Only an admin of that org may, others get 404."""

    model = Org
    pk_url_kwarg = "id"
    template_name = "management/edit/org.html"
    context_object_name = "org_object"
    success_url = reverse_lazy("organization")
    view_title = ""

    def get_object(self, queryset=None) -> Org:
        org = get_object_or_404(Org, pk=self.kwargs[self.pk_url_kwarg])
        if not self.profile.is_org_admin(org):
            raise Http404("No organization found.")
        return org

    def form_valid(self, form) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f"{self.view_title} of {self.object.name} saved.")
        return response

    def get_context_data(self, **kwargs) -> dict[str, object]:
        return super().get_context_data(view_title=self.view_title, **kwargs)


class OrgLimitsEditView(OrgEditView):
    form_class = OrgLimitsForm
    view_title = "Limits"


class OrgSwitchOffsEditView(OrgEditView):
    form_class = OrgSwitchOffsForm
    view_title = "Switch-offs"


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
        back = request.POST.get("next", "")
        if url_has_allowed_host_and_scheme(back, {request.get_host()}, request.is_secure()):
            return redirect(back)
        return redirect(reverse("org"))
