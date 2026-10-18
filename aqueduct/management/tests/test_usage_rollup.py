import random
from datetime import UTC, datetime, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from aqueduct.celery import delete_old_requests
from management import usage_rollup as rollup
from management.access import EveryOrg, KeyAct, OrgWide, Viewer, WholeOrg, requests_of
from management.activity import MEDIAN, TOKENS, TOTALS, Activity, series, served
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
    UserGroup,
    UserProfile,
)
from management.periods import DAY, Period, Span
from management.signals import mark_rollups_dirty
from management.tests.helpers import make_token, make_user, median_error

User = get_user_model()
CHAT = "gpt-4.1-nano"
EMBEDDING = "text-embedding-ada-002"
UNSERVED = "retired-model"
STATUSES = (200, 200, 302, 429, 404, 400, 500, 503, None)


class ApproximateMedianTest(TestCase):
    def test_no_requests_have_no_median(self):
        self.assertIsNone(rollup.approximate_median({}))

    def test_the_median_is_within_five_percent_or_two_ms(self):
        token = make_token(make_user("timer", Org.objects.create(name="timing")), "timer")
        Request.objects.bulk_create(
            Request(token=token, timestamp=timezone.now(), response_time_ms=ms)
            for ms in range(5000)
        )
        for ms, bucket in Request.objects.values_list("response_time_ms", rollup.BUCKET):
            with self.subTest(ms=ms):
                approximate = rollup.approximate_median({bucket: 1})
                self.assertLessEqual(abs(approximate - ms), median_error(ms))


class SplitTest(SimpleTestCase):
    start = datetime(2026, 10, 11, tzinfo=UTC)
    end = datetime(2026, 10, 18, tzinfo=UTC)
    sealed = datetime(2026, 10, 17, 14, tzinfo=UTC)

    def raw(self, low: datetime, high: datetime) -> Q:
        return Q(timestamp__gte=low, timestamp__lt=high)

    def test_a_start_on_the_hour_leaves_one_raw_range(self):
        hours, rest = rollup._split(self.start, self.end, self.sealed)
        self.assertEqual(hours, Q(hour__gte=self.start, hour__lt=self.sealed))
        self.assertEqual(rest, self.raw(self.sealed, self.end))

    def test_a_start_within_an_hour_reads_the_rest_of_that_hour_raw(self):
        start = self.start + timedelta(minutes=23)
        hours, rest = rollup._split(start, self.end, self.sealed)
        first = self.start + timedelta(hours=1)
        self.assertEqual(hours, Q(hour__gte=first, hour__lt=self.sealed))
        self.assertEqual(rest, self.raw(start, first) | self.raw(self.sealed, self.end))

    def test_rollups_up_to_the_end_leave_no_raw_range(self):
        hours, rest = rollup._split(self.start, self.sealed, self.sealed)
        self.assertEqual(hours, Q(hour__gte=self.start, hour__lt=self.sealed))
        self.assertIsNone(rest)

    def test_without_rollups_everything_is_raw(self):
        self.assertEqual(
            rollup._split(self.start, self.end, None), (None, self.raw(self.start, self.end))
        )


class UsageRollupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.org = Org.objects.create(name="lab-org")
        cls.other_org = Org.objects.create(name="other-org")
        cls.team = Team.objects.create(name="NLP Lab", org=cls.org)
        cls.other_team = Team.objects.create(name="Vision", org=cls.other_org)
        cls.member = make_user("member", cls.org)
        cls.foreigner = make_user("foreigner", cls.other_org)
        cls.member_key = make_token(cls.member, "laptop")
        cls.pipeline = ServiceAccount.objects.create(name="pipeline", team=cls.team)
        cls.pipeline_key = make_token(cls.member, "pipeline", service_account=cls.pipeline)
        cls.foreign_key = make_token(cls.foreigner, "foreign")
        cls.orphan_key = make_token(User.objects.create_user(username="orphan"), "orphan")

        draw = random.Random(42)
        keys = (cls.member_key, cls.pipeline_key, cls.foreign_key, cls.orphan_key)
        requests = [
            Request(
                token=draw.choice(keys),
                model=draw.choice((CHAT, CHAT, EMBEDDING, UNSERVED, "")),
                timestamp=cls.now - timedelta(minutes=draw.randrange(0, 40 * 24 * 60)),
                status_code=draw.choice(STATUSES),
                response_time_ms=draw.choice((None, 0, int(draw.lognormvariate(6, 2)))),
                input_tokens=(tokens := draw.randrange(0, 5000)),
                cached_input_tokens=draw.randrange(0, tokens + 1),
                output_tokens=draw.randrange(0, 2000),
            )
            for _ in range(1500)
        ]
        Request.objects.bulk_create(requests)

    def log(self, token: Token, ago: timedelta, **fields) -> Request:
        fields.setdefault("model", CHAT)
        return Request.objects.create(
            token=token, timestamp=self.now - ago, status_code=200, **fields
        )

    def scoped(self, extent: OrgWide, model: str | None):
        requests = served(requests_of(extent))
        return requests if model is None else requests.filter(model=model)

    def assert_matches_raw(self, period: Period | None = None, now=None):
        now = now or self.now
        periods = [period] if period else [Period.last(span, now) for span in Span]
        for extent in (WholeOrg(self.org), WholeOrg(self.other_org), EveryOrg()):
            for model in (None, CHAT):
                for each in periods:
                    with self.subTest(extent=extent, model=model, period=each.start):
                        self.assert_figures(extent, model, each, now)

    def assert_figures(self, extent, model, period: Period, now):
        requests = self.scoped(extent, model)
        self.assertEqual(rollup.org_series(extent, model, period), series(requests, period))

        raw_models = {
            row.pop("model"): row
            for row in period.of(requests).values("model").annotate(**TOTALS, median=MEDIAN)
        }
        found = rollup.org_models(extent, model, period)
        self.assertEqual(
            {name: usage.activity for name, usage in found.items()},
            {name: Activity(**{f: row[f] for f in TOTALS}) for name, row in raw_models.items()},
        )
        for name, usage in found.items():
            exact = raw_models[name]["median"]
            if exact is None:
                self.assertIsNone(usage.median_ms)
            else:
                self.assertLessEqual(abs(usage.median_ms - exact), median_error(exact))

        start = now - timedelta(weeks=4)
        self.assertEqual(
            rollup.org_heat(extent, start, now, model=model),
            self.raw_heat(extent, model, start, now),
        )

    def raw_heat(self, extent, model, start, end) -> dict[tuple[int, int], int]:
        return {
            (row["day"], row["at"]): row["tokens"]
            for row in self.scoped(extent, model)
            .filter(timestamp__gte=start, timestamp__lt=end)
            .values(day=rollup.ExtractIsoWeekDay("timestamp"), at=rollup.ExtractHour("timestamp"))
            .annotate(tokens=rollup.Sum(TOKENS))
            .order_by()
        }

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

    def test_the_heatmap_of_one_model_adds_the_raw_tail_to_the_rollups(self):
        rollup.refresh(self.now - timedelta(hours=3))
        for ago in (timedelta(minutes=5), timedelta(hours=2)):
            self.log(self.member_key, ago, input_tokens=7000, output_tokens=11)
            self.log(self.member_key, ago, input_tokens=9000, model=EMBEDDING)
        sealed = rollup.Sealed.read()
        self.assertLess(sealed.until, self.now - timedelta(hours=2))
        start = self.now - timedelta(weeks=4)
        self.assertTrue(OrgUsageHour.objects.filter(model=CHAT, hour__gte=start).exists())
        for extent in (WholeOrg(self.org), EveryOrg()):
            with self.subTest(extent=extent):
                self.assertEqual(
                    rollup.org_heat(extent, start, self.now, sealed, model=CHAT),
                    self.raw_heat(extent, CHAT, start, self.now),
                )

    def test_a_period_the_rollups_cover_entirely_gives_the_raw_figures(self):
        rollup.refresh(self.now)
        self.assert_matches_raw(Period.last(Span.WEEK, self.now - timedelta(days=2)))

    def test_sealed_hours_are_read_from_the_rollups(self):
        rollup.refresh(self.now)
        period = Period.last(Span.WEEK, self.now)
        before = rollup.org_series(WholeOrg(self.org), None, period).total
        Request.objects.filter(timestamp__lt=self.now - timedelta(hours=2)).update(output_tokens=0)
        self.assertEqual(rollup.org_series(WholeOrg(self.org), None, period).total, before)

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

    def dirty(self) -> bool:
        return UsageRollupState.objects.get(name=rollup.HOURLY).dirty

    def test_deleting_a_key_marks_the_rollups_dirty_and_the_refresh_rebuilds(self):
        rollup.refresh(self.now)
        with self.captureOnCommitCallbacks(execute=True):
            self.foreign_key.delete()
        self.assertTrue(self.dirty())
        rollup.refresh(self.now)
        self.assertFalse(self.dirty())
        self.assert_matches_raw()

    def test_the_rollups_turn_dirty_only_once_the_change_commits(self):
        rollup.refresh(self.now)
        with self.captureOnCommitCallbacks() as pending:
            self.foreign_key.delete()
        self.assertFalse(self.dirty())
        for callback in pending:
            callback()
        self.assertTrue(self.dirty())

    def test_a_change_marked_during_a_refresh_keeps_the_rollups_dirty(self):
        rollup.refresh(self.now)
        insert = rollup._insert

        def insert_then_mark(*args, **kwargs):
            insert(*args, **kwargs)
            mark_rollups_dirty()

        with mock.patch.object(rollup, "_insert", insert_then_mark):
            rollup.refresh(self.now)
        self.assertTrue(self.dirty())
        rollup.refresh(self.now)
        self.assertFalse(self.dirty())

    def test_moving_an_owner_to_another_org_marks_the_rollups_dirty(self):
        rollup.refresh(self.now)
        profile = self.foreigner.profile
        with self.captureOnCommitCallbacks(execute=True):
            profile.save()
        self.assertFalse(self.dirty())
        profile.org = self.org
        with self.captureOnCommitCallbacks(execute=True):
            profile.save()
        self.assertTrue(self.dirty())
        rollup.refresh(self.now)
        self.assert_matches_raw()

    def test_giving_a_key_owner_a_profile_marks_the_rollups_dirty(self):
        rollup.refresh(self.now)
        with self.captureOnCommitCallbacks(execute=True):
            UserProfile.objects.create(user=self.orphan_key.user, org=self.org)
        self.assertTrue(self.dirty())
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
                with self.captureOnCommitCallbacks(execute=True):
                    instance.save()
                self.assertFalse(self.dirty())
                setattr(instance, field, moved)
                with self.captureOnCommitCallbacks(execute=True):
                    instance.save()
                self.assertTrue(self.dirty())
                rollup.refresh(self.now)
                self.assert_matches_raw()

    def test_a_service_account_key_counts_for_its_teams_org_only(self):
        owner_in_a = self.member
        sa = ServiceAccount.objects.create(name="vision-bot", team=self.other_team)
        key = make_token(owner_in_a, "vision-bot", service_account=sa)
        self.log(key, timedelta(days=2), input_tokens=777, response_time_ms=50)
        rollup.refresh(self.now)

        org_admin = make_user("org-admin", self.org)
        org_admin.profile.group = UserGroup.ORG_ADMIN
        org_admin.profile.save()
        self.assertNotIn(key, Viewer.of(org_admin.profile).keys(KeyAct.SEE))
        self.assertFalse(requests_of(WholeOrg(self.org)).filter(token=key).exists())
        self.assertTrue(requests_of(WholeOrg(self.other_org)).filter(token=key).exists())
        week = Period.last(Span.WEEK, self.now)
        rollup.refresh_key_windows(self.now)
        totals = rollup.key_window_totals(WholeOrg(self.other_org), None, week)
        self.assertEqual(totals[key.pk].input, 777)
        self.assert_matches_raw()

    @override_settings(TIME_ZONE="Australia/Lord_Howe")
    def test_zones_whose_hours_shift_in_the_year_read_the_requests(self):
        rollup.refresh(self.now)
        self.assertFalse(OrgUsageHour.objects.exists())
        self.assert_matches_raw()

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
        across_cutoff = Period(DAY.floor(now - timedelta(days=32)), DAY.floor(now) + DAY.size, DAY)
        self.assert_matches_raw(across_cutoff, now)

    def test_key_windows_give_each_keys_raw_activity(self):
        rollup.refresh_key_windows(self.now)
        self.assertTrue(KeyUsageWindow.objects.exists())
        for span in Span:
            period = Period.last(span, self.now)
            for extent in (EveryOrg(), WholeOrg(self.org)):
                for model in (None, CHAT):
                    with self.subTest(span=span, extent=extent, model=model):
                        requests = self.scoped(extent, model)
                        raw = {
                            row.pop("token_id"): Activity(**row)
                            for row in period.of(requests)
                            .values("token_id")
                            .annotate(**TOTALS)
                            .order_by()
                        }
                        self.assertEqual(rollup.key_window_totals(extent, model, period), raw)

    def test_key_windows_are_read_where_they_start_with_the_period(self):
        rollup.refresh_key_windows(self.now)
        period = Period.last(Span.WEEK, self.now)
        before = rollup.key_window_totals(EveryOrg(), None, period)
        Request.objects.filter(timestamp__lt=self.now - timedelta(hours=2)).update(output_tokens=0)
        self.assertEqual(rollup.key_window_totals(EveryOrg(), None, period), before)

    def test_a_refresh_between_the_reads_counts_nothing_twice(self):
        just_sealed = rollup.floor_hour(self.now - rollup.SETTLE) + timedelta(seconds=1)
        self.log(self.member_key, self.now - just_sealed, input_tokens=99)
        rollup.refresh_key_windows(self.now)
        period = Period.last(Span.WEEK, self.now)
        first, raced = QuerySet.first, []

        def first_then_refresh(rows):
            found = first(rows)
            if rows.model is UsageRollupState and not raced:
                raced.append(True)
                rollup.refresh_key_windows(self.now + timedelta(hours=1))
            return found

        with mock.patch.object(QuerySet, "first", first_then_refresh):
            totals = rollup.key_window_totals(EveryOrg(), None, period)
        self.assertTrue(raced)
        raw = {
            row.pop("token_id"): Activity(**row)
            for row in period.of(served(Request.objects.all()))
            .values("token_id")
            .annotate(**TOTALS)
            .order_by()
        }
        self.assertEqual(totals, raw)

    def test_the_next_refresh_drops_a_deleted_keys_windows(self):
        rollup.refresh_key_windows(self.now)
        gone = self.foreign_key.pk
        self.foreign_key.delete()
        rollup.refresh_key_windows(self.now)
        self.assertFalse(KeyUsageWindow.objects.filter(token_id=gone).exists())

    def test_rows_left_for_a_deleted_key_or_org_are_not_read(self):
        org = Org.objects.create(name="short-lived")
        account = ServiceAccount.objects.create(
            name="gone", team=Team.objects.create(name="gone", org=org)
        )
        key = make_token(self.member, "gone", service_account=account)
        self.log(key, timedelta(hours=3), input_tokens=500, response_time_ms=40)
        rollup.refresh(self.now)
        rollup.refresh_key_windows(self.now)
        key_id, org_id = key.pk, org.pk
        org.delete()
        self.assertTrue(KeyUsageWindow.objects.filter(token_id=key_id).exists())
        self.assertTrue(OrgUsageHour.objects.filter(org_id=org_id).exists())
        self.assertTrue(OrgLatencyHour.objects.filter(org_id=org_id).exists())

        self.assert_matches_raw()
        period = Period.last(Span.WEEK, self.now)
        self.assertNotIn(org_id, rollup.org_tokens(None, period))
        raw = {
            row.pop("token_id"): Activity(**row)
            for row in period.of(served(Request.objects.all()))
            .values("token_id")
            .annotate(**TOTALS)
        }
        self.assertEqual(rollup.key_window_totals(EveryOrg(), None, period), raw)
