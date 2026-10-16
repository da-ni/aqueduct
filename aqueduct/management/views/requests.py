from typing import Any

from django.conf import settings
from django.views.generic import TemplateView

from management.models import Org
from management.request_log import Outcome, chosen_org, request_log
from management.views.base import BaseAqueductView


def _id(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


class RequestLogView(BaseAqueductView, TemplateView):
    template_name = "management/requests.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        profile = self.profile
        org = chosen_org(profile, params.get("org"))
        outcome = Outcome.parse(params.get("status", ""))
        context.update(
            log=request_log(
                profile,
                org,
                self.get_teams_for_user(),
                outcome=outcome,
                key_id=_id(params.get("key", "")),
                model=params.get("model"),
                page_number=params.get("page"),
                open_id=_id(params.get("request", "")),
            ),
            outcome=outcome,
            org=org,
            orgs=Org.objects.order_by("name") if profile.is_admin() else (),
            retention_days=int(getattr(settings, "REQUEST_RETENTION_DAYS", 30)),
        )
        return context
