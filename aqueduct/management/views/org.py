from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.contrib import messages
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext
from django.views.generic import TemplateView, UpdateView, View

from management.forms import OrgAvailabilityForm, OrgLimitsForm
from management.models import Org, Team
from management.org_overview import org_page
from management.request_log import ORG_BOUND_PARAMS, parse_id, remember_org
from management.team_overview import team_rows
from management.views.base import BaseAqueductView, OrgAdminRequiredMixin


class OrgView(BaseAqueductView, TemplateView):
    """The teams page: the teams the user may open, with their size, limit, and usage. An admin
    viewing every org sees the teams of every org."""

    template_name = "management/org.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        all_orgs = self.viewed_org is None
        teams = Team.objects.all() if all_orgs else self.get_teams_for_user()
        context.update(
            is_org_admin=self.is_org_admin(),
            all_orgs=all_orgs,
            in_no_team=not self.is_org_admin() and not teams.exists(),
            query=query,
            teams=team_rows(
                teams, self.profile, query, self.request.GET.get("page", 1), timezone.now()
            ),
            org_object=self.org,
        )
        return context


class OrganizationView(BaseAqueductView, OrgAdminRequiredMixin, TemplateView):
    """The organization page: its size, default limits, and what is unavailable in it. A
    platform admin viewing every org picks one from a list instead."""

    template_name = "management/organization.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        if self.viewed_org is None:
            context["orgs"] = Org.objects.annotate(team_count=Count("teams")).order_by("name")
        else:
            context.update(org_object=self.org, page=org_page(self.org))
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


class OrgAvailabilityEditView(OrgEditView):
    form_class = OrgAvailabilityForm
    view_title = "Availability"


class OrgSwitchView(BaseAqueductView, View):
    """Sets the org an admin looks at, one org or every org, for every page of their session."""

    def post(self, request, *args, **kwargs) -> HttpResponse:
        if not self.is_admin():
            messages.error(request, gettext("Only platform admins can do that."))
            return redirect(reverse("org"))

        raw = request.POST.get("org_id", "")
        org = None if raw == "all" else get_object_or_404(Org, pk=parse_id(raw))
        remember_org(request.session, org)
        messages.success(
            request,
            gettext("Viewing %(name)s.") % {"name": org.name}
            if org
            else gettext("Viewing all organizations."),
        )
        back = request.POST.get("next", "")
        if url_has_allowed_host_and_scheme(back, {request.get_host()}, request.is_secure()):
            return redirect(_without_org_params(back))
        return redirect(reverse("org"))


def _without_org_params(url: str) -> str:
    """`url` without the parameters that name something of the org the viewer leaves."""
    parts = urlsplit(url)
    query = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if name not in ORG_BOUND_PARAMS
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))
