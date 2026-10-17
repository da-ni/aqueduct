from typing import Any

from django.conf import settings
from django.utils import timezone
from django.views.generic import TemplateView

from management.request_log import Outcome, read_filters, request_log
from management.usage_report import Span
from management.views.base import BaseAqueductView


def _id(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


class RequestLogView(BaseAqueductView, TemplateView):
    template_name = "management/requests.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        teams = self.get_teams_for_user()
        filters = read_filters(
            self.profile, teams, params, default_span=Span.MONTH, now=timezone.now()
        )
        outcome = Outcome.parse(params.get("status", ""))
        context.update(
            log=request_log(
                self.profile,
                teams,
                filters,
                outcome=outcome,
                page_number=params.get("page"),
                open_id=_id(params.get("request", "")),
            ),
            filters=filters,
            outcome=outcome,
            retention_days=int(getattr(settings, "REQUEST_RETENTION_DAYS", 30)),
        )
        return context
