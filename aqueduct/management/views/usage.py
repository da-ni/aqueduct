from datetime import datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.generic import TemplateView

from gateway.config import get_router_config, model_info_with_context
from management import usage_report as report
from management import usage_rollup as rollup
from management.key_activity import Activity
from management.model_catalog import mode_for
from management.models import Org, Request, Token
from management.models.token import of_org
from management.request_log import (
    PAGE_SIZE,
    Filters,
    counts_whole_org,
    describe_key,
    key_search,
    parse_id,
    read_filters,
)
from management.views.base import BaseAqueductView


def _org_wide(filters: Filters) -> bool:
    """Whether the rollups count the selection. Their medians are approximate, so a single hour,
    which they hardly cover, keeps the exact ones."""
    return counts_whole_org(filters) and filters.span is not report.Span.HOUR


def _key_windows(filters: Filters) -> dict[int, Activity] | None:
    """The activity per key of the selection, from the key windows where they cover it."""
    if not _org_wide(filters) or filters.span not in rollup.KEY_WINDOWS.values():
        return None
    org = filters.org
    tokens = None if org is None else Token.objects.filter(of_org(org))
    return rollup.key_window_totals(tokens, filters.model, filters.period)


HEAT_CACHE_SECONDS = 60 * 60


def _heat(
    org: Org | None, requests: QuerySet[Request], now: datetime, weeks: int, sealed: rollup.Sealed
) -> dict[tuple[int, int], int]:
    """`rollup.org_heat` of the last `weeks` weeks. While the rollups are not current, it reads
    weeks of requests of a whole org, so it is cached for an hour."""
    start = now - timedelta(weeks=weeks)
    if sealed.current(now):
        return rollup.org_heat(org, start, now, sealed, requests)
    key = f"usage-heat:{org.pk if org else 'all'}:{weeks}"
    tokens = cache.get(key)
    if tokens is None:
        tokens = rollup.org_heat(org, start, now, sealed, requests)
        cache.set(key, tokens, HEAT_CACHE_SECONDS)
    return tokens


def _org_tokens(filters: Filters) -> QuerySet[Token]:
    return Token.objects.all() if filters.org is None else Token.objects.filter(of_org(filters.org))


class UsageDashboardView(BaseAqueductView, TemplateView):
    """The Overview page: usage of the keys the viewer may see, for one org or all orgs."""

    template_name = "management/usage.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        profile = self.profile
        now = timezone.now()
        filters = read_filters(
            profile, self.get_teams_for_user(), params, default_span=report.DEFAULT_SPAN, now=now
        )
        selected_org, selected_key, period = filters.org, filters.key, filters.period
        reqs = filters.requests
        model_kinds = {
            entry["model_name"]: mode_for(model_info_with_context(entry).get("mode")).label
            for entry in get_router_config().get("model_list", [])
        }

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
        metric = "requests" if params.get("metric") == "requests" else "tokens"
        heat_weeks = max(1, min(4, int(retention_days // 7)))

        if selected_key is not None:
            keys = report.user_id_shares(in_period, total.tokens)
        else:
            team_ids = set(self.get_teams_for_user().values_list("id", flat=True))
            windows = _key_windows(filters)
            keys = report.key_shares(
                report.top_keys(in_period)
                if windows is None
                else report.ranked_totals(windows, report.KeyOrder.TOKENS, _org_tokens(filters))[
                    : report.TOP_KEYS
                ],
                total.tokens,
                lambda token: describe_key(token, profile, team_ids, with_org=selected_org is None),
            )

        context.update(
            {
                "filters": filters,
                "period": period,
                "kpis": report.kpis(report.slots(timeline.points, period.step)),
                "requests_in_period": total.requests,
                "metric": metric,
                "chart": report.columns(timeline.points, period.step, metric),
                "statuses": report.statuses(timeline, filters.log_url),
                "heat_org": selected_org,
                "heat": report.heat(
                    _heat(selected_org, org_requests, now, heat_weeks, sealed), heat_weeks
                ),
                "heat_days": heat_weeks * 7,
                "heat_legend": report.HEAT_CLASSES[1:],
                "heat_hours": report.HEAT_HOURS,
                "models": report.model_rows(
                    models, model_kinds, filters.log_url, approximate=org_wide
                ),
                "approximate_tip": report.APPROXIMATE_MEDIAN if org_wide else "",
                "keys": keys,
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
    """The All keys page: the usage of every key the viewer may see, a page at a time, with the
    Overview's filters, so its first rows are the Overview's Top keys."""

    template_name = "management/usage_keys.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("key", None)
        teams = self.get_teams_for_user()
        filters = read_filters(
            self.profile, teams, params, default_span=report.DEFAULT_SPAN, now=timezone.now()
        )
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
        team_ids = set(teams.values_list("id", flat=True))
        keys = report.key_shares(
            pages.object_list,
            everyone.tokens,
            lambda token: describe_key(token, self.profile, team_ids, with_org=filters.org is None),
            everyone.busiest,
        )
        context.update(
            {
                "filters": filters,
                "overview_url": f"{reverse('usage')}?{urlencode(filters.query)}",
                "keys": keys,
                "count": count,
                "q": q,
                "orders": tuple(report.KeyOrder),
                "order": order,
                "pages": pages,
            }
        )
        return context
