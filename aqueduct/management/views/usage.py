import datetime as dt
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.generic import TemplateView

from gateway.config import get_router_config, model_info_with_context
from management import usage_report as report
from management.model_catalog import mode_for
from management.models import Org
from management.request_log import chosen_org, visible_requests, visible_tokens
from management.views.base import BaseAqueductView


class UsageDashboardView(BaseAqueductView, TemplateView):
    """The Overview page: usage of the keys the viewer may see, for one org or all orgs."""

    template_name = "management/usage.html"

    def get_context_data(self, **kwargs) -> dict[str, object]:
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        profile = self.profile
        is_global_admin = profile.is_admin()
        selected_org = chosen_org(profile, params.get("org"))
        sees_others = selected_org is None or profile.is_org_admin(selected_org)
        teams = self.get_teams_for_user()
        tokens = visible_tokens(profile, selected_org, teams)
        reqs = visible_requests(profile, selected_org, teams)

        mine = sees_others and params.get("scope") == "mine"
        if mine:
            reqs = reqs.filter(
                Q(token__user=profile.user, token__service_account__isnull=True)
                | Q(token__service_account__team__in=profile.teams.all())
            )

        selected_token = None
        if "token" in params:
            if not params["token"].isdigit():
                raise Http404
            selected_token = get_object_or_404(tokens, pk=int(params["token"]))
            reqs = reqs.filter(token=selected_token)

        model_kinds = {
            entry["model_name"]: mode_for(model_info_with_context(entry).get("mode")).label
            for entry in get_router_config().get("model_list", [])
        }
        selected_model = params.get("model", "")
        if selected_model in model_kinds:
            reqs = reqs.filter(model=selected_model)
        else:
            selected_model = ""

        now = timezone.now()
        custom_start_str = params.get("start", "")
        custom_end_str = params.get("end", "")
        custom_start, custom_end, custom_error = self._parse_custom_range(
            custom_start_str, custom_end_str, now
        )
        span = report.Span.parse(params.get("span", "")) or report.DEFAULT_SPAN
        is_custom = bool(custom_start and custom_end and not custom_error)
        if is_custom:
            period = report.Period.between(custom_start, custom_end)
        else:
            period = report.Period.last(span, now)

        retention_days = getattr(settings, "REQUEST_RETENTION_DAYS", 30)
        retention_start = now - timedelta(days=retention_days)
        previous = period.previous(now)
        knows_previous = previous.start >= retention_start
        in_period = period.of(reqs)
        points = report.series(reqs, period)
        metric = "requests" if params.get("metric") == "requests" else "tokens"
        heat_weeks = max(1, min(4, int(retention_days // 7)))

        if selected_token is not None:
            keys = report.user_id_shares(in_period)
        else:
            keys = report.key_shares(
                in_period,
                viewer_id=profile.user.id,
                team_ids=set(self.get_teams_for_user().values_list("id", flat=True)),
                with_org=selected_org is None,
            )

        context.update(
            {
                "is_global_admin": is_global_admin,
                "orgs": Org.objects.order_by("name") if is_global_admin else (),
                "selected_org": selected_org,
                "can_pick_scope": sees_others,
                "mine": mine,
                "selected_token": selected_token,
                "model_names": sorted(model_kinds),
                "selected_model": selected_model,
                "spans": tuple(report.Span),
                "selected_span": None if is_custom else span,
                "period": period,
                "kpis": report.kpis(
                    points,
                    report.median_ms(in_period),
                    report.totals(previous.of(reqs)) if knows_previous else None,
                    report.median_ms(previous.of(reqs)) if knows_previous else None,
                ),
                "compares_previous": knows_previous,
                "metric": metric,
                "chart": report.columns(points, period.step, metric),
                "statuses": report.statuses(in_period, sum(a.requests for _, a in points)),
                "heat": report.heat(reqs, now, heat_weeks),
                "heat_days": heat_weeks * 7,
                "heat_legend": report.HEAT_CLASSES[1:],
                "heat_hours": report.HEAT_HOURS,
                "models": report.model_rows(in_period, model_kinds),
                "keys": keys,
                "orgs_usage": report.org_shares(in_period) if selected_org is None else (),
                "retention_warning": period.start < retention_start,
                "retention_days": int(retention_days),
                "custom_start": custom_start_str,
                "custom_end": custom_end_str,
                "custom_error": custom_error,
                "kept_params": [
                    (name, params[name])
                    for name in ("org", "scope", "token", "model", "metric")
                    if params.get(name)
                ],
                "now_utc_iso": now.strftime("%Y-%m-%dT%H:%M"),
            }
        )
        return context

    @staticmethod
    def _validate_custom_range(
        start: dt.datetime | None, end: dt.datetime | None, now: dt.datetime
    ) -> str | None:
        """Return the first validation error for a custom range, or ``None``."""
        if not (start and end):
            return None
        if end > now:
            return "End time is in the future."
        if start >= end:
            return "Start must be before end."
        if (end - start) < timedelta(minutes=1):
            return "Range must be at least 1 minute."
        return None

    def _parse_custom_range(
        self, start_str: str, end_str: str, now: dt.datetime
    ) -> tuple[dt.datetime | None, dt.datetime | None, str | None]:
        """Parse and validate the optional custom start/end query params.

        Returns ``(start, end, error)``:
            - ``start`` / ``end`` are timezone-aware datetimes in UTC, or ``None``
              if missing or invalid.
            - ``error`` is a human-readable message describing the first
              validation problem found, or ``None`` when the range is valid.
        """
        if not start_str and not end_str:
            return None, None, None
        try:
            start = dt.datetime.fromisoformat(start_str) if start_str else None
            end = dt.datetime.fromisoformat(end_str) if end_str else None
        except (ValueError, TypeError):
            return None, None, "Invalid date format."
        if start is not None and timezone.is_naive(start):
            start = timezone.make_aware(start, dt.UTC)
        if end is not None and timezone.is_naive(end):
            end = timezone.make_aware(end, dt.UTC)
        return start, end, self._validate_custom_range(start, end, now)
