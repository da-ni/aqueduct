# management/views/team.py
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, UpdateView

from management.forms import TeamCreateForm
from management.models import ServiceAccount, Team, UserProfile
from management.team_overview import team_page
from management.views.base import (
    BaseAqueductView,
    BaseTeamView,
    OrgAdminRequiredMixin,
    TeamAdminRequiredMixin,
    TeamViewerRequiredMixin,
)


class TeamCreateView(BaseAqueductView, OrgAdminRequiredMixin, CreateView):
    """
    Handles creation of new Teams. Requires Org Admin privileges.
    """

    model = Team
    form_class = TeamCreateForm
    template_name = "management/create/team.html"
    success_url = reverse_lazy("org")  # Redirects to organization dashboard

    # Permission checking is handled by OrgAdminRequiredMixin in dispatch.

    def get_form_kwargs(self) -> dict:
        """Pass the user's organization to the form."""
        kwargs = super().get_form_kwargs()
        # self.org is guaranteed by BaseAqueductView for authenticated users.
        kwargs["org"] = self.org
        return kwargs

    def form_valid(self, form) -> HttpResponse:
        """Assign the organization to the team instance before saving."""
        # self.org is guaranteed by BaseAqueductView.
        form.instance.org = self.org
        response = super().form_valid(form)
        messages.success(self.request, f"Team '{form.instance.name}' created successfully.")
        return response

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Create a team"
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
        messages.success(self.request, f"Team '{self.object.name}' updated successfully.")
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


class TeamDeleteView(BaseTeamView, OrgAdminRequiredMixin, DeleteView):
    """
    Handles deletion of a Team. Requires Org Admin privileges.
    Ensures the team belongs to the admin's organization implicitly via BaseTeamView fetching
    and OrgAdminRequiredMixin checks on self.org (user's org).
    """

    model = Team
    pk_url_kwarg = "id"
    template_name = "management/common/confirm_delete.html"
    success_url = reverse_lazy("org")

    # Permission checking is handled by OrgAdminRequiredMixin and BaseTeamView fetches team.
    # No need for get_queryset to filter by org, as OrgAdminRequiredMixin ensures user
    # is admin of their own org.
    # get_object() inherited from BaseTeamView returns self.team

    def form_valid(self, form) -> HttpResponse:
        """Adds a success message upon successful deletion."""
        team_name = self.team.name  # Use self.team from BaseTeamView
        response = super().form_valid(form)
        messages.success(self.request, f"Team '{team_name}' deleted successfully.")
        return response

    def get_context_data(self, **kwargs) -> dict[str, object]:
        """Provide context for the confirmation template."""
        context = super().get_context_data(**kwargs)
        context["object_type_name"] = self.model._meta.verbose_name
        context["object_name"] = str(self.team)  # Use self.team
        context["cancel_url"] = self.get_success_url()
        # context['object'] is implicitly set to self.team by DeleteView calling get_object()
        return context


class TeamAdminManagementView(BaseTeamView, OrgAdminRequiredMixin, View):
    """
    Allows org-admins to add or remove team admin status for team members.
    Only org-admins can use this view. POST to add or remove.
    """

    pk_url_kwarg = "id"  # Tell BaseTeamView how to find the team

    def post(self, request, *args, **kwargs) -> HttpResponse:
        # self.team is fetched by BaseTeamView
        team = self.team
        # self.profile and self.org (user's org) are available from
        # BaseAqueductView/OrgAdminRequiredMixin
        profile_id = request.POST.get("profile_id")
        action = request.POST.get("action")
        # Ensure the profile being managed belongs to the same org as the team (and the admin)
        profile = get_object_or_404(
            UserProfile, id=profile_id, org=self.team_org
        )  # Use team_org for consistency

        # OrgAdminRequiredMixin already confirmed the requesting user is an org admin.
        # Check if the target profile is the org admin themself (no-op)
        if profile == self.profile:
            messages.warning(request, "As an org-admin, you already have all team admin rights.")
            return redirect(reverse("team", kwargs={"id": team.id}))

        # Ensure the target profile is actually a member of the team before changing admin status
        # Although get_or_create handles creation, it might be confusing if not explicitly added.
        # Let's find the membership first.
        try:
            membership = team.teammembership_set.get(user_profile=profile)
        except team.teammembership_set.model.DoesNotExist:
            messages.error(
                request,
                f"{profile.user.email} is not a member of team {team.name}. "
                f"Cannot modify admin status.",
            )
            return redirect(reverse("team", kwargs={"id": team.id}))

        if action == "add":
            if membership.is_admin:
                messages.info(request, f"{profile.user.email} is already a team admin.")
            else:
                membership.is_admin = True
                membership.save(update_fields=["is_admin"])
                messages.success(
                    request, f"{profile.user.email} is now a team admin for {team.name}."
                )
        elif action == "remove":
            if membership.is_admin:
                membership.is_admin = False
                membership.save(update_fields=["is_admin"])
                messages.success(
                    request, f"{profile.user.email} is no longer a team admin for {team.name}."
                )
            else:
                messages.info(request, f"{profile.user.email} is not a team admin.")
        else:
            messages.error(request, "Invalid action.")
        return redirect(reverse("team", kwargs={"id": team.id}))


class TeamDetailView(BaseTeamView, TeamViewerRequiredMixin, DetailView):
    """
    Displays team details, members, and service accounts.
    Allows Team Admins (or Org Admins) to manage membership via POST requests.
    """

    model = Team
    template_name = "management/team.html"
    context_object_name = "team"
    pk_url_kwarg = "id"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        team = self.team
        can_manage_admins = self.profile.is_org_admin(self.team_org)
        page = team_page(team, self.profile, can_manage_admins, timezone.now())
        max_service_accounts = getattr(settings, "MAX_SERVICE_ACCOUNTS_PER_TEAM", 10)
        context.update(
            page=page,
            is_team_admin=self.profile.is_team_admin(team),
            can_manage_admins=can_manage_admins,
            can_delete=self.is_org_admin(),
            can_edit_in_admin=self.request.user.is_staff,
            max_service_accounts=max_service_accounts,
            can_add_service_account=len(page.service_accounts) < max_service_accounts,
        )
        return context

    @transaction.atomic  # Ensure membership changes are atomic.
    def post(self, request, *args, **kwargs) -> HttpResponse:
        """Handles adding/removing team members. Requires Team Admin privileges."""
        # self.team is fetched by BaseTeamView and verified in dispatch
        # self.profile is guaranteed by BaseAqueductView.
        team = self.team
        profile = self.profile
        redirect_url = reverse("team", kwargs={"id": team.id})

        # --- Security Check: Verify user is admin for *this specific team* ---
        # UserProfile.is_team_admin checks both explicit team admin status
        # and if the user is an admin of the team's organization.
        if not profile.is_team_admin(team):
            messages.error(request, "You do not have permission to modify this team's membership.")
            return redirect(redirect_url)

        user_profile_id_to_add = request.POST.get("user_profile_to_add_id")
        user_profile_id_to_remove = request.POST.get("user_profile_to_remove_id")

        # --- Action: Add User ---
        if user_profile_id_to_add:
            try:
                # Verify the profile exists and belongs to the *same organization*
                # as the team (which should match the admin's org unless they are superuser).
                profile_to_add = get_object_or_404(
                    UserProfile,
                    id=user_profile_id_to_add,
                    org=self.team_org,  # Ensures org consistency with the team
                )
                if team.member_profiles.filter(id=profile_to_add.id).exists():
                    messages.warning(
                        request, f"{profile_to_add.user.email} is already in the team."
                    )
                else:
                    team.member_profiles.add(profile_to_add)
                    # Note: TeamMembership object (with is_admin=False) is created automatically.
                    messages.success(
                        request, f"Added {profile_to_add.user.email} to team {team.name}."
                    )
            except UserProfile.DoesNotExist:
                # This message covers both not found and wrong org cases due to the filter.
                messages.error(
                    request, "User profile to add not found or not in this team's organization."
                )
            except Exception:
                messages.error(request, "An unexpected error occurred while adding the user.")

        # --- Action: Remove User ---
        elif user_profile_id_to_remove:
            try:
                # Verify the profile exists and is part of the team's org before checking membership
                profile_to_remove = get_object_or_404(
                    UserProfile,
                    id=user_profile_id_to_remove,
                    org=self.team_org,  # Ensures they are in the correct org
                )

                # --- Critical Check: Prevent removal if user owns SA tokens in this team ---
                owns_sa_in_team = ServiceAccount.objects.filter(
                    team=team, token__user__profile=profile_to_remove
                ).exists()

                if owns_sa_in_team:
                    messages.error(
                        request,
                        f"Cannot remove {profile_to_remove.user.email}. They own Service Account "
                        f"tokens in this team. Transfer or delete these tokens first.",
                    )
                    return redirect(redirect_url)  # Stop processing removal

                # Verify the user is actually a member before attempting removal.
                if team.member_profiles.filter(id=profile_to_remove.id).exists():
                    team.member_profiles.remove(profile_to_remove)
                    messages.success(
                        request, f"Removed {profile_to_remove.user.email} from team {team.name}."
                    )
                else:
                    # User wasn't a member anyway.
                    messages.warning(
                        request,
                        f"{profile_to_remove.user.email} is not currently a member of this team.",
                    )

            except UserProfile.DoesNotExist:
                # Covers not found or wrong org
                messages.error(
                    request, "User profile to remove not found or not in this team's organization."
                )
            except Exception:
                messages.error(request, "An unexpected error occurred while removing the user.")

        # --- No valid action ---
        elif not user_profile_id_to_add and not user_profile_id_to_remove:
            # Only show warning if the POST request was intended for modification
            # (e.g., check for a specific button name if necessary)
            # For simplicity, showing warning if neither ID is present.
            messages.warning(request, "No add or remove action specified.")

        return redirect(redirect_url)
