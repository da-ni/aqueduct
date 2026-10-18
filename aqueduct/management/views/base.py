from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views import View

from management.access import TeamAct, Viewer
from management.models import Org, ServiceAccount, Team, UserProfile
from management.org_choice import viewer_of

NEW_KEY_TAG = "token-regenerated-key"


def show_new_key(request: HttpRequest, secret: str) -> None:
    """Show `secret` once, in the new-key panel at the top of the next page."""
    messages.info(request, secret, extra_tags=NEW_KEY_TAG)


class BaseAqueductView(LoginRequiredMixin, View):
    login_url = reverse_lazy("sso")
    redirect_field_name = "next"

    @property
    def viewer(self) -> Viewer:
        viewer = viewer_of(self.request)
        if viewer is None:
            raise UserProfile.DoesNotExist
        return viewer


ONLY_ORG_ADMINS = _("Only org admins can do that.")
ONLY_TEAM_ADMINS = _("Only team admins can do that.")


class OrgAdminRequiredMixin:
    def dispatch(self, request, *args, **kwargs) -> HttpResponse:
        if not self.viewer.administers(self.viewer.org):
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
        if self.org_admin_only and not self.viewer.may_team(TeamAct.ADMINISTER, team):
            messages.error(request, ONLY_ORG_ADMINS)
        elif not self.viewer.may_team(TeamAct.MANAGE, team):
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
                self.viewer.teams(TeamAct.OPEN).select_related("org"),
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
                ServiceAccount.objects.filter(team__in=self.viewer.teams(TeamAct.OPEN)),
                pk=self.kwargs.get(self.pk_url_kwarg),
            )
        return self.service_account_object

    def get_team_object(self) -> Team:
        return self.get_service_account_object().team

    def get_object(self, queryset=None) -> ServiceAccount:
        return self.get_service_account_object()
