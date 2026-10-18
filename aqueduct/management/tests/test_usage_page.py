import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from management import usage_rollup as rollup
from management.models import Org, Request, ServiceAccount, Team, TeamMembership, Token, UserGroup
from management.tests.helpers import PAGES, make_token, make_user
from management.usage_report import APPROXIMATE_MEDIAN

User = get_user_model()
CHAT = "gpt-4.1-nano"
EMBEDDING = "text-embedding-ada-002"
KEY_NAMES = ("solo-laptop", "member-laptop", "pipeline", "foreign-laptop")


@PAGES
class UsagePageTestBase(TestCase):
    def setUp(self):
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
        return make_user(name, org or self.org, group)

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
    def test_user_with_one_key_gets_no_key_picker_and_no_top_keys(self):
        response = self.page(self.solo)
        self.assert_keys(response)
        self.assertEqual(self.kpi(response, "Requests"), "1")
        self.assertNotContains(response, '<span class="sr-only">Key: </span>')
        self.assertNotContains(response, "Top keys")
        self.assertNotContains(response, "All organizations")

    def test_user_with_several_keys_picks_from_all_keys_only(self):
        make_token(self.solo, "solo-desktop")
        response = self.page(self.solo)
        self.assertContains(response, '<span class="sr-only">Key: </span>')
        self.assertContains(response, 'aria-current="true" class="menu-active">All keys</a>')
        self.assertNotContains(response, "My keys")
        self.assertNotContains(response, 'name="key_q"')
        self.assertContains(response, "Top keys")
        self.assertEqual([key.detail for key in response.context["keys"]], [""])

    def test_org_admin_sees_and_links_every_key_of_their_org(self):
        response = self.page(self.org_admin)
        self.assert_keys(response, "solo-laptop", "member-laptop", "pipeline")
        self.assertEqual(self.kpi(response, "Requests"), "3")
        for key in (self.solo_key, self.member_key, self.pipeline_key):
            self.assertContains(response, f'href="{reverse("token_detail", args=[key.id])}"')

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

    def test_key_filter_lets_an_org_admin_name_any_key_of_their_org(self):
        response = self.page(self.org_admin, key=self.member_key.id)
        self.assertEqual(self.kpi(response, "Requests"), "1")
        self.assertContains(response, "By user ID")


class UsageFiguresTest(UsagePageTestBase):
    def test_totals_count_tokens_cache_hits_and_failures(self):
        response = self.page(self.org_admin)
        self.assertEqual(self.kpi(response, "Input"), "6K")
        self.assertEqual(self.kpi(response, "Output"), "1.5K")
        self.assertEqual(self.kpi(response, "Cached"), "250")
        self.assertEqual(self.kpi(response, "Requests"), "3")
        self.assertEqual(self.kpi(response, "Failed"), "67%")

    def test_requests_are_split_by_status(self):
        response = self.page(self.org_admin)
        self.assertEqual(self.status_count(response, "OK"), "1")
        self.assertEqual(self.status_count(response, "Rate-limited"), "1")
        self.assertEqual(self.status_count(response, "Client error"), "0")
        self.assertEqual(self.status_count(response, "Server error"), "1")

    def test_time_frame_limits_the_requests_counted(self):
        self.log(self.solo_key, timedelta(days=3), 200)
        self.assertEqual(self.kpi(self.page(self.solo), "Requests"), "1")
        self.assertEqual(self.kpi(self.page(self.solo, span="1w"), "Requests"), "2")

    def test_model_filter_keeps_one_model(self):
        self.log(self.solo_key, timedelta(hours=1), 200, model=EMBEDDING, input_tokens=9)
        self.assertEqual(self.kpi(self.page(self.solo), "Requests"), "2")
        response = self.page(self.solo, model=EMBEDDING)
        self.assertEqual(self.kpi(response, "Requests"), "1")
        self.assertEqual(self.kpi(response, "Input"), "9")

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
        self.assertContains(response, cell.format("3.6K"))

    def test_without_current_rollups_the_heatmap_is_cached_per_org(self):
        heat = self.page(self.solo).context["heat"]
        self.log(self.member_key, timedelta(minutes=1), 200, input_tokens=10**6)
        self.assertEqual(self.page(self.org_admin).context["heat"], heat)
        self.assertNotEqual(self.page(self.foreigner).context["heat"], heat)

    def test_with_current_rollups_the_heatmap_shows_new_requests_at_once(self):
        rollup.refresh()
        heat = self.page(self.solo).context["heat"]
        foreign_heat = self.page(self.foreigner).context["heat"]
        self.log(self.member_key, timedelta(minutes=1), 200, input_tokens=10**6)
        self.assertNotEqual(self.page(self.org_admin).context["heat"], heat)
        self.assertEqual(self.page(self.foreigner).context["heat"], foreign_heat)

    @override_settings(REQUEST_RETENTION_DAYS=7)
    def test_retention_warning_when_the_time_frame_reaches_past_retention(self):
        self.assertContains(self.page(self.solo, span="1m"), "Older requests are missing")
        self.assertNotContains(self.page(self.solo, span="1w"), "Older requests are missing")

    def test_the_time_frame_is_24_hours_unless_a_known_one_is_chosen(self):
        self.log(self.solo_key, timedelta(days=2), 200)
        for span in ("", "all", "bogus"):
            response = self.page(self.solo, span=span)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.kpi(response, "Requests"), "1")
            self.assertContains(response, 'aria-current="page">24 hours</a>')
        self.assertEqual(self.kpi(self.page(self.solo, span="1w"), "Requests"), "2")

    def test_empty_time_frame_says_so_in_each_section(self):
        Request.objects.all().delete()
        response = self.page(self.member)
        self.assertContains(response, "No requests in this time frame.", count=3)

    def test_model_rows_open_the_log_on_their_model(self):
        response = self.page(self.solo, span="1d")
        log = f"{reverse('requests')}?span=1d&amp;model={CHAT}"
        self.assertContains(
            response, f'<a class="link-plain row-link break-all font-mono" href="{log}"'
        )
        self.assertNotContains(response, f"{reverse('models')}?model=")

    def test_model_rows_show_the_lower_median_response(self):
        Request.objects.all().delete()
        for ms in (700, 100, None, 500, 300):
            Request.objects.create(
                token=self.solo_key,
                timestamp=timezone.now() - timedelta(hours=1),
                status_code=200,
                model=CHAT,
                response_time_ms=ms,
            )
        self.assertEqual([row.median for row in self.page(self.solo).context["models"]], ["300 ms"])
        self.assertNotContains(self.page(self.solo), APPROXIMATE_MEDIAN)

    def test_top_keys_rows_open_the_key(self):
        response = self.page(self.member)
        detail = reverse("token_detail", args=[self.member_key.id])
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
        self.assertEqual(pipeline.share, "61%")
        self.assertEqual(pipeline.activity.segments, (("c", 0), ("n", 4000), ("o", 1500)))
        self.assertEqual(
            keys["solo-laptop"].activity.segments, (("c", 250), ("n", 750), ("o", 500))
        )
        self.assertEqual((keys["solo-laptop"].cached, pipeline.cached), ("25%", "0%"))

    def test_totals_do_not_link_to_the_log(self):
        response = self.page(self.org_admin)
        self.assertNotContains(response, '<a class="card')
        self.assertNotContains(response, "open in the request log")


class UsageChartTest(UsagePageTestBase):
    def series(self, response) -> dict[str, tuple[bool, str]]:
        html = response.content.decode()
        found = {}
        opening = re.compile(r'<div class="flex h-72[^"]*" data-metric="(\w+)"( hidden)?>')
        matches = list(opening.finditer(html))
        for match, following in zip(matches, [*matches[1:], None], strict=True):
            end = following.start() if following else html.index("</section>", match.end())
            labels = re.findall(r'chart-col[^>]*aria-label="([^"]*)"', html[match.end() : end])
            found[match.group(1)] = (match.group(2) is None, " ".join(labels))
        return found

    def test_both_series_are_on_the_page_and_tokens_is_shown_first(self):
        series = self.series(self.page(self.org_admin))
        self.assertEqual(series.keys(), {"tokens", "requests"})
        self.assertTrue(series["tokens"][0])
        self.assertFalse(series["requests"][0])
        self.assertIn("Output", series["tokens"][1])
        self.assertIn("OK 1, Failed 2", series["requests"][1])

    def test_the_metric_parameter_picks_the_series_shown(self):
        response = self.page(self.org_admin, metric="requests")
        series = self.series(response)
        self.assertFalse(series["tokens"][0])
        self.assertTrue(series["requests"][0])
        self.assertContains(response, '<span data-metric="requests">Requests per hour</span>')

    def test_the_metric_links_still_load_the_page_without_scripts(self):
        response = self.page(self.org_admin, span="1m", key_q="lap")
        html = response.content.decode()
        self.assertIn('href="?span=1m" data-metric-show="tokens" aria-current="page"', html)
        self.assertIn('href="?span=1m&amp;metric=requests" data-metric-show="requests"', html)


class UsageGermanTest(UsagePageTestBase):
    def test_a_german_browser_reads_the_overview_and_all_keys_in_german(self):
        self.client.force_login(self.org_admin)
        overview = self.client.get(
            reverse("usage"), {"span": "1w", "metric": "requests"}, HTTP_ACCEPT_LANGUAGE="de"
        ).content.decode()
        self.assertIn('aria-current="page">7 Tage</a>', overview)
        self.assertIn('<span data-metric="requests">Anfragen pro Tag</span>', overview)
        self.assertIn("3 Anfragen (OK 1, Fehlgeschlagen 2)", overview)
        self.assertIn("Limit erreicht", overview)
        self.assertIn("Mo 00:00 bis 01:00", overview)
        months = r"(Jan\.|Feb\.|März|April|Mai|Juni|Juli|Aug\.|Sept\.|Okt\.|Nov\.|Dez\.)"
        self.assertRegex(overview, rf">\d{{1,2}}\. {months}<")
        tokens = self.client.get(reverse("usage"), {"span": "1w"}, HTTP_ACCEPT_LANGUAGE="de")
        self.assertEqual(self.kpi(tokens, "Ausgabe"), "1,5\xa0Tsd.")
        self.assertEqual(self.kpi(tokens, "Fehlgeschlagen"), "67\xa0%")
        keys = self.client.get(reverse("usage_keys"), HTTP_ACCEPT_LANGUAGE="de")
        self.assertContains(keys, "3 Schlüssel</span>")


class UsageHeatModelTest(UsagePageTestBase):
    def test_heat_model_narrows_only_the_heatmap(self):
        self.log(self.member_key, timedelta(hours=1), 200, model=EMBEDDING, input_tokens=400)
        moment = timezone.now() - timedelta(hours=1)
        weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[moment.isoweekday() - 1]
        cell = (
            f'aria-label="{weekday} {moment.hour:02d}:00 to {(moment.hour + 1) % 24:02d}:00: '
            '{} tokens per hour"'
        )
        everything = self.page(self.org_admin)
        embedding = self.page(self.org_admin, heat_model=EMBEDDING)
        self.assertContains(everything, cell.format("2K"))
        self.assertContains(embedding, cell.format("100"))
        self.assertEqual(self.kpi(embedding, "Requests"), self.kpi(everything, "Requests"))
        self.assertEqual(self.kpi(embedding, "Requests"), "4")

    def test_the_page_filters_leave_the_heatmap_alone(self):
        heat = self.page(self.org_admin).context["heat"]
        for params in ({"model": EMBEDDING}, {"scope": "mine"}, {"span": "1h"}):
            with self.subTest(**params):
                self.assertEqual(self.page(self.org_admin, **params).context["heat"], heat)

    def test_heat_model_links_keep_the_filters_and_return_to_the_heatmap(self):
        response = self.page(self.org_admin, model=CHAT, span="1w")
        self.assertContains(
            response, f'href="?model={CHAT}&amp;span=1w&amp;heat_model={EMBEDDING}#org-activity"'
        )
        self.assertNotContains(response, "The filters above do not apply here")

    def test_an_unknown_heat_model_shows_every_model(self):
        response = self.page(self.org_admin, heat_model="retired-model")
        self.assertIsNone(response.context["heat_model"])
        self.assertEqual(response.context["heat"], self.page(self.org_admin).context["heat"])


class UsageFilterChoicesTest(UsagePageTestBase):
    def test_models_sort_without_regard_to_case(self):
        with mock.patch(
            "management.request_log.model_names", return_value=("b-model", "A-model", "c-model")
        ):
            filters = self.page(self.org_admin).context["filters"]
        self.assertEqual(filters.models, ("A-model", "b-model", "c-model"))

    def test_the_model_filter_hides_with_one_model(self):
        self.assertContains(self.page(self.org_admin), '<span class="sr-only">Model: </span>')
        with mock.patch("management.request_log.model_names", return_value=(CHAT,)):
            response = self.page(self.org_admin)
        self.assertNotContains(response, '<span class="sr-only">Model: </span>')
        self.assertNotContains(response, "Heatmap model")

    def test_an_org_admin_without_keys_gets_no_my_keys(self):
        response = self.page(self.org_admin)
        self.assertContains(response, 'aria-current="true" class="menu-active">All keys</a>')
        self.assertNotContains(response, ">My keys</a>")

    def test_an_org_admin_finds_their_own_keys_under_my_keys(self):
        own = make_token(self.org_admin, "admin-laptop")
        response = self.page(self.org_admin)
        html = response.content.decode()
        mine = html.index(">My keys</a>")
        self.assertGreater(html.index(f"key={own.id}", mine), mine)


class UsageKeyNamesTest(UsagePageTestBase):
    def details(self, user, **params) -> dict[str, str]:
        return {key.name: key.detail for key in self.page(user, **params).context["keys"]}

    def test_the_second_line_says_whose_key_it_is(self):
        own = make_token(self.org_admin, "admin-laptop")
        self.log(own, timedelta(hours=1), 200, input_tokens=5)
        self.assertEqual(
            self.details(self.org_admin),
            {
                "pipeline": "Service account · NLP Lab",
                "member-laptop": "member@example.com",
                "solo-laptop": "solo@example.com",
                "admin-laptop": "You",
            },
        )

    def test_a_viewer_of_their_own_keys_gets_no_second_line(self):
        self.assertEqual(self.details(self.member), {"pipeline": "", "member-laptop": ""})


class UsageModelColumnsTest(UsagePageTestBase):
    def test_median_time_is_marked_approximate_only_org_wide(self):
        self.assertContains(self.page(self.org_admin), f'data-tip="{APPROXIMATE_MEDIAN}"')
        response = self.page(self.member)
        self.assertContains(response, "Median time")
        self.assertNotContains(response, f'data-tip="{APPROXIMATE_MEDIAN}"')

    def test_models_without_tokens_show_dashes_and_follow_by_requests(self):
        for _ in range(2):
            self.log(self.solo_key, timedelta(hours=1), 200, model=EMBEDDING)
        Request.objects.filter(model=EMBEDDING).update(response_time_ms=None)
        rows = self.page(self.solo).context["models"]
        self.assertEqual([row.name for row in rows], [CHAT, EMBEDDING])
        unmetered = rows[1]
        self.assertEqual(
            (
                unmetered.input,
                unmetered.cached,
                unmetered.output,
                unmetered.share,
                unmetered.median,
            ),
            ("—", "—", "—", "—", "—"),
        )
        self.assertEqual((rows[0].input, rows[0].cached), ("1K", "25%"))

    def test_day_columns_read_only_the_day_on_small_screens(self):
        response = self.page(self.org_admin, span="1w")
        day = timezone.localtime().strftime("%-d")
        self.assertContains(
            response,
            f'<span class="sm:hidden">{day}</span><span class="hidden sm:inline">'
            f"{timezone.localtime():%-d %b}</span>",
        )
