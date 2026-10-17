import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from management.key_activity import Expiry
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

User = get_user_model()

PAGES = override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    LITELLM_ROUTER_CONFIG_FILE_PATH=Path(settings.BASE_DIR).parent / "example_router_config.yaml",
)


def make_token(user, name: str, **kwargs) -> Token:
    token = Token(name=name, user=user, **kwargs)
    token._set_new_key()
    token.save()
    return token


def secret_on(response) -> str | None:
    match = re.search(r'data-copy="(sk-[^"]+)"', response.content.decode())
    return match.group(1) if match else None


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
        user = User.objects.create_user(username=name, email=f"{name}@example.com")
        UserProfile.objects.create(user=user, org=self.org)
        return user

    def request_log(self, token: Token, ago: timedelta, **fields) -> Request:
        fields.setdefault("model", "gpt-4.1-nano")
        return Request.objects.create(token=token, timestamp=timezone.now() - ago, **fields)

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
        self.assertContains(response, "1 of 3 personal keys")
        self.assertNotContains(response, "others-laptop")
        self.assertNotContains(response, "pipeline")

    def test_page_never_names_another_users_key_id(self):
        response = self.get(self.owner, "tokens")
        self.assertNotContains(response, f"/tokens/{self.others_key.id}/")
        self.assertNotContains(response, f"key={self.others_key.id}")

    def test_team_member_sees_team_service_account_managed_from_the_team_page(self):
        response = self.get(self.member, "tokens")
        self.assertContains(
            response, f'href="{reverse("token_detail", args=[self.pipeline_key.id])}"'
        )
        team = reverse("team", args=[self.team.id])
        self.assertContains(
            response,
            f'Service account · <a class="link-plain relative z-10" href="{team}">keys-team</a>',
        )
        self.assertContains(response, "Managed by the team")
        self.assertNotContains(response, reverse("token_edit", args=[self.pipeline_key.id]))
        self.assertNotContains(response, reverse("token_delete", args=[self.pipeline_key.id]))
        self.assertNotContains(response, reverse("token_regenerate", args=[self.pipeline_key.id]))
        self.assertNotContains(response, "hidden-sa")

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
        self.assertIn("an hour ago", row)
        self.assertIn("2.5K", row)  # 2,000 input + 500 output, the old request left out
        self.assertIn("12%", row)  # 250 of 2,000 input tokens from the cache
        self.assertIn('<td class="text-right font-mono">2</td>', row)

    def test_unused_key_reads_never(self):
        response = self.get(self.owner, "tokens")
        self.assertContains(response, '<td class="muted">Never</td>', html=True)

    def test_last_used_names_the_exact_utc_time(self):
        used = self.request_log(self.key, timedelta(hours=2)).timestamp
        response = self.get(self.owner, "tokens")
        self.assertContains(response, f'datetime="{used.isoformat()}"')
        self.assertContains(response, f'title="{used:%-d %b %Y, %H:%M:%S} UTC"')

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

    def test_create_button_is_disabled_at_the_limit(self):
        make_token(self.owner, "two")
        make_token(self.owner, "three")
        response = self.get(self.owner, "tokens")
        self.assertContains(response, "3 of 3 personal keys")
        self.assertNotContains(response, f'href="{reverse("token_create")}"')
        self.assertContains(response, '<button type="button" class="btn btn-primary" disabled>')


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
        self.assertIn("3K", page)  # input tokens
        self.assertIn("50%", page)  # one of two requests failed
        self.assertIn("curl/8.5.0 100%", page)
        self.assertIn("/responses", page)
        self.assertIn(f"{reverse('usage')}?key={self.key.id}", page)

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
            response,
            '<span class="st-warn inline-flex items-center gap-1.5" title="Too Many Requests">',
        )
        self.assertContains(response, '<td class="text-right">5.6 s</td>', html=True)
        log = reverse("requests")
        self.assertContains(
            response,
            f'href="{log}?key={self.key.id}&amp;request={limited.id}#request-{limited.id}"',
        )
        self.assertContains(
            response,
            f'<a class="link-plain" href="{log}?key={self.key.id}">Open the log</a>',
            html=True,
        )
        opened = self.client.get(f"{log}?key={self.key.id}&request={limited.id}")
        self.assertContains(opened, f'aria-label="Details of request {limited.id}"')

    def test_latest_requests_are_those_the_log_opens_on(self):
        shown = self.request_log(self.key, timedelta(days=29))
        self.request_log(self.key, timedelta(days=31))
        detail = self.get(self.owner, "token_detail", self.key.id)
        self.assertEqual([row.request for row in detail.context["latest"]], [shown])
        log = self.get(self.owner, "requests", key=self.key.id).context["log"]
        self.assertEqual([row.request for row in log.rows], [shown])
        shown.delete()
        self.assertContains(
            self.get(self.owner, "token_detail", self.key.id), "No requests in the last 30 days."
        )

    def test_requests_metric_charts_failed_requests(self):
        self.request_log(self.key, timedelta(minutes=5), status_code=500)
        response = self.get(self.owner, "token_detail", self.key.id, metric="requests")
        self.assertContains(response, "Requests per day")
        self.assertContains(response, '<i class="f" style="height: 100%"></i>', html=True)

    def test_chart_column_names_its_day_and_counts(self):
        self.request_log(self.key, timedelta(minutes=5), status_code=500)
        response = self.get(self.owner, "token_detail", self.key.id, metric="requests")
        today = f"{timezone.localdate():%-d %b}"
        self.assertContains(response, f'aria-label="{today}: 1 request (Answered 0, Failed 1)"')

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
            overview = self.get(self.owner, "usage", key=self.key.id).context["kpis"]
        requests = next(kpi.value for kpi in overview if kpi.label == "Requests")
        # 20:00 seven days back falls before the window; 01:00 six days back falls inside.
        self.assertEqual((listed, detail.totals.requests, requests), (2, 2, "2"))
        self.assertEqual(sum(day.activity.requests for day in detail.days), 2)

    def test_key_pages_leave_out_requests_the_log_does_not_show(self):
        served = self.request_log(self.key, timedelta(minutes=3), status_code=200)
        retired = self.request_log(self.key, timedelta(minutes=2), model="retired-model")
        no_model = self.request_log(self.key, timedelta(minutes=1), model="", path="/files")
        listed = self.get(self.owner, "tokens").context["keys"].rows[0].activity
        detail = self.get(self.owner, "token_detail", self.key.id).context
        overview = self.get(self.owner, "usage", key=self.key.id).context["kpis"]
        log = self.get(self.owner, "requests", key=self.key.id).context["log"]
        requests = next(kpi.value for kpi in overview if kpi.label == "Requests")
        self.assertEqual((listed.requests, detail["report"].totals.requests, requests), (1, 1, "1"))
        self.assertEqual(sum(day.activity.requests for day in detail["report"].days), 1)
        # A key used only for files must not look unused, so "Last used" counts every request.
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


class SecretShownOnceTest(ApiKeysPagesTestBase):
    def test_created_key_secret_appears_once_on_the_list(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("token_create"), {"name": "fresh", "expires_at": ""}, follow=True
        )
        self.assertEqual(response.redirect_chain, [(reverse("tokens"), 302)])
        self.assertContains(response, "Your key is ready")
        secret = secret_on(response)
        self.assertEqual(Token.find_by_key(secret), Token.objects.get(name="fresh"))
        self.assertContains(response, "curl http://testserver/v1/models")

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
        self.assertContains(response, "API key &quot;laptop&quot; deleted successfully.")
        self.assertFalse(Token.objects.filter(id=self.key.id).exists())


class ExpiryTest(SimpleTestCase):
    def test_labels(self):
        now = timezone.now()
        cases = [
            (None, "Never", "muted"),
            (now - timedelta(minutes=1), "Expired", "st-err font-semibold"),
            (now + timedelta(hours=3), "Today", "st-warn font-semibold"),
            (now + timedelta(days=5, hours=1), "In 5 days", "st-warn font-semibold"),
            (now + timedelta(days=30), f"{now + timedelta(days=30):%-d %b %Y}", "muted"),
        ]
        for expires_at, label, tone in cases:
            with self.subTest(label=label):
                self.assertEqual(Expiry.of(expires_at, now), Expiry(label, tone))


class ServiceAccountKeyDetailTest(ApiKeysPagesTestBase):
    """Whoever may see a team sees its service account keys' usage page, and nobody else."""

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
            '<div class="flex items-baseline gap-2"><dt class="muted">Requests per minute</dt>'
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
