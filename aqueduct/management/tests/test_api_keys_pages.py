import html
import re
from datetime import UTC, datetime, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from management.key_activity import Expiry, KeyAccess
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
from management.models.token import HIERARCHY
from management.tests.helpers import PAGES, make_token, make_user, secret_on

User = get_user_model()


@PAGES
class ApiKeysPagesTestBase(TestCase):
    def setUp(self):
        self.org = Org.objects.create(name="keys-org")
        self.team = Team.objects.create(name="keys-team", org=self.org)
        self.other_team = Team.objects.create(name="other-team", org=self.org)

        self.owner = self.user("owner")
        self.other = self.user("other")
        member_profile = UserProfile.objects.get(user=self.user("member"))
        self.member = member_profile.user
        TeamMembership.objects.create(user_profile=member_profile, team=self.team, is_admin=True)

        self.key = make_token(self.owner, "laptop")
        self.others_key = make_token(self.other, "others-laptop")
        self.pipeline = ServiceAccount.objects.create(name="pipeline", team=self.team)
        self.pipeline_key = make_token(self.member, "pipeline", service_account=self.pipeline)
        hidden_sa = ServiceAccount.objects.create(name="hidden-sa", team=self.other_team)
        make_token(self.other, "hidden-sa", service_account=hidden_sa)

    def user(self, name: str):
        return make_user(name, self.org)

    def request_log(self, token: Token, ago: timedelta, **fields) -> Request:
        fields.setdefault("model", "gpt-4.1-nano")
        return Request.objects.create(token=token, timestamp=timezone.now() - ago, **fields)

    def request_today(self, token: Token, **fields) -> Request:
        midnight = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.request_log(token, timezone.now() - midnight, **fields)

    def get(self, user, name: str, *args, **params):
        self.client.force_login(user)
        return self.client.get(reverse(name, args=args), params)


class KeyListTest(ApiKeysPagesTestBase):
    def test_signed_out_goes_to_sign_in(self):
        url = reverse("tokens")
        response = self.client.get(url)
        self.assertRedirects(
            response, f"{reverse('sso')}?next={url}", fetch_redirect_response=False
        )

    def test_owner_sees_own_keys_linked_to_their_detail_page(self):
        response = self.get(self.owner, "tokens")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("token_detail", args=[self.key.id])}"')
        self.assertContains(response, self.key.key_preview)
        self.assertContains(response, "Create API key")
        self.assertContains(response, ">1/3</span>")
        self.assertNotContains(response, "others-laptop")
        self.assertNotContains(response, "pipeline")

    def test_page_never_names_another_users_key_id(self):
        response = self.get(self.owner, "tokens")
        self.assertNotContains(response, f"/tokens/{self.others_key.id}/")
        self.assertNotContains(response, f"key={self.others_key.id}")

    def test_team_admin_manages_the_teams_service_accounts_from_the_list(self):
        response = self.get(self.member, "tokens")
        self.assertContains(
            response, f'href="{reverse("token_detail", args=[self.pipeline_key.id])}"'
        )
        team = reverse("team", args=[self.team.id])
        self.assertContains(
            response,
            f'Service account · <a class="link-plain relative z-10" href="{team}">keys-team</a>',
        )
        self.assertContains(response, reverse("token_regenerate", args=[self.pipeline_key.id]))
        self.assertContains(response, reverse("service_account_edit", args=[self.pipeline.id]))
        self.assertContains(response, reverse("service_account_delete", args=[self.pipeline.id]))
        self.assertContains(response, 'data-confirm="Delete pipeline?"')
        self.assertNotContains(response, reverse("token_edit", args=[self.pipeline_key.id]))
        self.assertNotContains(response, "hidden-sa")

    def test_plain_team_member_sees_the_service_account_without_actions(self):
        plain = self.user("plain")
        TeamMembership.objects.create(user_profile=plain.profile, team=self.team)
        response = self.get(plain, "tokens")
        self.assertContains(
            response, f'href="{reverse("token_detail", args=[self.pipeline_key.id])}"'
        )
        self.assertNotContains(response, reverse("token_regenerate", args=[self.pipeline_key.id]))
        self.assertNotContains(response, reverse("service_account_edit", args=[self.pipeline.id]))
        self.assertNotContains(response, reverse("service_account_delete", args=[self.pipeline.id]))
        self.assertNotContains(response, "Managed by the team")

    def test_rotating_from_the_list_returns_to_the_list_with_the_new_secret(self):
        self.client.force_login(self.member)
        response = self.client.post(
            reverse("token_regenerate", args=[self.pipeline_key.id]),
            {"next": reverse("tokens")},
            follow=True,
        )
        self.assertEqual(response.redirect_chain, [(reverse("tokens"), 302)])
        self.assertEqual(Token.find_by_key(secret_on(response)), self.pipeline_key)

    def test_columns_count_the_last_seven_days(self):
        self.request_log(
            self.key,
            timedelta(hours=1),
            input_tokens=1000,
            cached_input_tokens=250,
            output_tokens=500,
        )
        self.request_log(self.key, timedelta(days=2), input_tokens=1000, output_tokens=0)
        self.request_log(self.key, timedelta(days=9), input_tokens=999_000, output_tokens=0)
        response = self.get(self.owner, "tokens")
        row = response.content.decode().split(self.key.key_preview, 1)[1]
        self.assertIn("1\xa0hour ago", row)
        self.assertIn("2.5K", row)
        self.assertIn('<td class="hidden text-right font-mono md:table-cell">2</td>', row)

    def test_unused_key_and_key_without_expiry_read_as_such(self):
        response = self.get(self.owner, "tokens")
        self.assertContains(response, '<td class="muted hidden md:table-cell">—</td>', html=True)
        self.assertContains(response, '<span class="muted">No expiry</span>', html=True)

    def test_last_used_names_the_exact_utc_time(self):
        used = self.request_log(self.key, timedelta(hours=2)).timestamp
        response = self.get(self.owner, "tokens")
        self.assertContains(response, f'datetime="{used.isoformat()}"')
        self.assertContains(response, f'title="{used:%Y-%m-%d %H:%M:%S} UTC"')

    def test_type_tab_and_search_filter_rows(self):
        make_token(self.member, "notebook")
        response = self.get(self.member, "tokens", kind="service")
        self.assertContains(response, "pipeline")
        self.assertNotContains(response, ">notebook<")
        response = self.get(self.member, "tokens", q="NOTE")
        self.assertContains(response, ">notebook<")
        self.assertNotContains(response, "Service account · keys-team")
        response = self.get(self.member, "tokens", q="nothing-like-this")
        self.assertContains(response, "No key matches.")
        self.assertNotContains(response, "No keys yet.")

    def test_tabs_read_all_personal_and_service_accounts(self):
        response = self.get(self.member, "tokens")
        for label in ("All", "Personal", "Service accounts"):
            self.assertContains(response, f">{label}</a>")

    def test_without_keys_the_list_offers_to_create_one(self):
        response = self.get(self.user("newcomer"), "tokens")
        self.assertContains(response, "No keys yet.")
        self.assertContains(response, f'href="{reverse("token_create")}"', count=2)
        self.assertContains(response, ">0/3</span>")

    def test_create_button_is_disabled_at_the_limit(self):
        make_token(self.owner, "two")
        make_token(self.owner, "three")
        response = self.get(self.owner, "tokens")
        self.assertNotContains(response, f'href="{reverse("token_create")}"')
        self.assertContains(
            response,
            '<button type="button" class="btn btn-primary" disabled>3 of 3 keys used</button>',
            html=True,
        )


class KeyDetailTest(ApiKeysPagesTestBase):
    def test_signed_out_goes_to_sign_in(self):
        url = reverse("token_detail", args=[self.key.id])
        response = self.client.get(url)
        self.assertRedirects(
            response, f"{reverse('sso')}?next={url}", fetch_redirect_response=False
        )

    def test_owner_sees_totals_models_and_latest_requests(self):
        self.request_log(
            self.key,
            timedelta(minutes=5),
            model="gpt-4.1-nano",
            input_tokens=3000,
            output_tokens=1000,
            status_code=200,
            path="/chat/completions",
            user_agent="curl/8.5.0",
        )
        self.request_log(
            self.key,
            timedelta(minutes=6),
            model="gpt-4.1-nano",
            status_code=504,
            path="/responses",
            user_agent="curl/8.5.0",
        )
        response = self.get(self.owner, "token_detail", self.key.id)
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("3K", page)
        self.assertIn("50%", page)
        self.assertIn("curl/8.5.0 100%", page)
        self.assertIn("/responses", page)
        self.assertIn("Last 7 days", page)
        self.assertNotIn(f"{reverse('usage')}?key={self.key.id}", page)

    def test_latest_requests_match_the_request_log(self):
        limited = self.request_log(
            self.key,
            timedelta(minutes=5),
            model="gpt-4.1-nano",
            status_code=429,
            response_time_ms=5640,
        )
        response = self.get(self.owner, "token_detail", self.key.id)
        self.assertContains(
            response, '<span class="st-warn inline-flex items-center gap-1.5" title="Rate-limited">'
        )
        self.assertContains(
            response, '<td class="whitespace-nowrap text-right">5.6 s</td>', html=True
        )
        log = reverse("requests")
        self.assertContains(
            response,
            f'href="{log}?key={self.key.id}&amp;span=1m&amp;request={limited.id}#request-{limited.id}"',
        )
        self.assertContains(
            response,
            f'<a class="link-plain text-sm" href="{log}?key={self.key.id}&amp;span=1m">'
            "All requests</a>",
            html=True,
        )
        opened = self.client.get(f"{log}?key={self.key.id}&request={limited.id}")
        self.assertContains(opened, f'aria-label="Details of request {limited.id}"')

    def test_latest_requests_are_those_the_log_opens_on(self):
        shown = self.request_log(self.key, timedelta(days=29))
        self.request_log(self.key, timedelta(days=31))
        detail = self.get(self.owner, "token_detail", self.key.id)
        self.assertEqual([row.request for row in detail.context["latest"]], [shown])
        log = self.get(self.owner, "requests", key=self.key.id, span="1m").context["log"]
        self.assertEqual([row.request for row in log.rows], [shown])
        shown.delete()
        self.assertContains(
            self.get(self.owner, "token_detail", self.key.id), "No requests in the last 30 days."
        )

    def test_requests_metric_charts_failed_requests(self):
        self.request_log(self.key, timedelta(minutes=5), status_code=500)
        response = self.get(self.owner, "token_detail", self.key.id, metric="requests")
        self.assertContains(response, '<span data-metric="requests">Requests per day</span>')
        self.assertContains(response, '<i class="f" style="height: 100%"></i>', html=True)

    def test_chart_holds_both_series_so_the_switch_needs_no_reload(self):
        self.request_today(self.key, status_code=500, output_tokens=7)
        today = f"{timezone.localdate():%-d %b}"
        for metric, shown, hidden in (
            ("tokens", "tokens", "requests"),
            ("requests", "requests", "tokens"),
        ):
            with self.subTest(metric=metric):
                html = self.get(self.owner, "token_detail", self.key.id, metric=metric)
                html = html.content.decode()
                self.assertIn(f'grid-cols-7 gap-3" data-metric="{shown}">', html)
                self.assertIn(f'grid-cols-7 gap-3" data-metric="{hidden}" hidden>', html)
                self.assertIn(f'aria-label="{today}: 1 request (OK 0, Failed 1)"', html)
                self.assertIn(f'aria-label="{today}: 7 tokens (', html)

    def test_metric_links_still_load_the_page_without_scripts(self):
        html = self.get(self.owner, "token_detail", self.key.id).content.decode()
        self.assertIn('href="?metric=requests" data-metric-show="requests"', html)
        self.assertIn('href="" data-metric-show="tokens" aria-current="page"', html)

    def test_chart_column_names_its_day_and_counts(self):
        self.request_today(self.key, status_code=500)
        response = self.get(self.owner, "token_detail", self.key.id, metric="requests")
        today = f"{timezone.localdate():%-d %b}"
        self.assertContains(response, f'aria-label="{today}: 1 request (OK 0, Failed 1)"')

    def test_dates_name_the_full_utc_time_and_the_tab_names_the_key(self):
        used = self.request_log(self.key, timedelta(minutes=5)).timestamp
        self.key.expires_at = timezone.now() + timedelta(days=30)
        self.key.save()
        response = self.get(self.owner, "token_detail", self.key.id)
        self.assertContains(response, "<title>laptop · API keys · Aqueduct AI Gateway</title>")
        self.assertContains(
            response, f'<time datetime="{used.isoformat()}" title="{used:%Y-%m-%d %H:%M:%S} UTC">'
        )
        self.assertContains(response, f">{used:%-d %b %Y %H:%M}</time></a>")
        expires = self.key.expires_at
        self.assertContains(
            response,
            f'<time datetime="{expires.isoformat()}" title="{expires:%Y-%m-%d %H:%M:%S} UTC">'
            f"Expires {expires:%-d %b %Y}</time>",
        )

    def test_other_users_key_is_not_found(self):
        response = self.get(self.other, "token_detail", self.key.id)
        self.assertEqual(response.status_code, 404)

    def test_unknown_key_is_not_found(self):
        response = self.get(self.owner, "token_detail", 999_999)
        self.assertEqual(response.status_code, 404)


class SevenDaysTest(ApiKeysPagesTestBase):
    def test_key_pages_count_the_same_seven_days_as_the_overview(self):
        now = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
        for ago in (timedelta(days=6, hours=12), timedelta(days=6, hours=7), timedelta(hours=1)):
            Request.objects.create(
                token=self.key, timestamp=now - ago, model="gpt-4.1-nano", status_code=200
            )
        with mock.patch("django.utils.timezone.now", return_value=now):
            listed = self.get(self.owner, "tokens").context["keys"].rows[0].activity.requests
            detail = self.get(self.owner, "token_detail", self.key.id).context["report"]
            overview = self.get(self.owner, "usage", key=self.key.id, span="1w").context["kpis"]
        requests = next(kpi.value for kpi in overview if kpi.label == "Requests")
        self.assertEqual((listed, detail.totals.requests, requests), (2, 2, "2"))
        self.assertEqual(sum(day.activity.requests for day in detail.days), 2)

    def test_key_pages_leave_out_requests_the_log_does_not_show(self):
        served = self.request_log(self.key, timedelta(minutes=3), status_code=200)
        retired = self.request_log(self.key, timedelta(minutes=2), model="retired-model")
        no_model = self.request_log(self.key, timedelta(minutes=1), model="", path="/files")
        listed = self.get(self.owner, "tokens").context["keys"].rows[0].activity
        detail = self.get(self.owner, "token_detail", self.key.id).context
        overview = self.get(self.owner, "usage", key=self.key.id, span="1w").context["kpis"]
        log = self.get(self.owner, "requests", key=self.key.id).context["log"]
        requests = next(kpi.value for kpi in overview if kpi.label == "Requests")
        self.assertEqual((listed.requests, detail["report"].totals.requests, requests), (1, 1, "1"))
        self.assertEqual(sum(day.activity.requests for day in detail["report"].days), 1)
        self.assertEqual(
            (listed.last_used, detail["report"].totals.last_used),
            (no_model.timestamp, no_model.timestamp),
        )
        self.assertEqual([row.request for row in detail["latest"]], [served])
        self.assertEqual([row.request for row in log.rows], [served])
        for hidden in (no_model, retired):
            self.assertNotContains(
                self.get(self.owner, "token_detail", self.key.id), f"request={hidden.id}"
            )

    def test_last_used_reaches_back_past_the_seven_days(self):
        old = self.request_log(self.pipeline_key, timedelta(days=20), model="", path="/files")
        listed = self.get(self.member, "tokens").context["keys"].rows[0].activity
        detail = self.get(self.member, "token_detail", self.pipeline_key.id).context["report"]
        team = self.get(self.member, "team", self.team.id).context["page"]
        on_team = team.service_accounts[0].activity
        self.assertEqual(
            (listed.last_used, detail.totals.last_used, on_team.last_used), (old.timestamp,) * 3
        )
        self.assertEqual((listed.requests, detail.totals.requests, on_team.requests), (0, 0, 0))


class SecretShownOnceTest(ApiKeysPagesTestBase):
    def test_created_key_secret_appears_once_on_the_list(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("token_create"), {"name": "fresh", "expires_at": ""}, follow=True
        )
        self.assertEqual(response.redirect_chain, [(reverse("tokens"), 302)])
        self.assertContains(response, "Your key is ready")
        self.assertContains(response, "Copy it now. You won't see it again.")
        self.assertNotContains(response, "created.")
        self.assertNotContains(response, 'class="alert ')
        secret = secret_on(response)
        self.assertEqual(Token.find_by_key(secret), Token.objects.get(name="fresh"))
        self.assertContains(response, "export OPENAI_BASE_URL=http://testserver/v1")
        self.assertContains(response, f"export OPENAI_API_KEY={secret}")
        self.assertContains(
            response,
            "curl $OPENAI_BASE_URL/models -H &quot;Authorization: Bearer $OPENAI_API_KEY&quot;",
        )

        again = self.client.get(reverse("tokens"))
        self.assertNotContains(again, "Your key is ready")
        self.assertNotContains(again, secret)

    def test_rotated_key_secret_replaces_the_old_one(self):
        self.client.force_login(self.owner)
        old_hash = self.key.key_hash
        response = self.client.post(reverse("token_regenerate", args=[self.key.id]), follow=True)
        secret = secret_on(response)
        self.key.refresh_from_db()
        self.assertNotEqual(self.key.key_hash, old_hash)
        self.assertEqual(Token.find_by_key(secret), self.key)
        self.assertContains(response, "Your key is ready")
        self.assertNotContains(response, "rotated")
        self.assertNotContains(response, 'class="alert ')

    def test_create_at_the_limit_rerenders_the_form_with_an_error(self):
        make_token(self.owner, "two")
        make_token(self.owner, "three")
        self.client.force_login(self.owner)
        response = self.client.post(reverse("token_create"), {"name": "fourth", "expires_at": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You can only have 3 API keys.")
        self.assertFalse(Token.objects.filter(name="fourth").exists())


class KeyEditAndDeleteReturnTest(ApiKeysPagesTestBase):
    def test_edit_and_delete_cancel_return_to_the_key(self):
        detail = reverse("token_detail", args=[self.key.id])
        for page in ("token_edit", "token_delete"):
            with self.subTest(page=page):
                response = self.get(self.owner, page, self.key.id)
                self.assertContains(response, f'href="{detail}">Cancel</a>')

    def test_delete_returns_to_the_list_with_a_message(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("token_delete", args=[self.key.id]), follow=True)
        self.assertRedirects(response, reverse("tokens"))
        self.assertContains(response, "API key laptop deleted.")
        self.assertFalse(Token.objects.filter(id=self.key.id).exists())


class ExpiryTest(SimpleTestCase):
    def test_labels_and_sentences(self):
        now = timezone.now()
        later = now + timedelta(days=30)
        day = f"{timezone.localtime(later):%-d %b %Y}"
        warn, err = "st-warn font-semibold", "st-err font-semibold"
        cases = [
            (None, "No expiry", "Never expires", "muted"),
            (now - timedelta(minutes=1), "Expired", "Expired", err),
            (now + timedelta(hours=3), "Today", "Expires today", warn),
            (now + timedelta(days=1, hours=1), "In 1 day", "Expires in 1 day", warn),
            (now + timedelta(days=5, hours=1), "In 5 days", "Expires in 5 days", warn),
            (later, day, f"Expires {day}", "muted"),
        ]
        for expires_at, label, sentence, tone in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    Expiry.of(expires_at, now), Expiry(label, sentence, tone, expires_at)
                )

    def test_german(self):
        now = timezone.now()
        with translation.override("de"):
            soon = Expiry.of(now + timedelta(days=3, hours=1), now)
            self.assertEqual((soon.label, soon.sentence), ("In 3 Tagen", "Läuft in 3 Tagen ab"))
            later = Expiry.of(
                datetime(2026, 12, 17, 12, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)
            )
            self.assertEqual(later.label, "17. Dez. 2026")


class OrgAdminKeyAccessTest(ApiKeysPagesTestBase):
    def setUp(self):
        super().setUp()
        self.org_admin = self.user("org-admin")
        self.org_admin.profile.group = UserGroup.ORG_ADMIN
        other_org = Org.objects.create(name="elsewhere")
        self.far_admin = User.objects.create_user(username="far", email="far@example.com")
        UserProfile.objects.create(user=self.far_admin, org=other_org).group = UserGroup.ORG_ADMIN
        self.admin = User.objects.create_user(username="admin", email="admin@example.com")
        UserProfile.objects.create(user=self.admin, org=other_org).group = UserGroup.ADMIN
        self.request_log(self.key, timedelta(hours=1), input_tokens=4200)

    def test_org_admin_sees_a_members_key_without_actions(self):
        response = self.get(self.org_admin, "token_detail", self.key.id)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4.2K")
        self.assertContains(response, "Personal · owner@example.com")
        self.assertContains(response, f"{reverse('requests')}?key={self.key.id}")
        for action in ("token_regenerate", "token_edit", "token_delete"):
            self.assertNotContains(response, reverse(action, args=[self.key.id]))

    def test_org_admin_cannot_change_a_members_key(self):
        self.client.force_login(self.org_admin)
        before = Token.objects.values().get(id=self.key.id)
        for action, data in (
            ("token_regenerate", {}),
            ("token_edit", {"name": "taken-over", "expires_at": ""}),
            ("token_delete", {}),
        ):
            with self.subTest(action=action):
                response = self.client.post(reverse(action, args=[self.key.id]), data)
                self.assertEqual(response.status_code, 404)
        self.assertEqual(Token.objects.values().get(id=self.key.id), before)

    def test_org_admin_of_another_org_gets_not_found(self):
        for key in (self.key, self.pipeline_key):
            with self.subTest(key=key.name):
                self.assertEqual(self.get(self.far_admin, "token_detail", key.id).status_code, 404)

    def test_admin_opens_any_key_whatever_org_the_switch_shows(self):
        self.client.force_login(self.admin)
        self.client.get(reverse("tokens"), {"org": Org.objects.get(name="elsewhere").id})
        for key in (self.key, self.pipeline_key):
            with self.subTest(key=key.name):
                response = self.client.get(reverse("token_detail", args=[key.id]))
                self.assertEqual(response.status_code, 200)

    def test_admin_follows_a_key_into_the_log_whatever_org_the_switch_shows(self):
        self.request_log(self.pipeline_key, timedelta(hours=1))
        self.client.force_login(self.admin)
        self.client.get(reverse("tokens"), {"org": Org.objects.get(name="elsewhere").id})
        log = re.escape(reverse("requests"))
        for key in (self.key, self.pipeline_key):
            page = self.client.get(reverse("token_detail", args=[key.id])).content.decode()
            links = re.findall(rf'href="({log}\?[^"#]*)', page)
            self.assertEqual(len(links), 2)
            for link in links:
                with self.subTest(key=key.name, link=link):
                    response = self.client.get(html.unescape(link))
                    shown = Request.objects.get(token=key)
                    self.assertContains(response, f'id="request-{shown.id}"')

    def test_admin_on_all_organizations_stays_there_after_a_key_log_link(self):
        self.client.force_login(self.admin)
        self.client.get(reverse("tokens"), {"org": "all"})
        page = self.client.get(reverse("token_detail", args=[self.key.id])).content.decode()
        self.assertNotIn("org=", "".join(re.findall(r'href="[^"]*/requests/\?[^"]*"', page)))

    def test_admin_rotating_another_orgs_service_account_lands_on_its_key(self):
        detail = reverse("token_detail", args=[self.pipeline_key.id])
        self.client.force_login(self.admin)
        self.client.get(reverse("tokens"), {"org": Org.objects.get(name="elsewhere").id})
        response = self.client.post(
            reverse("token_regenerate", args=[self.pipeline_key.id]), {"next": detail}, follow=True
        )
        self.assertEqual(response.redirect_chain, [(detail, 302)])
        self.assertEqual(Token.find_by_key(secret_on(response)), self.pipeline_key)

    def test_key_links_agree_with_the_keys_that_open(self):
        plain = self.user("plain")
        TeamMembership.objects.create(user_profile=plain.profile, team=self.team)
        everyone = Token.objects.select_related(*HIERARCHY)
        for user in (self.owner, self.member, plain, self.org_admin, self.far_admin, self.admin):
            access = KeyAccess.of(user.profile)
            with self.subTest(user=user.username):
                self.assertEqual(
                    {token.id for token in everyone if access.can_open(token)},
                    set(access.tokens().values_list("id", flat=True)),
                )
        self.assertEqual(
            {token.name for token in KeyAccess.of(self.org_admin.profile).tokens()},
            {"laptop", "others-laptop", "pipeline", "hidden-sa"},
        )


class KeyLinksTest(ApiKeysPagesTestBase):
    PAGES = ("requests", "usage", "usage_keys")

    def setUp(self):
        super().setUp()
        self.org_admin = self.user("org-admin")
        self.org_admin.profile.group = UserGroup.ORG_ADMIN
        self.hidden_key = Token.objects.get(name="hidden-sa")
        self.members_key = make_token(self.member, "members-laptop")
        for token in (
            self.key,
            self.others_key,
            self.pipeline_key,
            self.hidden_key,
            self.members_key,
        ):
            self.request_log(token, timedelta(hours=1), input_tokens=100)

    def detail(self, token: Token) -> str:
        return f'href="{reverse("token_detail", args=[token.id])}"'

    def test_org_admin_sees_every_members_key_linked(self):
        for page in self.PAGES:
            response = self.get(self.org_admin, page)
            for token in (self.key, self.others_key, self.pipeline_key, self.hidden_key):
                with self.subTest(page=page, key=token.name):
                    self.assertContains(response, self.detail(token))

    def test_team_member_sees_only_their_own_and_their_teams_keys_linked(self):
        for page in ("requests", "usage"):
            response = self.get(self.member, page)
            with self.subTest(page=page):
                self.assertContains(response, self.detail(self.members_key))
                self.assertContains(response, self.detail(self.pipeline_key))
                for token in (self.key, self.others_key, self.hidden_key):
                    self.assertNotContains(response, self.detail(token))

    def test_queries_do_not_grow_with_the_keys_listed(self):
        def queries() -> dict[str, int]:
            self.client.force_login(self.org_admin)
            counts = {}
            for page in self.PAGES:
                cache.clear()
                with CaptureQueriesContext(connection) as captured:
                    self.assertEqual(self.client.get(reverse(page)).status_code, 200)
                counts[page] = len(captured)
            return counts

        before = queries()
        for n in range(5):
            colleague = self.user(f"colleague-{n}")
            self.request_log(make_token(colleague, f"colleague-{n}"), timedelta(hours=1))
            account = ServiceAccount.objects.create(name=f"bot-{n}", team=self.other_team)
            bot = make_token(colleague, f"bot-{n}", service_account=account)
            self.request_log(bot, timedelta(hours=1))
        self.assertEqual(queries(), before)


class ServiceAccountKeyDetailTest(ApiKeysPagesTestBase):
    def setUp(self):
        super().setUp()
        self.plain_member = self.user("plain-member")
        TeamMembership.objects.create(user_profile=self.plain_member.profile, team=self.team)
        self.org_admin = self.user("org-admin")
        self.org_admin.profile.group = UserGroup.ORG_ADMIN
        other_org = Org.objects.create(name="elsewhere")
        self.other_org_admin = User.objects.create_user(username="far", email="far@example.com")
        UserProfile.objects.create(
            user=self.other_org_admin, org=other_org
        ).group = UserGroup.ORG_ADMIN
        self.request_log(self.pipeline_key, timedelta(hours=1), input_tokens=4200)

    def detail(self, user):
        return self.get(user, "token_detail", self.pipeline_key.id)

    def test_team_admin_and_org_admin_see_usage_and_can_manage_the_account(self):
        edit = reverse("service_account_edit", args=[self.pipeline.id])
        for user in (self.member, self.org_admin):
            with self.subTest(user=user.username):
                response = self.detail(user)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Service account · ")
                self.assertContains(response, "4.2K")
                self.assertContains(response, f"?key={self.pipeline_key.id}")
                self.assertContains(response, f'href="{edit}"')
                self.assertContains(
                    response, reverse("token_regenerate", args=[self.pipeline_key.id])
                )
                self.assertNotContains(response, reverse("token_edit", args=[self.pipeline_key.id]))

    def test_plain_team_member_sees_usage_without_management_actions(self):
        response = self.detail(self.plain_member)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4.2K")
        self.assertNotContains(response, reverse("token_regenerate", args=[self.pipeline_key.id]))
        self.assertNotContains(response, reverse("service_account_edit", args=[self.pipeline.id]))
        self.assertNotContains(response, reverse("service_account_delete", args=[self.pipeline.id]))

    def test_users_outside_the_team_get_not_found(self):
        for user in (self.other, self.owner, self.other_org_admin):
            with self.subTest(user=user.username):
                self.assertEqual(self.detail(user).status_code, 404)

    def test_team_page_links_each_service_account_to_its_key(self):
        response = self.get(self.plain_member, "team", self.team.id)
        self.assertContains(
            response, f'href="{reverse("token_detail", args=[self.pipeline_key.id])}"'
        )

    def test_limits_are_the_teams_not_the_viewers(self):
        self.team.requests_per_minute = 7
        self.team.save()
        profile = self.member.profile
        profile.requests_per_minute = 999
        profile.save()
        response = self.detail(self.member)
        self.assertContains(
            response,
            '<div class="flex flex-wrap items-baseline gap-x-2">'
            '<dt class="muted whitespace-nowrap">Requests per minute</dt>'
            '<dd><span class="font-mono font-semibold">7</span></dd></div>',
            html=True,
        )
        self.assertNotContains(response, "999</span>")

    def test_plain_team_member_cannot_rotate_the_key(self):
        old_hash = self.pipeline_key.key_hash
        self.client.force_login(self.plain_member)
        response = self.client.post(reverse("token_regenerate", args=[self.pipeline_key.id]))
        self.assertRedirects(
            response, reverse("team", args=[self.team.id]), fetch_redirect_response=False
        )
        self.pipeline_key.refresh_from_db()
        self.assertEqual(self.pipeline_key.key_hash, old_hash)

    def test_rotating_from_the_detail_page_returns_there_with_the_new_secret(self):
        detail = reverse("token_detail", args=[self.pipeline_key.id])
        self.client.force_login(self.member)
        response = self.client.post(
            reverse("token_regenerate", args=[self.pipeline_key.id]), {"next": detail}, follow=True
        )
        self.assertEqual(response.redirect_chain, [(detail, 302)])
        self.assertEqual(Token.find_by_key(secret_on(response)), self.pipeline_key)

    def test_rotating_from_the_team_page_returns_to_the_team(self):
        self.client.force_login(self.member)
        response = self.client.post(reverse("token_regenerate", args=[self.pipeline_key.id]))
        self.assertRedirects(
            response, reverse("team", args=[self.team.id]), fetch_redirect_response=False
        )
