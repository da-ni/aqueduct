import random
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from management import usage_rollup as rollup
from management.access import EveryOrg, Viewer, WholeOrg
from management.models import (
    KeyUsageWindow,
    Org,
    OrgUsageHour,
    Request,
    ServiceAccount,
    Team,
    TeamMembership,
    UserGroup,
)
from management.periods import HOUR, Period, Span
from management.request_log import read_filters
from management.tests.helpers import make_token, make_user, median_error
from management.usage_source import UsageSource

CHAT = "gpt-4.1-nano"
EMBEDDING = "text-embedding-ada-002"


class UsageSourceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.org = Org.objects.create(name="lab-org")
        other_org = Org.objects.create(name="other-org")
        team = Team.objects.create(name="NLP Lab", org=cls.org)
        cls.admin = make_user("admin", cls.org, UserGroup.ADMIN)
        cls.org_admin = make_user("org-admin", cls.org, UserGroup.ORG_ADMIN)
        cls.member = make_user("member", cls.org)
        TeamMembership.objects.create(user_profile=cls.member.profile, team=team)
        pipeline = ServiceAccount.objects.create(name="pipeline", team=team)
        cls.keys = [
            make_token(cls.member, "laptop"),
            make_token(cls.member, "pipeline", service_account=pipeline),
            make_token(cls.org_admin, "scripts"),
            make_token(make_user("foreigner", other_org), "foreign"),
        ]
        draw = random.Random(11)
        Request.objects.bulk_create(
            Request(
                token=draw.choice(cls.keys),
                model=draw.choice((CHAT, CHAT, EMBEDDING, "retired-model")),
                timestamp=cls.now - timedelta(minutes=draw.randrange(1, 35 * 24 * 60)),
                status_code=draw.choice((200, 200, 429, 400, 500, None)),
                response_time_ms=draw.choice((None, int(draw.lognormvariate(5, 1)))),
                input_tokens=(tokens := draw.randrange(0, 5000)),
                cached_input_tokens=draw.randrange(0, tokens + 1),
                output_tokens=draw.randrange(0, 2000),
            )
            for _ in range(1500)
        )

    def viewers(self):
        yield "org", Viewer.of(self.org_admin.profile)
        yield "all orgs", Viewer.of(self.admin.profile, lambda _: EveryOrg())
        yield "member", Viewer.of(self.member.profile)

    def test_the_rollups_give_the_raw_figures_across_the_sealed_boundary(self):
        rollup.refresh(self.now)
        rollup.refresh_key_windows(self.now)
        self.assertTrue(OrgUsageHour.objects.exists())
        self.assertTrue(KeyUsageWindow.objects.exists())
        for name, viewer in self.viewers():
            for span in Span:
                for model in ({}, {"model": CHAT}):
                    with self.subTest(extent=name, span=span, **model):
                        filters = read_filters(
                            viewer, {"span": span, **model}, default_span=span, now=self.now
                        )
                        source = UsageSource.of(filters)
                        raw = UsageSource(filters.requests, filters.period)
                        self.assertEqual(
                            source.approximate, name != "member" and span is not Span.HOUR
                        )
                        self.assertEqual(source.series(), raw.series())
                        self.assertEqual(source.key_totals(), raw.key_totals())
                        self.assertEqual(source.org_tokens(), raw.org_tokens())
                        self.assert_same_models(source, raw)

    def test_the_heatmap_gives_the_raw_figures_across_the_sealed_boundary(self):
        rollup.refresh(self.now - timedelta(hours=3))
        period = Period(self.now - timedelta(weeks=4), self.now, HOUR)
        for extent in (WholeOrg(self.org), EveryOrg()):
            for model in (None, CHAT):
                with self.subTest(extent=extent, model=model):
                    source = UsageSource.whole(extent, model, period)
                    self.assertEqual(source.heat(), UsageSource(source.requests, period).heat())

    def assert_same_models(self, source: UsageSource, raw: UsageSource):
        found, exact = source.models(), raw.models()
        self.assertEqual(
            {name: usage.activity for name, usage in found.items()},
            {name: usage.activity for name, usage in exact.items()},
        )
        for name, usage in found.items():
            if exact[name].median_ms is None:
                self.assertIsNone(usage.median_ms)
            else:
                difference = abs(usage.median_ms - exact[name].median_ms)
                self.assertLessEqual(difference, median_error(exact[name].median_ms))

    def test_an_org_without_window_rows_reads_only_the_requests_after_the_seal(self):
        rollup.refresh_key_windows(self.now)
        quiet_org = Org.objects.create(name="quiet-org")
        quiet = make_token(make_user("quiet", quiet_org), "quiet")
        busy = self.keys[2]
        sealed = KeyUsageWindow.objects.values_list("sealed_until", flat=True).first()
        for key in (quiet, busy):
            for at, tokens in ((sealed - timedelta(minutes=5), 1000), (sealed, 7)):
                Request.objects.create(
                    token=key, model=CHAT, timestamp=at, status_code=200, input_tokens=tokens
                )
        period = Period.last(Span.WEEK, self.now)
        for extent, key in ((WholeOrg(quiet_org), quiet), (WholeOrg(self.org), busy)):
            with self.subTest(org=extent.org.name):
                windowed = UsageSource.whole(extent, CHAT, period, key_windows=True)
                raw = UsageSource.whole(extent, CHAT, period)
                late = raw.key_totals()[key.pk].input - windowed.key_totals()[key.pk].input
                self.assertEqual(late, 1000)
