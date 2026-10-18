from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext
from django.views.generic import TemplateView

from management import usage_report as report
from management.access import EveryOrg, KeyAct, OrgWide, WholeOrg
from management.charts import HEAT_CLASSES, HEAT_HOURS, OVERVIEW_STATS, columns, heat, slots, stats
from management.ids import parse_id
from management.model_catalog import model_kinds
from management.models import Token
from management.periods import DEFAULT_SPAN, HOUR, Period
from management.request_filters import Filters, Key, describe_key, key_search, read_filters
from management.request_paging import PAGE_SIZE, Header
from management.usage_source import Sealed, UsageSource
from management.views.base import BaseAqueductView

HEAT_CACHE_SECONDS = 60 * 60


def _heat(
    extent: OrgWide, model: str | None, now: datetime, weeks: int, sealed: Sealed
) -> dict[tuple[int, int], int]:
    source = UsageSource.whole(
        extent, model, Period(now - timedelta(weeks=weeks), now, HOUR), sealed
    )
    if sealed.current(now):
        return source.heat()
    key = f"usage-heat:{extent.param}:{weeks}:{model or ''}"
    tokens = cache.get(key)
    if tokens is None:
        tokens = source.heat()
        cache.set(key, tokens, HEAT_CACHE_SECONDS)
    return tokens


def _describe(filters: Filters) -> Callable[[Token], Key]:
    every_org = isinstance(filters.viewer.chosen, EveryOrg)

    def describe(token: Token) -> Key:
        key = describe_key(token, filters.viewer, with_org=every_org)
        if not filters.can_pick_scope:
            return replace(key, note="")
        return replace(key, note=gettext("You")) if key.own else key

    return describe


class UsageDashboardView(BaseAqueductView, TemplateView):
    template_name = "management/usage.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        now = timezone.now()
        filters = read_filters(self.viewer, params, default_span=DEFAULT_SPAN, now=now)
        chosen, selected_key, period = self.viewer.chosen, filters.key, filters.period
        selected_org = chosen.org if isinstance(chosen, WholeOrg) else None

        retention_days = settings.REQUEST_RETENTION_DAYS
        sealed = Sealed.read()
        source = UsageSource.of(filters, sealed)
        timeline = source.series()
        total = timeline.total
        heat_weeks = max(1, min(4, int(retention_days // 7)))
        heat_model = params.get("heat_model")
        heat_model = heat_model if heat_model in filters.models else None

        more_keys = False
        if selected_key is not None:
            keys = report.user_id_shares(period.of(filters.requests), total.tokens)
        elif filters.only_key is not None:
            keys = ()
        else:
            ranked = report.ranked(
                source.key_totals(), report.KeyOrder.TOKENS, filters.viewer.keys(KeyAct.SEE)
            )
            more_keys = len(ranked) > report.TOP_KEYS
            keys = report.key_shares(ranked[: report.TOP_KEYS], total.tokens, _describe(filters))

        context.update(
            {
                "filters": filters,
                "period": period,
                "kpis": stats(slots(timeline.points, period.step), OVERVIEW_STATS),
                "requests_in_period": total.requests,
                "charts": tuple(
                    (shown, columns(timeline.points, period.step, shown))
                    for shown in ("tokens", "requests")
                ),
                "statuses": report.statuses(timeline, filters.log_url),
                "heat_org": selected_org,
                "heat_model": heat_model,
                "heat": heat(_heat(chosen, heat_model, now, heat_weeks, sealed), heat_weeks),
                "heat_days": heat_weeks * 7,
                "heat_legend": HEAT_CLASSES[1:],
                "heat_hours": HEAT_HOURS,
                "models": report.model_rows(
                    source.models(), model_kinds(), filters.log_url, approximate=source.approximate
                ),
                "approximate_tip": report.APPROXIMATE_MEDIAN if source.approximate else "",
                "keys": keys,
                "more_keys": more_keys,
                "all_keys_url": f"{reverse('usage_keys')}?{urlencode(filters.query)}",
                "orgs_usage": ()
                if selected_org is not None
                else report.org_shares(source.org_tokens()),
                "retention_warning": period.start < now - timedelta(days=retention_days),
                "retention_days": int(retention_days),
            }
        )
        return context


class KeyUsageView(BaseAqueductView, TemplateView):
    template_name = "management/usage_keys.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        params = request.GET.copy()
        params.pop("key", None)
        filters = read_filters(self.viewer, params, default_span=DEFAULT_SPAN, now=timezone.now())
        only_key = filters.only_key
        if not filters.can_pick_scope and only_key is not None and only_key.href is not None:
            return redirect(only_key.href)
        return self.render_to_response(self.get_context_data(filters=filters, **kwargs))

    def get_context_data(self, *, filters: Filters, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        q = params.get("q", "").strip()
        search = key_search(q, by_owner=filters.can_pick_scope)
        order = report.KeyOrder.parse(params.get("sort", "")) or report.KeyOrder.TOKENS
        totals = UsageSource.of(filters).key_totals()
        everyone = report.KeyTotals.of(totals)
        tokens = filters.viewer.keys(KeyAct.SEE)
        if q:
            ids = set(tokens.filter(search).values_list("id", flat=True))
            totals = {token_id: a for token_id, a in totals.items() if token_id in ids}
        rows = report.ranked(totals, order, tokens)
        count = len(rows)

        pages = Paginator(rows, PAGE_SIZE).get_page(parse_id(params.get("page")) or 1)
        keys = report.key_shares(
            pages.object_list, everyone.tokens, _describe(filters), everyone.busiest
        )
        context.update(
            {
                "filters": filters,
                "overview_url": f"{reverse('usage')}?{urlencode(filters.query)}",
                "keys": keys,
                "count": count,
                "q": q,
                "order": order,
                "headers": {
                    option.value: Header(
                        sortable=True,
                        choice=None if option is report.KeyOrder.TOKENS else option.value,
                        aria_sort=(
                            ("ascending" if option.ascending else "descending")
                            if option is order
                            else None
                        ),
                    )
                    for option in report.KeyOrder
                },
                "pages": pages,
            }
        )
        return context
