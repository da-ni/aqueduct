from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.views.generic import TemplateView
from tos.models import UserAgreement

from management.key_activity import personal_tokens
from management.models import LimitSet
from management.nav import terms_of_service
from management.views.base import BaseAqueductView

SHOWN_LIMITS = ("requests_per_minute", "input_tokens_per_minute", "output_tokens_per_minute")


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
            limits_set_for_account=any(getattr(profile, f) is not None for f in SHOWN_LIMITS),
            personal_keys=personal_tokens(self.request.user).count(),
            max_tokens=settings.MAX_USER_TOKENS,
            show_terms=show_terms,
            terms_accepted_on=accepted_terms_on(self.request.user) if show_terms else None,
        )
        return context
