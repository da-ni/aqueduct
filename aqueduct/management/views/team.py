from functools import cached_property

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, UpdateView

from management.deletion import deletion
from management.forms import MemberEmailForm, TeamAvailabilityForm, TeamCreateForm
from management.models import Org, ServiceAccount, Team, UserProfile
from management.request_log import parse_id
from management.team_overview import team_page
from management.views.base import (
    ONLY_TEAM_ADMINS,
    BaseAqueductView,
    BaseTeamView,
    OrgAdminRequiredMixin,
    TeamAdminRequiredMixin,
)


class TeamCreateView(BaseAqueductView, OrgAdminRequiredMixin, CreateView):
    """
    Handles creation of new Teams. Requires Org Admin privileges.
    """

    model = Team
    form_class = TeamCreateForm
    template_name = "management/create/team.html"
    success_url = reverse_lazy("org")  # Redirects to organization dashboard

    @cached_property
    def target_org(self) -> Org:
        """The org the form was opened for. The form posts it, so switching the org in another
        tab before submitting does not move the team."""
        if self.request.method != "POST":
            return self.org
        org = Org.objects.filter(pk=parse_id(self.request.POST.get("org"))).first()
        if org is None or not self.profile.is_org_admin(org):
            raise Http404("No organization found.")
        return org

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["org"] = self.target_org
        return kwargs

    def form_valid(self, form) -> HttpResponse:
        form.instance.org = self.target_org
        response = super().form_valid(form)
        messages.success(self.request, f"Team {form.instance.name} created.")
        return response

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Create a team"
        context["target_org"] = self.target_org
        return context


class TeamUpdateView(BaseTeamView, TeamAdminRequiredMixin, UpdateView):
    """
    Handles editing an existing Team (name, description).
    Requires Team Admin privileges, checked by TeamAdminRequiredMixin.
    Uses TeamCreateForm for validation.
    """

    model = Team
    form_class = TeamCreateForm
    template_name = "management/edit/team.html"
    pk_url_kwarg = "id"
    context_object_name = "team"  # Match DetailView/template usage

    def get_form_kwargs(self) -> dict:
        """Pass the team's organization to the form for validation."""
        kwargs = super().get_form_kwargs()
        # self.team is guaranteed by dispatch
        kwargs["org"] = self.team.org
        return kwargs

    def form_valid(self, form) -> HttpResponse:
        """Adds a success message upon successful update."""
        response = super().form_valid(form)
        messages.success(self.request, f"Team {self.object.name} saved.")
        return response

    def get_success_url(self) -> str:
        """Redirect back to the team detail page after successful edit."""
        # self.object is the updated team instance
        return reverse("team", kwargs={"id": self.object.id})

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Edit team"
        context["cancel_url"] = self.get_success_url()  # Add cancel URL
        # context['team'] is set automatically by UpdateView
        return context


class TeamAvailabilityEditView(BaseTeamView, UpdateView):
    """Edits what a team's service account keys may use. Only an org admin of the team's org
    may, others get 404."""

    model = Team
    form_class = TeamAvailabilityForm
    template_name = "management/edit/team.html"
    context_object_name = "team"

    def get_object(self, queryset=None) -> Team:
        team = self.get_team_object()
        if not self.profile.is_org_admin(team.org):
            raise Http404("No team found.")
        return team

    def form_valid(self, form) -> HttpResponse:
        response = super().form_valid(form)
        messages.success(self.request, f"Availability of {self.object.name} saved.")
        return response

    def get_success_url(self) -> str:
        return reverse("team", kwargs={"id": self.object.id})

    def get_context_data(self, **kwargs) -> dict[str, object]:
        return super().get_context_data(
            view_title="Availability",
            breadcrumb="Availability",
            cancel_url=self.get_success_url(),
            **kwargs,
        )


class TeamDeleteView(BaseTeamView, TeamAdminRequiredMixin, DeleteView):
    """Deletes a team. Only an admin of the team's org may."""

    model = Team
    org_admin_only = True
    template_name = "management/common/confirm_delete.html"
    success_url = reverse_lazy("org")

    def form_valid(self, form) -> HttpResponse:
        team_name = self.team.name
        response = super().form_valid(form)
        messages.success(self.request, f"Team {team_name} deleted.")
        return response

    def get_context_data(self, **kwargs) -> dict[str, object]:
        return super().get_context_data(
            confirmation=deletion(self.team),
            cancel_url=reverse("team", kwargs={"id": self.team.id}),
            **kwargs,
        )


class TeamAdminManagementView(BaseTeamView, TeamAdminRequiredMixin, View):
    """Makes a member a team admin, or takes the rights away. Only an admin of the team's org
    may."""

    org_admin_only = True

    def post(self, request, *args, **kwargs) -> HttpResponse:
        team = self.team
        profile_id = request.POST.get("profile_id")
        action = request.POST.get("action")
        # Ensure the profile being managed belongs to the same org as the team (and the admin)
        profile = get_object_or_404(
            UserProfile, id=profile_id, org=self.team_org
        )  # Use team_org for consistency

        if profile == self.profile:
            messages.warning(request, "Org admins already have team admin rights.")
            return redirect(reverse("team", kwargs={"id": team.id}))

        # Ensure the target profile is actually a member of the team before changing admin status
        # Although get_or_create handles creation, it might be confusing if not explicitly added.
        # Let's find the membership first.
        try:
            membership = team.teammembership_set.get(user_profile=profile)
        except team.teammembership_set.model.DoesNotExist:
            messages.error(request, f"{profile.user.email} is not in this team.")
            return redirect(reverse("team", kwargs={"id": team.id}))

        if action == "add":
            if membership.is_admin:
                messages.info(request, f"{profile.user.email} is already a team admin.")
            else:
                membership.is_admin = True
                membership.save(update_fields=["is_admin"])
                messages.success(request, f"{profile.user.email} is now a team admin.")
        elif action == "remove":
            if membership.is_admin:
                membership.is_admin = False
                membership.save(update_fields=["is_admin"])
                messages.success(request, f"{profile.user.email} is no longer a team admin.")
            else:
                messages.info(request, f"{profile.user.email} is not a team admin.")
        else:
            messages.error(request, "Invalid action.")
        return redirect(reverse("team", kwargs={"id": team.id}))


class TeamDetailView(BaseTeamView, DetailView):
    """
    Displays team details, members, and service accounts.
    Allows Team Admins (or Org Admins) to manage membership via POST requests.
    """

    model = Team
    template_name = "management/team.html"
    context_object_name = "team"
    pk_url_kwarg = "id"

    def add_member_form(self, data=None) -> MemberEmailForm:
        return MemberEmailForm(
            data,
            candidates=self.team_org.user_profiles.all(),
            not_found=f"No one in {self.team_org.name} uses {{email}}.",
        )

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        team = self.team
        can_manage_admins = self.profile.is_org_admin(self.team_org)
        member_query = self.request.GET.get("q", "").strip()
        page = team_page(
            team,
            self.profile,
            can_manage_admins,
            member_query,
            self.request.GET.get("page", 1),
            timezone.now(),
        )
        max_service_accounts = getattr(settings, "MAX_SERVICE_ACCOUNTS_PER_TEAM", 10)
        context.setdefault("add_form", self.add_member_form())
        context.update(
            page=page,
            member_query=member_query,
            is_team_admin=self.profile.is_team_admin(team),
            can_manage_admins=can_manage_admins,
            is_org_admin=can_manage_admins,
            can_delete=self.is_org_admin(),
            can_edit_in_admin=self.request.user.is_staff,
            max_service_accounts=max_service_accounts,
            can_add_service_account=len(page.service_accounts) < max_service_accounts,
        )
        return context

    @transaction.atomic  # Ensure membership changes are atomic.
    def post(self, request, *args, **kwargs) -> HttpResponse:
        """Handles adding/removing team members. Requires Team Admin privileges."""
        team = self.team
        profile = self.profile
        redirect_url = reverse("team", kwargs={"id": team.id})

        if not profile.is_team_admin(team):
            messages.error(request, ONLY_TEAM_ADMINS)
            return redirect(redirect_url)

        user_profile_id_to_remove = request.POST.get("user_profile_to_remove_id")

        if "email" in request.POST:
            form = self.add_member_form(request.POST)
            if form.is_valid() and team.member_profiles.filter(id=form.profile.id).exists():
                form.add_error("email", f"{form.profile.user.email} is already in this team.")
            if not form.is_valid():
                self.object = team
                return self.render_to_response(self.get_context_data(add_form=form))
            team.member_profiles.add(form.profile)
            messages.success(request, f"{form.profile.user.email} added.")

        elif user_profile_id_to_remove:
            leaving = team.member_profiles.filter(id=parse_id(user_profile_id_to_remove)).first()
            if leaving is None:
                messages.error(request, "Member not found.")
            elif ServiceAccount.objects.filter(team=team, token__user__profile=leaving).exists():
                messages.error(
                    request, f"Transfer or delete {leaving.user.email}'s service accounts first."
                )
            else:
                team.member_profiles.remove(leaving)
                messages.success(request, f"{leaving.user.email} removed.")
        else:
            messages.warning(request, "Nothing to change.")

        return redirect(redirect_url)
