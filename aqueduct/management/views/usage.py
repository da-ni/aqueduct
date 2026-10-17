import math
from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.generic import TemplateView

from gateway.config import get_router_config, model_info_with_context
from management import usage_report as report
from management.model_catalog import mode_for
from management.models import Token
from management.request_log import PAGE_SIZE, describe_key, key_search, parse_id, read_filters
from management.views.base import BaseAqueductView


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
        timeline = report.series(reqs, period)
        total = timeline.total
        metric = "requests" if params.get("metric") == "requests" else "tokens"
        heat_weeks = max(1, min(4, int(retention_days // 7)))

        if selected_key is not None:
            keys = report.user_id_shares(in_period, total.tokens)
        else:
            team_ids = set(self.get_teams_for_user().values_list("id", flat=True))
            keys = report.key_shares(
                in_period,
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
                "heat": report.heat(selected_org, now, heat_weeks),
                "heat_days": heat_weeks * 7,
                "heat_legend": report.HEAT_CLASSES[1:],
                "heat_hours": report.HEAT_HOURS,
                "models": report.model_rows(in_period, model_kinds, filters.log_url),
                "keys": keys,
                "all_keys_url": f"{reverse('usage_keys')}?{urlencode(filters.query)}",
                "orgs_usage": report.org_shares(in_period) if selected_org is None else (),
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
        in_period = filters.period.of(filters.requests)
        everyone = report.key_totals(in_period)

        q = params.get("q", "").strip()
        listed = in_period
        count = everyone.keys
        if q:
            found = Token.objects.filter(key_search(q, by_owner=filters.can_pick_scope))
            listed = in_period.filter(token__in=found)
            count = report.key_totals(listed).keys

        order = report.KeyOrder.parse(params.get("sort", "")) or report.KeyOrder.TOKENS
        pages = max(1, math.ceil(count / PAGE_SIZE))
        page = min(parse_id(params.get("page")) or 1, pages)
        team_ids = set(teams.values_list("id", flat=True))
        keys = report.key_shares(
            listed,
            everyone.tokens,
            lambda token: describe_key(token, self.profile, team_ids, with_org=filters.org is None),
            order=order,
            start=(page - 1) * PAGE_SIZE,
            stop=page * PAGE_SIZE,
            busiest=everyone.busiest,
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
                "page": page,
                "pages": pages,
            }
        )
        return context
