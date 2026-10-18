from functools import cached_property

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View

from management.models import Org, ServiceAccount, Team, UserProfile
from management.request_log import chosen_org


class BaseAqueductView(LoginRequiredMixin, View):
    """
    Base view providing user profile/org context for logged-in users.
    """

    login_url = reverse_lazy("sso")
    redirect_field_name = "next"

    # Caching profile lookup per request avoids repeated DB hits via user.profile
    _profile: UserProfile | None = None

    @property
    def profile(self) -> UserProfile:
        """
        Returns the UserProfile for the logged-in user.
        This assumes a UserProfile always exists for an authenticated user,
        typically enforced via signals or user creation logic.
        """
        # LoginRequiredMixin ensures self.request.user is not AnonymousUser.
        # The OneToOneField relationship ensures the 'profile' attribute exists.
        # If the related UserProfile object *doesn't* exist for some reason
        # (data inconsistency), user.profile access will raise RelatedObjectDoesNotExist.
        # We let that happen as it indicates a deeper issue.
        if self._profile is None:
            self._profile = self.request.user.profile
        return self._profile

    @cached_property
    def viewed_org(self) -> Org | None:
        return chosen_org(self.profile, self.request)

    @property
    def org(self) -> Org:
        return self.viewed_org or self.profile.org

    def is_admin(self) -> bool:
        """Checks if the current user is a global admin."""
        return self.profile.is_admin()

    def is_org_admin(self) -> bool:
        """Checks if the current user is an admin of their associated organization."""
        # Relies on the is_org_admin method defined in the UserProfile model.
        # Assumes self.profile and self.org properties correctly return non-None objects.
        return self.profile.is_org_admin(self.org)

    def get_teams_for_user(self) -> QuerySet[Team]:
        """
        Returns a queryset of teams accessible by the current user.
        - Org admins see all teams in their org.
        - Regular users see only teams they are members of.
        """
        # profile and org are guaranteed by the properties above for logged-in users.
        if self.is_org_admin():
            # Org admin sees all teams in their organization.
            return self.org.teams.all()
        # Regular user sees only teams they are a member of via the ManyToManyField.
        # The 'teams' attribute is guaranteed to exist due to the M2M definition.
        return self.profile.teams.all()


ONLY_ORG_ADMINS = _("Only org admins can do that.")
ONLY_TEAM_ADMINS = _("Only team admins can do that.")


class OrgAdminRequiredMixin:
    def dispatch(self, request, *args, **kwargs) -> HttpResponse:
        if not hasattr(self, "is_org_admin") or not self.is_org_admin():
            messages.error(request, ONLY_ORG_ADMINS)
            return redirect(reverse_lazy("org"))
        return super().dispatch(request, *args, **kwargs)


class TeamAdminRequiredMixin:
    org_admin_only = False
    team_object: Team | None = None

    def get_team_object(self) -> Team:
        raise NotImplementedError(f"{self.__class__.__name__} must implement get_team_object()")

    def dispatch(self, request, *args, **kwargs) -> HttpResponse:
        team = self.team_object = self.get_team_object()
        profile = self.profile
        if self.org_admin_only and not profile.is_org_admin(team.org):
            messages.error(request, ONLY_ORG_ADMINS)
        elif not profile.is_team_admin(team):
            messages.error(request, ONLY_TEAM_ADMINS)
        else:
            return super().dispatch(request, *args, **kwargs)
        return redirect(reverse("team", kwargs={"id": team.id}))


class BaseTeamView(BaseAqueductView):
    pk_url_kwarg: str = "id"
    _team: Team | None = None

    def get_team_object(self) -> Team:
        if self._team is None:
            self._team = get_object_or_404(
                self.profile.openable_teams().select_related("org"),
                pk=self.kwargs.get(self.pk_url_kwarg),
            )
        return self._team

    @property
    def team(self) -> Team:
        return self.get_team_object()

    @property
    def team_org(self) -> Org:
        return self.team.org

    def get_object(self, queryset=None) -> Team | ServiceAccount:
        return self.team


class BaseServiceAccountView(BaseAqueductView, TeamAdminRequiredMixin, View):
    model = ServiceAccount
    pk_url_kwarg: str | None = None
    service_account_object: ServiceAccount | None = None

    def get_service_account_object(self) -> ServiceAccount:
        if self.service_account_object is None:
            if not self.pk_url_kwarg:
                raise ImproperlyConfigured(f"{self.__class__.__name__} must define 'pk_url_kwarg'.")
            self.service_account_object = get_object_or_404(
                ServiceAccount.objects.filter(team__in=self.profile.openable_teams()),
                pk=self.kwargs.get(self.pk_url_kwarg),
            )
        return self.service_account_object

    def get_team_object(self) -> Team:
        return self.get_service_account_object().team

    def get_object(self, queryset=None) -> ServiceAccount:
        return self.get_service_account_object()
