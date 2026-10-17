from typing import ClassVar

from django import forms
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView

from gateway.rate_limiting import get_per_token_usage
from management.forms import TokenCreateForm
from management.key_activity import (
    Expiry,
    KeyKind,
    key_list,
    key_report,
    limit_labels,
    personal_tokens,
)
from management.models import Token
from management.views.base import BaseAqueductView


class UserTokensView(BaseAqueductView, TemplateView):
    template_name = "management/tokens.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        kind = KeyKind.parse(self.request.GET.get("kind", ""))
        query = self.request.GET.get("q", "").strip()
        max_tokens = getattr(settings, "MAX_USER_TOKENS", 3)
        keys = key_list(self.profile, kind, query, timezone.now())
        context.update(
            keys=keys,
            kinds=tuple(KeyKind),
            kind=kind,
            query=query,
            max_tokens=max_tokens,
            can_add_token=keys.personal_count < max_tokens,
        )
        return context


class TokenDetailView(BaseAqueductView, DetailView):
    model = Token
    template_name = "management/token_detail.html"
    pk_url_kwarg = "id"
    context_object_name = "token"

    def get_queryset(self) -> QuerySet:
        """The viewer's personal keys and the service account keys of every team they may see."""
        service = Token.objects.filter(service_account__team__in=self.get_teams_for_user())
        return (personal_tokens(self.request.user) | service).select_related(
            "service_account__team"
        )

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        token = self.object
        now = timezone.now()
        report = key_report(token, now)
        limits = token.get_limit()
        team = token.service_account.team if token.service_account else None
        metric = "requests" if self.request.GET.get("metric") == "requests" else "tokens"
        context.update(
            report=report,
            metric=metric,
            chart=report.chart(metric),
            expiry=Expiry.of(token.expires_at, now),
            limits=limits,
            limit_labels=limit_labels(limits),
            usage=get_per_token_usage([token.id])[token.id],
            excluded_models=token.model_exclusion_list(),
            excluded_mcp_servers=token.mcp_server_exclusion_list(),
            team=team,
            can_manage=team is None or self.profile.is_team_admin(team),
        )
        return context


class TokenCreateView(BaseAqueductView, CreateView):
    model = Token
    form_class = TokenCreateForm
    template_name = "management/create/token.html"
    success_url = reverse_lazy("tokens")  # Redirect to tokens list

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_form(self, form_class=None) -> forms.BaseForm:
        return super().get_form(form_class)

    @transaction.atomic
    def form_valid(self, form) -> HttpResponse:
        # Get the instance without saving to DB yet
        self.object = form.save(commit=False)

        # Assign the user directly to the instance
        self.object.user = self.request.user

        # Call the instance method to generate and set key data
        secret_key = self.object._set_new_key()  # Call method on the instance

        # Now save the fully prepared object to the database
        # This will trigger clean() with the user assigned
        self.object.save()

        # Use the returned secret_key in the success message
        messages.success(self.request, f"API key '{self.object.name}' created successfully.")
        messages.info(self.request, f"{secret_key}", extra_tags="token-regenerated-key")

        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Create an API key"

        now = timezone.now().replace(second=0, microsecond=0)
        context["now"] = now.strftime("%Y-%m-%dT%H:%M")
        context["tz"] = settings.TIME_ZONE
        return context


class TokenEditView(BaseAqueductView, UpdateView):
    model = Token
    form_class = TokenCreateForm
    template_name = "management/edit/token.html"
    pk_url_kwarg = "id"
    success_url = reverse_lazy("tokens")

    def get_queryset(self) -> QuerySet:
        return personal_tokens(self.request.user)

    def get_form_kwargs(self) -> dict:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["view_title"] = "Edit API key"
        context["cancel_url"] = self.get_success_url()
        return context


class TokenDeleteView(BaseAqueductView, DeleteView):
    model = Token
    pk_url_kwarg = "id"
    template_name = "management/common/confirm_delete.html"
    success_url = reverse_lazy("tokens")

    def get_queryset(self) -> QuerySet:
        return personal_tokens(self.request.user)

    def form_valid(self, form) -> HttpResponse:
        token_name = self.object.name
        response = super().form_valid(form)
        messages.success(self.request, f"API key '{token_name}' deleted successfully.")
        return response

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        context["object_type_name"] = "API key"
        context["object_name"] = self.object.name
        context["cancel_url"] = self.get_success_url()
        return context


@method_decorator(require_POST, name="dispatch")
class TokenRegenerateView(BaseAqueductView, View):
    """
    Handles regeneration of a token key (both user and service account tokens).
    Requires POST request.
    Permissions are checked based on token type.
    """

    http_method_names: ClassVar[list] = ["post"]

    def post(self, request, *args, **kwargs) -> HttpResponse:
        token_id = kwargs.get("id")
        profile = self.profile
        user = request.user

        try:
            token = get_object_or_404(
                Token.objects.select_related("service_account__team", "user"), pk=token_id
            )
            token_name = token.name
            is_service_account_token = token.service_account is not None

            # --- Permission Check --- #
            can_regenerate = False
            redirect_url = reverse_lazy("tokens")  # Default redirect

            if is_service_account_token:
                # Check if user is admin of the SA's team
                team = token.service_account.team
                redirect_url = reverse("team", kwargs={"id": team.id})
                if profile.is_team_admin(team):
                    can_regenerate = True
                else:
                    messages.error(
                        request,
                        f"You do not have permission to regenerate the token for service account "
                        f"'{token.service_account.name}'.",
                    )
            # Check if user owns the token
            elif token.user == user:
                can_regenerate = True
            else:
                # This case shouldn't happen with correct view/URL setup, but good to have
                messages.error(request, "You do not have permission to regenerate this token.")

            # --- Regeneration --- #
            if can_regenerate:
                new_key = token.regenerate_key()  # Assumes regenerate_key() saves the token
                messages.success(
                    request,
                    f"API key '{token_name}' has been regenerated. "
                    f"Please update any clients using the old key.",
                )
                messages.info(
                    request,
                    f"{new_key}",  # Only include the key itself in the message
                    extra_tags="token-regenerated-key",  # Use a specific tag
                )
            # Else: error message already added during permission check

        except Token.DoesNotExist:
            messages.error(request, "The specified token could not be found.")
            # Redirect to a default page if token not found
            redirect_url = reverse_lazy("tokens")
        except Exception as e:
            messages.error(
                request, f"An unexpected error occurred while regenerating the token: {e}"
            )
            # Try to determine redirect URL based on what we know, default if necessary
            try:
                # Attempt to get redirect URL again if token was fetched
                if is_service_account_token and team:
                    redirect_url = reverse("team", kwargs={"id": team.id})
            except NameError:  # If token fetch failed earlier
                redirect_url = reverse_lazy("tokens")

        return redirect(redirect_url)
