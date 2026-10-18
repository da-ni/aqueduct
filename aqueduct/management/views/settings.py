from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import TemplateView
from tos.models import UserAgreement

from management.key_activity import personal_tokens
from management.models import LimitSet
from management.nav import terms_of_service
from management.views.auth import is_safe, remember_language
from management.views.base import BaseAqueductView

LANGUAGE_CODES = frozenset(code for code, _name in settings.LANGUAGES)


def accepted_terms_on(user: AbstractBaseUser) -> datetime | None:
    agreement = UserAgreement.objects.filter(user=user, terms_of_service__active=True).first()
    return agreement.created if agreement else None


class SettingsView(BaseAqueductView, TemplateView):
    template_name = "management/settings.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        profile = self.profile
        show_terms = terms_of_service(self.request)
        context.update(
            memberships=profile.teammembership_set.select_related("team").order_by("team__name"),
            limits=LimitSet.from_objects(profile, profile.org),
            limits_set_for_account=not LimitSet.from_objects(profile, None).is_unlimited,
            personal_keys=personal_tokens(self.request.user).count(),
            max_tokens=settings.MAX_USER_TOKENS,
            show_terms=show_terms,
            terms_accepted_on=accepted_terms_on(self.request.user) if show_terms else None,
            languages=sorted(settings.LANGUAGES, key=lambda language: language[1]),
            language_chosen=bool(profile.language),
        )
        return context


class LanguageView(BaseAqueductView):
    def post(self, request, *args, **kwargs) -> HttpResponse:
        back = request.POST.get("next")
        response = redirect(back if is_safe(request, back) else reverse("settings"))
        language = request.POST.get("language", "")
        if language not in LANGUAGE_CODES:
            messages.error(request, _("Choose one of the offered languages."))
            return response
        self.profile.language = language
        self.profile.save(update_fields=["language"])
        remember_language(response, language)
        return response
