from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext
from django.views.generic import TemplateView

from management import usage_report as report
from management import usage_rollup as rollup
from management.key_activity import Activity
from management.model_catalog import model_kinds
from management.models import Org, Request, Token
from management.ownership import of_orgs
from management.request_log import (
    PAGE_SIZE,
    Filters,
    Header,
    Key,
    counts_whole_org,
    describe_key,
    key_search,
    parse_id,
    read_filters,
)
from management.views.base import BaseAqueductView


def _org_wide(filters: Filters) -> bool:
    return counts_whole_org(filters) and filters.span is not report.Span.HOUR


def _key_windows(filters: Filters) -> dict[int, Activity] | None:
    if not _org_wide(filters) or filters.span not in rollup.KEY_WINDOWS.values():
        return None
    org = filters.org
    tokens = None if org is None else Token.objects.filter(of_orgs({org.pk}))
    return rollup.key_window_totals(tokens, filters.model, filters.period)


HEAT_CACHE_SECONDS = 60 * 60


def _heat(
    org: Org | None,
    model: str | None,
    requests: QuerySet[Request],
    now: datetime,
    weeks: int,
    sealed: rollup.Sealed,
) -> dict[tuple[int, int], int]:
    start = now - timedelta(weeks=weeks)
    if sealed.current(now):
        return rollup.org_heat(org, start, now, sealed, requests, model)
    key = f"usage-heat:{org.pk if org else 'all'}:{weeks}:{model or ''}"
    tokens = cache.get(key)
    if tokens is None:
        tokens = rollup.org_heat(org, start, now, sealed, requests, model)
        cache.set(key, tokens, HEAT_CACHE_SECONDS)
    return tokens


def _org_tokens(filters: Filters) -> QuerySet[Token]:
    return (
        Token.objects.all()
        if filters.org is None
        else Token.objects.filter(of_orgs({filters.org.pk}))
    )


def _describe(filters: Filters) -> Callable[[Token], Key]:

    def describe(token: Token) -> Key:
        key = describe_key(token, filters.access, with_org=filters.org is None)
        if not filters.can_pick_scope:
            return replace(key, note="")
        return replace(key, note=gettext("You")) if key.own else key

    return describe


class UsageDashboardView(BaseAqueductView, TemplateView):
    template_name = "management/usage.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        profile = self.profile
        now = timezone.now()
        filters = read_filters(
            profile,
            self.get_teams_for_user(),
            params,
            org=self.viewed_org,
            default_span=report.DEFAULT_SPAN,
            now=now,
        )
        selected_org, selected_key, period = filters.org, filters.key, filters.period
        reqs = filters.requests

        retention_days = getattr(settings, "REQUEST_RETENTION_DAYS", 30)
        in_period = period.of(reqs)
        org_wide = _org_wide(filters)
        sealed = rollup.Sealed.read()
        org_requests = report.org_requests(selected_org)
        if org_wide:
            timeline = rollup.org_series(selected_org, filters.model, period, sealed, org_requests)
            models = rollup.org_models(selected_org, filters.model, period, sealed, org_requests)
        else:
            timeline, models = report.series(reqs, period), report.model_usage(in_period)
        total = timeline.total
        heat_weeks = max(1, min(4, int(retention_days // 7)))
        heat_model = params.get("heat_model")
        heat_model = heat_model if heat_model in filters.models else None

        more_keys = False
        if selected_key is not None:
            keys = report.user_id_shares(in_period, total.tokens)
        elif filters.only_key is not None:
            keys = ()
        else:
            windows = _key_windows(filters)
            ranked = (
                report.top_keys(in_period, report.TOP_KEYS + 1)
                if windows is None
                else report.ranked_totals(windows, report.KeyOrder.TOKENS, _org_tokens(filters))
            )
            more_keys = len(ranked) > report.TOP_KEYS
            keys = report.key_shares(ranked[: report.TOP_KEYS], total.tokens, _describe(filters))

        context.update(
            {
                "filters": filters,
                "period": period,
                "kpis": report.kpis(report.slots(timeline.points, period.step)),
                "requests_in_period": total.requests,
                "charts": tuple(
                    (shown, report.columns(timeline.points, period.step, shown))
                    for shown in ("tokens", "requests")
                ),
                "statuses": report.statuses(timeline, filters.log_url),
                "heat_org": selected_org,
                "heat_model": heat_model,
                "heat": report.heat(
                    _heat(selected_org, heat_model, org_requests, now, heat_weeks, sealed),
                    heat_weeks,
                ),
                "heat_days": heat_weeks * 7,
                "heat_legend": report.HEAT_CLASSES[1:],
                "heat_hours": report.HEAT_HOURS,
                "models": report.model_rows(
                    models, model_kinds(), filters.log_url, approximate=org_wide
                ),
                "approximate_tip": report.APPROXIMATE_MEDIAN if org_wide else "",
                "keys": keys,
                "more_keys": more_keys,
                "all_keys_url": f"{reverse('usage_keys')}?{urlencode(filters.query)}",
                "orgs_usage": ()
                if selected_org is not None
                else report.org_shares(
                    rollup.org_tokens(filters.model, period, sealed)
                    if org_wide
                    else report.org_tokens(in_period)
                ),
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
        filters = read_filters(
            self.profile,
            self.get_teams_for_user(),
            params,
            org=self.viewed_org,
            default_span=report.DEFAULT_SPAN,
            now=timezone.now(),
        )
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
        rows: list[report.KeyRow] | report.CountedKeys
        if (windows := _key_windows(filters)) is not None:
            everyone = report.KeyTotals.of(windows)
            tokens = _org_tokens(filters)
            if q:
                ids = set(tokens.filter(search).values_list("id", flat=True))
                windows = {token_id: a for token_id, a in windows.items() if token_id in ids}
            rows = report.ranked_totals(windows, order, tokens)
            count = len(rows)
        else:
            in_period = filters.period.of(filters.requests)
            everyone = report.key_totals(in_period)
            listed = in_period.filter(token__in=Token.objects.filter(search)) if q else in_period
            count = report.key_totals(listed).keys if q else everyone.keys
            rows = report.CountedKeys(report.ranked_keys(listed, order), count)

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
