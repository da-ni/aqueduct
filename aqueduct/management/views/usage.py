from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.views.generic import TemplateView

from gateway.config import get_router_config, model_info_with_context
from management import usage_report as report
from management.model_catalog import mode_for
from management.request_log import read_filters
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
        retention_start = now - timedelta(days=retention_days)
        previous = period.previous(now)
        knows_previous = previous.start >= retention_start
        in_period = period.of(reqs)
        points = report.series(reqs, period)
        metric = "requests" if params.get("metric") == "requests" else "tokens"
        heat_weeks = max(1, min(4, int(retention_days // 7)))

        if selected_key is not None:
            keys = report.user_id_shares(in_period)
        else:
            keys = report.key_shares(
                in_period,
                viewer_id=profile.user.id,
                team_ids=set(self.get_teams_for_user().values_list("id", flat=True)),
                with_org=selected_org is None,
            )
        spark_slots = report.slots(points, period.step)
        requests_in_period = sum(a.requests for _, a in points)
        kpis = report.kpis(
            spark_slots,
            report.median_ms(in_period),
            report.slot_medians(in_period, spark_slots),
            report.totals(previous.of(reqs)) if knows_previous else None,
            report.median_ms(previous.of(reqs)) if knows_previous else None,
        )

        context.update(
            {
                "filters": filters,
                "period": period,
                "kpis": kpis,
                "compares_previous": any(kpi.change for kpi in kpis),
                "requests_in_period": requests_in_period,
                "metric": metric,
                "chart": report.columns(points, period.step, metric),
                "statuses": report.statuses(in_period, requests_in_period, filters.log_url),
                "heat": report.heat(reqs, now, heat_weeks),
                "heat_days": heat_weeks * 7,
                "heat_legend": report.HEAT_CLASSES[1:],
                "heat_hours": report.HEAT_HOURS,
                "models": report.model_rows(in_period, model_kinds, filters.log_url),
                "keys": keys,
                "orgs_usage": report.org_shares(in_period) if selected_org is None else (),
                "retention_warning": period.start < retention_start,
                "retention_days": int(retention_days),
            }
        )
        return context
