from typing import Any

from django.conf import settings
from django.utils import timezone
from django.views.generic import TemplateView

from management.request_log import read_filters, request_log
from management.usage_report import Span
from management.views.base import BaseAqueductView


class RequestLogView(BaseAqueductView, TemplateView):
    template_name = "management/requests.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        teams = self.get_teams_for_user()
        filters = read_filters(
            self.profile,
            teams,
            params,
            org=self.viewed_org,
            default_span=Span.DAY,
            now=timezone.now(),
        )
        log = request_log(self.profile, teams, filters, params)
        context.update(
            log=log,
            filters=filters,
            outcome=log.outcome,
            search_params=tuple(
                (name, value) for name, value in filters.search_params if name != "q"
            ),
            narrowed=log.outcome is not None
            or bool(log.search)
            or filters.span is not Span.DAY
            or bool(filters.mine or filters.key or filters.model),
            retention_days=int(getattr(settings, "REQUEST_RETENTION_DAYS", 30)),
        )
        return context
