import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from management import usage_report as report
from management import usage_rollup as rollup
from management.models import (
    KeyUsageWindow,
    Org,
    OrgUsageHour,
    Request,
    ServiceAccount,
    Team,
    TeamMembership,
    Token,
    UsageRollupState,
    UserGroup,
)
from management.request_log import Key
from management.tests.helpers import PAGES, make_token, make_user

User = get_user_model()
CHAT = "gpt-4.1-nano"
EMBEDDING = "text-embedding-ada-002"
MEDIAN_BOUND = 2 ** (1 / 16)


def named(token) -> Key:
    return Key(token, None, "")


@PAGES
class UsageFromRollupsTest(TestCase):
    """The Overview and All keys pages read the rollups when the viewer sees a whole org, and
    show the figures the requests give."""

    def setUp(self):
        self.now = timezone.now()
        self.org = Org.objects.create(name="lab-org")
        self.other_org = Org.objects.create(name="other-org")
        self.team = Team.objects.create(name="NLP Lab", org=self.org)
        self.admin = make_user("admin", self.org, UserGroup.ADMIN)
        self.org_admin = make_user("org-admin", self.org, UserGroup.ORG_ADMIN)
        self.member = make_user("member", self.org)
        TeamMembership.objects.create(user_profile=self.member.profile, team=self.team)
        foreigner = make_user("foreigner", self.other_org)
        pipeline = ServiceAccount.objects.create(name="pipeline", team=self.team)
        keys = [
            make_token(self.member, "laptop"),
            make_token(self.member, "pipeline", service_account=pipeline),
            make_token(self.org_admin, "Scripts"),
            make_token(foreigner, "foreign"),
            *(make_token(self.org_admin, f"batch-{n:02d}") for n in range(12)),
        ]
        draw = random.Random(7)
        Request.objects.bulk_create(
            Request(
                token=draw.choice(keys),
                model=draw.choice((CHAT, CHAT, EMBEDDING, "retired-model")),
                timestamp=self.now - timedelta(minutes=draw.randrange(1, 35 * 24 * 60)),
                status_code=draw.choice((200, 200, 200, 429, 400, 404, 500, 502, None)),
                response_time_ms=draw.choice((None, int(draw.lognormvariate(5, 1)))),
                input_tokens=(tokens := draw.randrange(0, 5000)),
                cached_input_tokens=draw.randrange(0, tokens + 1),
                output_tokens=draw.randrange(0, 2000),
            )
            for _ in range(1200)
        )

    def get(self, user, name: str, **params):
        self.client.force_login(user)
        response = self.client.get(reverse(name), params)
        self.assertEqual(response.status_code, 200)
        return response.context

    def refresh(self):
        rollup.refresh()
        rollup.refresh_key_windows()
        self.assertTrue(OrgUsageHour.objects.exists())
        self.assertTrue(KeyUsageWindow.objects.exists())

    def org_wide_views(self):
        for user, org in ((self.org_admin, {}), (self.admin, {}), (self.admin, {"org": "all"})):
            for span in ("1d", "1w", "1m"):
                for model in ({}, {"model": CHAT}):
                    yield user, {**org, "span": span, **model}

    def test_org_wide_overview_gives_the_raw_figures(self):
        self.refresh()
        for user, params in self.org_wide_views():
            with self.subTest(user=user.username, **params):
                page = self.get(user, "usage", **params)
                filters, period = page["filters"], page["period"]
                in_period = period.of(filters.requests)
                timeline = report.series(filters.requests, period)
                self.assertEqual(
                    page["kpis"], report.kpis(report.slots(timeline.points, period.step))
                )
                self.assertEqual(
                    page["chart"], report.columns(timeline.points, period.step, "tokens")
                )
                self.assertEqual(page["statuses"], report.statuses(timeline, filters.log_url))

                if filters.org is None:
                    self.assertEqual(
                        page["orgs_usage"], report.org_shares(report.org_tokens(in_period))
                    )
                    self.assertTrue(page["orgs_usage"])

                exact = report.model_usage(in_period)
                self.assertEqual(
                    {row.name: row.activity for row in page["models"]},
                    {name: usage.activity for name, usage in exact.items()},
                )
                for row in page["models"]:
                    shown = int(row.median.removesuffix(" ms"))
                    self.assertLessEqual(shown, exact[row.name].median_ms * MEDIAN_BOUND)
                    self.assertGreaterEqual(shown, exact[row.name].median_ms / MEDIAN_BOUND)

                self.assertEqual(
                    [(key.name, key.activity, key.share) for key in page["keys"]],
                    [
                        (key.name, key.activity, key.share)
                        for key in report.key_shares(
                            report.top_keys(in_period), timeline.total.tokens, named
                        )
                    ],
                )

    def test_org_wide_overview_opens_the_log_on_the_same_requests(self):
        self.refresh()
        for user, params in self.org_wide_views():
            with self.subTest(user=user.username, **params):
                page = self.get(user, "usage", **params)
                figures = [(row.href, row.count) for row in page["statuses"]]
                figures += [(row.log_url, row.activity.requests) for row in page["models"]]
                figures.append((page["filters"].log_url(), page["requests_in_period"]))
                for href, count in figures:
                    self.client.force_login(user)
                    self.assertEqual(self.client.get(href).context["log"].count, count, href)

    def test_heatmap_gives_the_raw_figures(self):
        self.refresh()
        for user, params in ((self.member, {}), (self.org_admin, {}), (self.admin, {"org": "all"})):
            with self.subTest(user=user.username, **params):
                page = self.get(user, "usage", **params)
                weeks = page["heat_days"] // 7
                raw = {
                    (row["day"], row["at"]): row["tokens"]
                    for row in report.org_requests(page["heat_org"])
                    .filter(timestamp__gte=timezone.now() - timedelta(weeks=weeks))
                    .values(
                        day=rollup.ExtractIsoWeekDay("timestamp"),
                        at=rollup.ExtractHour("timestamp"),
                    )
                    .annotate(tokens=rollup.Sum(report.TOKENS))
                    .order_by()
                }
                self.assertEqual(page["heat"], report.heat(raw, weeks))

    def test_heatmap_is_cached_while_the_rollups_lag_behind(self):
        cache.clear()
        rollup.refresh(self.now - timedelta(hours=3))
        heat = self.get(self.org_admin, "usage")["heat"]
        Request.objects.create(
            token=Token.objects.get(name="Scripts"),
            model=CHAT,
            timestamp=timezone.now(),
            status_code=200,
            input_tokens=10**7,
        )
        self.assertEqual(self.get(self.org_admin, "usage")["heat"], heat)
        rollup.refresh()
        self.assertNotEqual(self.get(self.org_admin, "usage")["heat"], heat)

    def test_all_keys_gives_the_raw_figures_in_every_order(self):
        self.refresh()
        for user, params in self.org_wide_views():
            for extra in ({}, {"sort": "requests"}, {"sort": "name"}, {"q": "batch"}):
                with self.subTest(user=user.username, **params, **extra):
                    page = self.get(user, "usage_keys", **params, **extra)
                    filters = page["filters"]
                    in_period = filters.period.of(filters.requests)
                    listed = in_period.filter(token__name__contains=extra.get("q", ""))
                    order = report.KeyOrder.parse(extra.get("sort", "")) or report.KeyOrder.TOKENS
                    rows = report.CountedKeys(
                        report.ranked_keys(listed, order), report.key_totals(listed).keys
                    )
                    everyone = report.key_totals(in_period)
                    expected = report.key_shares(rows[:], everyone.tokens, named, everyone.busiest)
                    self.assertEqual(page["count"], len(expected))
                    self.assertEqual(
                        [(k.name, k.activity, k.share, k.width) for k in page["keys"]],
                        [(k.name, k.activity, k.share, k.width) for k in expected],
                    )

    def test_whole_org_figures_come_from_the_rollups_and_the_rest_from_the_requests(self):
        self.refresh()
        views = {
            "org": (self.org_admin, {"span": "1w"}),
            "org, mine": (self.org_admin, {"scope": "mine", "span": "1w"}),
            "member": (self.member, {"span": "1w"}),
        }

        def requests() -> dict[str, tuple[int, int]]:
            return {
                name: (
                    self.get(user, "usage", **params)["requests_in_period"],
                    sum(
                        k.activity.requests for k in self.get(user, "usage_keys", **params)["keys"]
                    ),
                )
                for name, (user, params) in views.items()
            }

        before = requests()
        # Deleting sealed requests behind the rollups' back shows which figures read them. Requests
        # after the earliest sealed hour are still read raw, so they stay.
        sealed = min(UsageRollupState.objects.values_list("sealed_until", flat=True))
        Request.objects.filter(timestamp__lt=sealed).delete()
        after = requests()
        self.assertEqual(after["org"], before["org"])
        for name in ("org, mine", "member"):
            self.assertLess(after[name], before[name], name)

    def test_only_whole_org_medians_are_marked_approximate(self):
        self.refresh()
        for user, params, approximate in (
            (self.org_admin, {}, True),
            (self.admin, {"org": "all"}, True),
            (self.org_admin, {"scope": "mine"}, False),
            (self.org_admin, {"span": "1h"}, False),
            (self.member, {}, False),
        ):
            with self.subTest(user=user.username, **params):
                self.client.force_login(user)
                response = self.client.get(reverse("usage"), params)
                rows = [row for row in response.context["models"] if row.median != "n/a"]
                self.assertTrue(rows)
                self.assertEqual({row.approximate for row in rows}, {approximate})
                html = response.content.decode()
                self.assertEqual(str(report.APPROXIMATE_MEDIAN) in html, approximate)
                self.assertEqual("approximately </span>" in html, approximate)
