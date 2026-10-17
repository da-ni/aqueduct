import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from aqueduct.celery import delete_old_requests
from management import usage_rollup as rollup
from management.key_activity import Activity
from management.models import (
    KeyUsageWindow,
    Org,
    OrgLatencyHour,
    OrgUsageHour,
    Request,
    ServiceAccount,
    Team,
    Token,
    UsageRollupState,
    UserProfile,
)
from management.tests.test_api_keys_pages import PAGES, make_token
from management.usage_report import DAY, MEDIAN, TOKENS, TOTALS, Period, Span, org_requests, series

User = get_user_model()
CHAT = "gpt-4.1-nano"
EMBEDDING = "text-embedding-ada-002"
UNSERVED = "retired-model"
STATUSES = (200, 200, 302, 429, 404, 400, 500, 503, None)
MEDIAN_BOUND = 2 ** (1 / 16)


class ApproximateMedianTest(SimpleTestCase):
    def test_no_requests_have_no_median(self):
        self.assertIsNone(rollup.approximate_median({}))


@PAGES
class UsageRollupTest(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.org = Org.objects.create(name="lab-org")
        self.other_org = Org.objects.create(name="other-org")
        self.team = Team.objects.create(name="NLP Lab", org=self.org)
        self.other_team = Team.objects.create(name="Vision", org=self.other_org)
        self.member = self.user("member", self.org)
        self.foreigner = self.user("foreigner", self.other_org)
        self.member_key = make_token(self.member, "laptop")
        self.pipeline = ServiceAccount.objects.create(name="pipeline", team=self.team)
        self.pipeline_key = make_token(self.member, "pipeline", service_account=self.pipeline)
        self.foreign_key = make_token(self.foreigner, "foreign")
        self.orphan_key = make_token(User.objects.create_user(username="orphan"), "orphan")

        draw = random.Random(42)
        keys = (self.member_key, self.pipeline_key, self.foreign_key, self.orphan_key)
        requests = [
            Request(
                token=draw.choice(keys),
                model=draw.choice((CHAT, CHAT, EMBEDDING, UNSERVED, "")),
                timestamp=self.now - timedelta(minutes=draw.randrange(0, 40 * 24 * 60)),
                status_code=draw.choice(STATUSES),
                response_time_ms=draw.choice((None, 0, int(draw.lognormvariate(6, 2)))),
                input_tokens=(tokens := draw.randrange(0, 5000)),
                cached_input_tokens=draw.randrange(0, tokens + 1),
                output_tokens=draw.randrange(0, 2000),
            )
            for _ in range(1500)
        ]
        Request.objects.bulk_create(requests)

    def user(self, name: str, org: Org):
        user = User.objects.create_user(username=name, email=f"{name}@example.com")
        UserProfile.objects.create(user=user, org=org)
        return user

    def log(self, token: Token, ago: timedelta, **fields) -> Request:
        return Request.objects.create(
            token=token, model=CHAT, timestamp=self.now - ago, status_code=200, **fields
        )

    def scoped(self, org: Org | None, model: str | None):
        requests = org_requests(org)
        return requests if model is None else requests.filter(model=model)

    def assert_matches_raw(self, period: Period | None = None, now=None):
        now = now or self.now
        periods = [period] if period else [Period.last(span, now) for span in Span]
        for org in (self.org, self.other_org, None):
            for model in (None, CHAT):
                for each in periods:
                    with self.subTest(org=org, model=model, period=each.label):
                        self.assert_figures(org, model, each, now)

    def assert_figures(self, org, model, period: Period, now):
        requests = self.scoped(org, model)
        self.assertEqual(rollup.org_series(org, model, period), series(requests, period))

        raw_models = {
            row.pop("model"): row
            for row in period.of(requests).values("model").annotate(**TOTALS, median=MEDIAN)
        }
        found = rollup.org_models(org, model, period)
        self.assertEqual(
            {name: usage.activity for name, usage in found.items()},
            {name: Activity(**{f: row[f] for f in TOTALS}) for name, row in raw_models.items()},
        )
        for name, usage in found.items():
            exact = raw_models[name]["median"]
            if exact is None:
                self.assertIsNone(usage.median_ms)
            else:
                self.assertLessEqual(
                    abs(usage.median_ms - exact), max(1, (MEDIAN_BOUND - 1) * exact)
                )

        start = now - timedelta(weeks=4)
        raw_heat = {
            (row["day"], row["at"]): row["tokens"]
            for row in self.scoped(org, None)
            .filter(timestamp__gte=start, timestamp__lt=now)
            .values(day=rollup.ExtractIsoWeekDay("timestamp"), at=rollup.ExtractHour("timestamp"))
            .annotate(tokens=rollup.Sum(TOKENS))
            .order_by()
        }
        self.assertEqual(rollup.org_heat(org, start, now), raw_heat)

    def rollup_rows(self):
        return (
            sorted(OrgUsageHour.objects.values_list(*self.fields(OrgUsageHour)), key=str),
            sorted(OrgLatencyHour.objects.values_list(*self.fields(OrgLatencyHour)), key=str),
        )

    def fields(self, model):
        return [f.attname for f in model._meta.fields if f.name != "id"]

    def test_without_rollups_the_helpers_read_the_requests(self):
        self.assertFalse(UsageRollupState.objects.exists())
        self.assert_matches_raw()

    def test_rollups_give_the_raw_figures(self):
        rollup.refresh(self.now)
        self.assertTrue(OrgUsageHour.objects.filter(org=None).exists(), "orphan key counted")
        self.assert_matches_raw()

    def test_sealed_hours_are_read_from_the_rollups(self):
        rollup.refresh(self.now)
        period = Period.last(Span.WEEK, self.now)
        before = rollup.org_series(self.org, None, period).total
        Request.objects.filter(timestamp__lt=self.now - timedelta(hours=2)).update(output_tokens=0)
        self.assertEqual(rollup.org_series(self.org, None, period).total, before)

    def test_refreshing_twice_keeps_the_same_rows(self):
        rollup.refresh(self.now)
        first = self.rollup_rows()
        rollup.refresh(self.now)
        self.assertEqual(self.rollup_rows(), first)
        self.assertTrue(first[0] and first[1])

    def test_a_late_status_change_is_picked_up_by_the_next_refresh(self):
        late = self.log(self.member_key, timedelta(hours=3), input_tokens=10, response_time_ms=100)
        rollup.refresh(self.now)
        late.status_code, late.input_tokens, late.output_tokens = 499, 1234, 55
        late.save()
        rollup.refresh(self.now + timedelta(minutes=10))
        self.assert_matches_raw()

    def test_deleting_a_key_marks_the_rollups_dirty_and_the_refresh_rebuilds(self):
        rollup.refresh(self.now)
        self.foreign_key.delete()
        self.assertTrue(UsageRollupState.objects.get(name=rollup.HOURLY).dirty)
        rollup.refresh(self.now)
        self.assertFalse(UsageRollupState.objects.get(name=rollup.HOURLY).dirty)
        self.assert_matches_raw()

    def test_moving_an_owner_to_another_org_marks_the_rollups_dirty(self):
        rollup.refresh(self.now)
        profile = self.foreigner.profile
        profile.save()
        self.assertFalse(UsageRollupState.objects.get(name=rollup.HOURLY).dirty)
        profile.org = self.org
        profile.save()
        self.assertTrue(UsageRollupState.objects.get(name=rollup.HOURLY).dirty)
        rollup.refresh(self.now)
        self.assert_matches_raw()

    def test_moving_a_service_account_or_team_marks_the_rollups_dirty(self):
        for instance, field, unchanged, moved in (
            (self.pipeline, "team", self.team, self.other_team),
            (self.team, "org", self.org, self.other_org),
        ):
            with self.subTest(field=field):
                rollup.refresh(self.now)
                setattr(instance, field, unchanged)
                instance.save()
                self.assertFalse(UsageRollupState.objects.get(name=rollup.HOURLY).dirty)
                setattr(instance, field, moved)
                instance.save()
                self.assertTrue(UsageRollupState.objects.get(name=rollup.HOURLY).dirty)

    def test_hours_follow_the_local_time_zone(self):
        rollup.refresh(self.now)
        with override_settings(TIME_ZONE="Asia/Kolkata"):
            self.assert_matches_raw()
            rollup.refresh(self.now)
            state = UsageRollupState.objects.get(name=rollup.HOURLY)
            self.assertEqual(state.time_zone, "Asia/Kolkata")
            self.assertEqual(timezone.localtime(state.sealed_until).minute, 0)
            self.assert_matches_raw()

    @override_settings(REQUEST_RETENTION_DAYS=30)
    def test_retention_leaves_no_difference_at_the_cutoff(self):
        rollup.refresh(self.now)
        now = timezone.now()
        delete_old_requests()
        self.assertFalse(Request.objects.filter(timestamp__lt=now - timedelta(days=30)).exists())
        across_cutoff = Period(
            DAY.floor(now - timedelta(days=32)), DAY.floor(now) + DAY.size, DAY, "cutoff"
        )
        self.assert_matches_raw(across_cutoff, now)

    def test_key_windows_give_each_keys_raw_activity(self):
        rollup.refresh_key_windows(self.now)
        self.assertTrue(KeyUsageWindow.objects.exists())
        for span in Span:
            period = Period.last(span, self.now)
            for tokens in (None, Token.objects.filter(user=self.member)):
                for model in (None, CHAT):
                    with self.subTest(span=span, tokens=tokens, model=model):
                        requests = self.scoped(None, model)
                        if tokens is not None:
                            requests = requests.filter(token__in=tokens)
                        raw = {
                            row.pop("token_id"): Activity(**row)
                            for row in period.of(requests)
                            .values("token_id")
                            .annotate(**TOTALS)
                            .order_by()
                        }
                        self.assertEqual(rollup.key_window_totals(tokens, model, period), raw)

    def test_key_windows_are_read_where_they_start_with_the_period(self):
        rollup.refresh_key_windows(self.now)
        period = Period.last(Span.WEEK, self.now)
        before = rollup.key_window_totals(None, None, period)
        Request.objects.filter(timestamp__lt=self.now - timedelta(hours=2)).update(output_tokens=0)
        self.assertEqual(rollup.key_window_totals(None, None, period), before)

    def test_deleting_a_key_drops_its_windows(self):
        rollup.refresh_key_windows(self.now)
        self.foreign_key.delete()
        self.assertFalse(KeyUsageWindow.objects.filter(token_id=self.foreign_key.pk).exists())
