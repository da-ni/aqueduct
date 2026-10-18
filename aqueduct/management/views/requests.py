from typing import Any

from django.conf import settings
from django.utils import timezone
from django.views.generic import TemplateView

from management.periods import Span
from management.request_log import read_filters, request_log
from management.views.base import BaseAqueductView


class RequestLogView(BaseAqueductView, TemplateView):
    template_name = "management/requests.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        filters = read_filters(self.viewer, params, default_span=Span.DAY, now=timezone.now())
        log = request_log(filters, params)
        context.update(
            log=log,
            filters=filters,
            outcome=log.outcome,
            narrowed=log.outcome is not None
            or bool(log.search)
            or filters.span is not Span.DAY
            or bool(filters.mine or filters.key or filters.model),
            retention_days=int(getattr(settings, "REQUEST_RETENTION_DAYS", 30)),
        )
        return context
