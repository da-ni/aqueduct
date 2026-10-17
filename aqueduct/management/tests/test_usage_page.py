import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from management.models import (
    Org,
    Request,
    ServiceAccount,
    Team,
    TeamMembership,
    Token,
    UserGroup,
    UserProfile,
)
from management.tests.test_api_keys_pages import PAGES, make_token

User = get_user_model()
CHAT = "gpt-4.1-nano"
EMBEDDING = "text-embedding-ada-002"
KEY_NAMES = ("solo-laptop", "member-laptop", "pipeline", "foreign-laptop")


@PAGES
class UsagePageTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Org.objects.create(name="lab-org")
        self.other_org = Org.objects.create(name="other-org")
        self.team = Team.objects.create(name="NLP Lab", org=self.org)

        self.admin = self.user("admin", UserGroup.ADMIN)
        self.org_admin = self.user("org-admin", UserGroup.ORG_ADMIN)
        self.team_admin = self.user("team-admin")
        self.member = self.user("member")
        self.solo = self.user("solo")
        self.other_org_admin = self.user("other-org-admin", UserGroup.ORG_ADMIN, self.other_org)
        self.foreigner = self.user("foreigner", org=self.other_org)
        TeamMembership.objects.create(
            user_profile=self.team_admin.profile, team=self.team, is_admin=True
        )
        TeamMembership.objects.create(user_profile=self.member.profile, team=self.team)

        self.solo_key = make_token(self.solo, "solo-laptop")
        self.member_key = make_token(self.member, "member-laptop")
        sa = ServiceAccount.objects.create(name="pipeline", team=self.team)
        self.pipeline_key = make_token(self.team_admin, "pipeline", service_account=sa)
        self.foreign_key = make_token(self.foreigner, "foreign-laptop")

        hour = timedelta(hours=1)
        self.log(
            self.solo_key, hour, 200, input_tokens=1000, cached_input_tokens=250, output_tokens=500
        )
        self.log(self.member_key, hour, 429, input_tokens=2000)
        self.log(self.pipeline_key, hour, 500, input_tokens=3000, output_tokens=1000)
        self.log(self.foreign_key, hour, 200, input_tokens=7000)

    def user(self, name: str, group: UserGroup = UserGroup.USER, org: Org | None = None):
        user = User.objects.create_user(username=name, email=f"{name}@example.com")
        UserProfile.objects.create(user=user, org=org or self.org).group = group
        return user

    def log(
        self, token: Token, ago: timedelta, status: int, model: str = CHAT, **fields
    ) -> Request:
        return Request.objects.create(
            token=token,
            timestamp=timezone.now() - ago,
            status_code=status,
            model=model,
            response_time_ms=100,
            **fields,
        )

    def page(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("usage"), params)

    def kpi(self, response, label: str) -> str:
        match = re.search(
            rf'{label}</span>\s*<span class="text-2xl font-semibold">([^<]*)</span>',
            response.content.decode(),
        )
        self.assertIsNotNone(match, f"no {label} figure on the page")
        return match.group(1)

    def status_count(self, response, label: str) -> str:
        match = re.search(
            rf'<span class="font-medium">{label}</span>.*?'
            r'<span class="ml-auto font-mono">([^<]*)</span>',
            response.content.decode(),
            re.S,
        )
        self.assertIsNotNone(match, f"no {label} row on the page")
        return match.group(1)

    def assert_keys(self, response, *visible: str) -> None:
        self.assertEqual(response.status_code, 200)
        for name in KEY_NAMES:
            if name in visible:
                self.assertContains(response, name)
            else:
                self.assertNotContains(response, name)


class UsageAccessTest(UsagePageTestBase):
    def test_signed_out_goes_to_sign_in(self):
        url = reverse("usage")
        self.assertRedirects(
            self.client.get(url), f"{reverse('sso')}?next={url}", fetch_redirect_response=False
        )

    def test_user_sees_only_their_own_key(self):
        response = self.page(self.solo)
        self.assert_keys(response, "solo-laptop")
        self.assertEqual(self.kpi(response, "Requests"), "1")
        self.assertContains(response, f'href="{reverse("token_detail", args=[self.solo_key.id])}"')
        self.assertNotContains(response, "My keys")
        self.assertNotContains(response, "All organizations")

    def test_team_member_sees_their_key_and_the_team_service_account(self):
        response = self.page(self.member)
        self.assert_keys(response, "member-laptop", "pipeline")
        self.assertContains(
            response, f'href="{reverse("token_detail", args=[self.pipeline_key.id])}"'
        )
        self.assertNotContains(response, f"key={self.solo_key.id}")

    def test_org_admin_sees_their_org_but_links_no_other_users_key(self):
        response = self.page(self.org_admin)
        self.assert_keys(response, "solo-laptop", "member-laptop", "pipeline")
        self.assertEqual(self.kpi(response, "Requests"), "3")
        self.assertContains(
            response, f'href="{reverse("token_detail", args=[self.pipeline_key.id])}"'
        )
        for key in (self.solo_key, self.member_key):
            self.assertNotContains(response, reverse("token_detail", args=[key.id]))

    def test_my_keys_narrows_an_org_admin_to_their_own_keys(self):
        response = self.page(self.org_admin, scope="mine")
        self.assert_keys(response)
        self.assertEqual(self.kpi(response, "Requests"), "0")

    def test_admin_sees_every_org_split_by_org(self):
        response = self.page(self.admin, org="all")
        self.assert_keys(response, *KEY_NAMES)
        self.assertContains(response, "By organization")
        self.assertContains(response, "other-org")
        self.assertEqual(self.kpi(response, "Requests"), "4")

    def test_all_orgs_names_no_org_for_a_key_whose_owner_has_no_profile(self):
        root = User.objects.create_superuser("root", "root@example.com", "pw")
        self.log(make_token(root, "root-laptop"), timedelta(hours=1), 200, input_tokens=10)
        response = self.page(self.admin, org="all")
        self.assertContains(response, "root@example.com · No organization")
        self.assertNotContains(response, "· None")

    def test_admin_may_pick_another_org(self):
        response = self.page(self.admin, org=self.other_org.id)
        self.assert_keys(response, "foreign-laptop")
        self.assertNotContains(response, "By organization")

    def test_org_admin_cannot_pick_another_org(self):
        response = self.page(self.other_org_admin, org=self.org.id)
        self.assert_keys(response, "foreign-laptop")
        response = self.page(self.other_org_admin, org="all")
        self.assert_keys(response, "foreign-laptop")

    def test_key_filter_accepts_the_owners_key(self):
        response = self.page(self.solo, key=self.solo_key.id)
        self.assertContains(response, "for the key")
        self.assertEqual(self.kpi(response, "Requests"), "1")

    def test_key_filter_hides_keys_outside_the_viewers_scope(self):
        self.client.force_login(self.solo)
        for token in (self.member_key, self.foreign_key):
            response = self.client.get(reverse("usage"), {"key": token.id})
            self.assertEqual(response.status_code, 404)
            self.assertNotContains(response, token.name, status_code=404)
        self.assertEqual(self.page(self.other_org_admin, key=self.solo_key.id).status_code, 404)
        self.assertEqual(self.page(self.solo, key="abc").status_code, 404)

    def test_key_filter_accepts_a_service_account_key_for_its_team_only(self):
        response = self.page(self.member, key=self.pipeline_key.id)
        self.assertContains(response, "for the key")
        self.assertEqual(self.kpi(response, "Requests"), "1")
        self.assertEqual(self.page(self.solo, key=self.pipeline_key.id).status_code, 404)
        self.assertEqual(self.page(self.other_org_admin, key=self.pipeline_key.id).status_code, 404)

    def test_key_filter_lets_an_org_admin_name_any_key_of_their_org(self):
        response = self.page(self.org_admin, key=self.member_key.id)
        self.assertEqual(self.kpi(response, "Requests"), "1")
        self.assertContains(response, "By user ID")


class UsageFiguresTest(UsagePageTestBase):
    def test_totals_count_tokens_cache_hits_and_failures(self):
        response = self.page(self.org_admin)
        self.assertEqual(self.kpi(response, "Input from cache"), "250")
        self.assertEqual(self.kpi(response, "New input"), "5.8K")  # 6,000 less 250 cached
        self.assertEqual(self.kpi(response, "Output"), "1.5K")
        self.assertEqual(self.kpi(response, "Requests"), "3")
        self.assertEqual(self.kpi(response, "Failed"), "67%")  # 429 and 500 of 3

    def test_requests_are_split_by_status(self):
        response = self.page(self.org_admin)
        self.assertEqual(self.status_count(response, "Answered"), "1")
        self.assertEqual(self.status_count(response, "Rate-limited"), "1")
        self.assertEqual(self.status_count(response, "Other client errors"), "0")
        self.assertEqual(self.status_count(response, "Model or provider error"), "1")

    def test_time_frame_limits_the_requests_counted(self):
        self.log(self.solo_key, timedelta(days=3), 200)
        self.assertEqual(self.kpi(self.page(self.solo), "Requests"), "2")
        self.assertEqual(self.kpi(self.page(self.solo, span="1d"), "Requests"), "1")

    def test_model_filter_keeps_one_model(self):
        self.log(self.solo_key, timedelta(hours=1), 200, model=EMBEDDING, input_tokens=9)
        self.assertEqual(self.kpi(self.page(self.solo), "Requests"), "2")
        response = self.page(self.solo, model=EMBEDDING)
        self.assertEqual(self.kpi(response, "Requests"), "1")
        self.assertEqual(self.kpi(response, "New input"), "9")

    def test_models_outside_the_router_config_are_left_out(self):
        self.log(self.solo_key, timedelta(hours=1), 200, model="retired-model")
        response = self.page(self.solo)
        self.assertEqual(self.kpi(response, "Requests"), "1")
        self.assertNotContains(response, "retired-model")

    def test_heatmap_averages_the_whole_orgs_tokens_per_hour_over_four_weeks(self):
        moment = timezone.now() - timedelta(hours=1)
        weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[moment.isoweekday() - 1]
        until = (moment.hour + 1) % 24
        cell = (
            f'aria-label="{weekday} {moment.hour:02d}:00 to {until:02d}:00: {{}} tokens per hour"'
        )
        # lab-org's 7,500 tokens in one hour slot, spread over four weeks, whatever the filters.
        for user, params in (
            (self.solo, {}),
            (self.solo, {"key": self.solo_key.id, "model": EMBEDDING}),
            (self.org_admin, {"model": EMBEDDING}),
        ):
            with self.subTest(user=user.username, **params):
                response = self.page(user, **params)
                self.assertContains(response, "Activity across lab-org")
                self.assertContains(response, cell.format("1.9K"))
        response = self.page(self.admin, org="all")
        self.assertContains(response, "Activity across all organizations")
        self.assertContains(response, cell.format("3.6K"))  # 14,500 tokens

    def test_heatmap_is_cached_per_org(self):
        heat = self.page(self.solo).context["heat"]
        self.log(self.member_key, timedelta(minutes=1), 200, input_tokens=10**6)
        self.assertEqual(self.page(self.org_admin).context["heat"], heat)
        self.assertNotEqual(self.page(self.foreigner).context["heat"], heat)

    @override_settings(REQUEST_RETENTION_DAYS=7)
    def test_retention_warning_when_the_time_frame_reaches_past_retention(self):
        self.assertContains(self.page(self.solo, span="1m"), "Older requests are missing")
        self.assertNotContains(self.page(self.solo, span="1w"), "Older requests are missing")

    def test_an_unknown_time_frame_falls_back_to_7_days(self):
        self.log(self.solo_key, timedelta(days=10), 200)
        for span in ("all", "bogus"):
            response = self.page(self.solo, span=span)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.kpi(response, "Requests"), "1")
            self.assertContains(response, 'aria-current="page">7 days</a>')
            self.assertNotContains(response, "All retained")

    def test_start_and_end_parameters_are_ignored(self):
        self.log(self.solo_key, timedelta(days=3), 200)
        start = (timezone.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
        response = self.page(self.solo, start=start, end="2020-01-01T00:00")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.kpi(response, "Requests"), "2")

    def test_empty_time_frame_says_so_in_each_section(self):
        Request.objects.all().delete()
        response = self.page(self.solo)
        self.assertContains(response, "No requests in this time frame.", count=3)

    def test_model_rows_open_the_log_on_their_model(self):
        response = self.page(self.solo, span="1d")
        log = f"{reverse('requests')}?span=1d&amp;model={CHAT}"
        self.assertContains(
            response, f'<a class="link-plain row-link break-all font-mono" href="{log}"'
        )
        self.assertNotContains(response, f"{reverse('models')}?model=")

    def test_model_and_key_rows_show_the_lower_median_response(self):
        Request.objects.all().delete()
        for ms in (700, 100, None, 500, 300):
            Request.objects.create(
                token=self.solo_key,
                timestamp=timezone.now() - timedelta(hours=1),
                status_code=200,
                model=CHAT,
                response_time_ms=ms,
            )
        context = self.page(self.solo).context
        self.assertEqual([row.median for row in context["models"]], ["300 ms"])
        self.assertEqual([key.median for key in context["keys"]], ["300 ms"])

    def test_top_keys_rows_open_the_key(self):
        response = self.page(self.solo)
        detail = reverse("token_detail", args=[self.solo_key.id])
        self.assertContains(
            response, f'class="link-plain row-link block truncate font-mono" href="{detail}"'
        )

    def test_top_keys_count_tokens_and_requests_like_the_model_rows(self):
        self.log(self.pipeline_key, timedelta(hours=2), 200, input_tokens=1000)
        self.log(self.pipeline_key, timedelta(hours=3), 200, output_tokens=500)
        keys = {key.name: key for key in self.page(self.org_admin).context["keys"]}
        self.assertEqual(
            {name: key.activity.requests for name, key in keys.items()},
            {"pipeline": 3, "solo-laptop": 1, "member-laptop": 1},
        )
        pipeline = keys["pipeline"]
        self.assertEqual((pipeline.input, pipeline.output), ("4K", "1.5K"))
        self.assertEqual((pipeline.share, pipeline.median), ("61%", "100 ms"))
        self.assertEqual(pipeline.activity.segments, (("c", 0), ("i", 4000), ("o", 1500)))
        self.assertEqual(
            keys["solo-laptop"].activity.segments, (("c", 250), ("i", 750), ("o", 500))
        )

    def test_totals_do_not_link_to_the_log(self):
        response = self.page(self.org_admin)
        self.assertNotContains(response, '<a class="card')
        self.assertNotContains(response, "open in the request log")
