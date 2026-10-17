from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
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
from management.tests.test_api_keys_pages import PAGES, make_token, secret_on

User = get_user_model()


@PAGES
class TeamsPagesTestBase(TestCase):
    def setUp(self):
        self.org = Org.objects.create(name="lab-org")
        self.other_org = Org.objects.create(name="other-org")
        self.team = Team.objects.create(name="NLP Lab", org=self.org, description="Language tech")
        self.side_team = Team.objects.create(name="Robotics", org=self.org)
        self.foreign_team = Team.objects.create(name="Foreign", org=self.other_org)

        self.org_admin = self.user("org-admin", UserGroup.ORG_ADMIN)
        self.team_admin = self.user("team-admin")
        self.member = self.user("member")
        self.outsider = self.user("outsider")
        self.other_org_admin = self.user("other-org-admin", UserGroup.ORG_ADMIN, self.other_org)
        self.join(self.team_admin, self.team, is_admin=True)
        self.join(self.member, self.team)

        self.pipeline = ServiceAccount.objects.create(name="pipeline", team=self.team)
        self.pipeline_key = make_token(self.team_admin, "pipeline", service_account=self.pipeline)

    def user(self, name: str, group: UserGroup = UserGroup.USER, org: Org | None = None):
        user = User.objects.create_user(username=name, email=f"{name}@example.com")
        UserProfile.objects.create(user=user, org=org or self.org).group = group
        return user

    def join(self, user, team: Team, is_admin: bool = False) -> None:
        TeamMembership.objects.create(user_profile=user.profile, team=team, is_admin=is_admin)

    def as_user(self, user):
        self.client.force_login(user)
        return self.client

    def assert_sent_to_sign_in(self, response, url: str) -> None:
        self.assertRedirects(
            response, f"{reverse('sso')}?next={url}", fetch_redirect_response=False
        )


class TeamListTest(TeamsPagesTestBase):
    def test_signed_out_goes_to_sign_in(self):
        url = reverse("org")
        self.assert_sent_to_sign_in(self.client.get(url), url)

    def test_member_sees_only_their_teams_with_their_role(self):
        response = self.as_user(self.member).get(reverse("org"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("team", args=[self.team.id])}"')
        self.assertContains(response, "<td>Member</td>", html=True)
        self.assertNotContains(response, "Robotics")
        self.assertNotContains(response, reverse("team_create"))

    def test_user_outside_every_team_sees_an_empty_list(self):
        response = self.as_user(self.outsider).get(reverse("org"))
        self.assertContains(response, "You are not in a team yet.")
        self.assertNotContains(response, "NLP Lab")

    def test_org_admin_sees_every_team_of_their_org_and_may_create_one(self):
        response = self.as_user(self.org_admin).get(reverse("org"))
        self.assertContains(response, "NLP Lab")
        self.assertContains(response, "Robotics")
        self.assertNotContains(response, "Foreign")
        self.assertContains(response, f'href="{reverse("team_create")}"')
        self.assertNotContains(response, "Your role")

    def test_search_filters_by_team_name(self):
        response = self.as_user(self.org_admin).get(reverse("org"), {"q": "robo"})
        self.assertContains(response, "Robotics")
        self.assertNotContains(response, "NLP Lab")
        response = self.as_user(self.org_admin).get(reverse("org"), {"q": "nothing-like-it"})
        self.assertContains(response, "No team matches.")

    def test_row_shows_counts_limit_and_service_account_tokens_of_seven_days(self):
        self.team.requests_per_minute = 1200
        self.team.save()
        now = timezone.now()
        Request.objects.create(
            token=self.pipeline_key,
            model="gpt-4.1-nano",
            timestamp=now,
            input_tokens=2000,
            output_tokens=500,
        )
        Request.objects.create(
            token=self.pipeline_key,
            model="gpt-4.1-nano",
            timestamp=now - timedelta(days=9),
            input_tokens=9_000_000,
        )
        personal = make_token(self.member, "laptop")
        Request.objects.create(
            token=personal, model="gpt-4.1-nano", timestamp=now, input_tokens=7_000_000
        )
        response = self.as_user(self.member).get(reverse("org"))
        row = response.content.decode().split("Language tech", 1)[1].split("</tr>", 1)[0]
        self.assertIn('<td class="text-right font-mono">2</td>', row)  # members
        self.assertIn('<td class="text-right font-mono">1</td>', row)  # service accounts
        self.assertIn("1,200", row)
        self.assertIn(">2.5K<", row)

    def test_org_default_limit_links_to_its_edit_page_for_org_admins_and_admin_for_staff(self):
        self.org.requests_per_minute = 600
        self.org.save()
        edit = reverse("organization_limits", args=[self.org.id])
        admin = reverse("admin:management_org_change", args=[self.org.id])
        response = self.as_user(self.org_admin).get(reverse("org"))
        self.assertContains(response, '<span class="font-mono font-semibold">600</span>')
        self.assertContains(response, edit)
        self.assertNotContains(response, admin)
        member_page = self.as_user(self.member).get(reverse("org"))
        self.assertNotContains(member_page, edit)
        self.assertNotContains(member_page, admin)
        self.org_admin.is_staff = True
        self.org_admin.save()
        response = self.as_user(self.org_admin).get(reverse("org"))
        self.assertContains(response, edit)
        self.assertContains(response, admin)


class TeamDetailTest(TeamsPagesTestBase):
    def url(self, team: Team | None = None) -> str:
        return reverse("team", args=[(team or self.team).id])

    def test_signed_out_goes_to_sign_in(self):
        self.assert_sent_to_sign_in(self.client.get(self.url()), self.url())

    def test_member_sees_the_team_without_management_controls(self):
        response = self.as_user(self.member).get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pipeline")
        self.assertContains(response, "team-admin@example.com")
        for name, args in [
            ("team_edit", [self.team.id]),
            ("service_account_create", [self.team.id]),
            ("service_account_edit", [self.pipeline.id]),
            ("service_account_transfer", [self.pipeline.id]),
            ("service_account_delete", [self.pipeline.id]),
            ("token_regenerate", [self.pipeline_key.id]),
            ("team_delete", [self.team.id]),
            ("team_admins_manage", [self.team.id]),
        ]:
            self.assertNotContains(response, reverse(name, args=args))
        self.assertNotContains(response, "user_profile_to_add_id")
        self.assertNotContains(response, "user_profile_to_remove_id")

    def test_team_admin_manages_members_and_service_accounts(self):
        response = self.as_user(self.team_admin).get(self.url())
        for name, args in [
            ("team_edit", [self.team.id]),
            ("service_account_create", [self.team.id]),
            ("service_account_edit", [self.pipeline.id]),
            ("service_account_transfer", [self.pipeline.id]),
            ("service_account_delete", [self.pipeline.id]),
            ("token_regenerate", [self.pipeline_key.id]),
        ]:
            self.assertContains(response, reverse(name, args=args))
        self.assertNotContains(response, reverse("team_delete", args=[self.team.id]))
        self.assertNotContains(response, reverse("team_admins_manage", args=[self.team.id]))
        self.assertContains(
            response, f'<option value="{self.outsider.profile.id}">outsider@example.com</option>'
        )
        self.assertNotContains(response, "other-org-admin@example.com")

    def test_owner_of_a_service_account_cannot_be_removed(self):
        response = self.as_user(self.team_admin).get(self.url())
        page = response.content.decode().split('id="members-title"', 1)[1]
        owner_row = page.split("team-admin@example.com</span>", 1)[1].split("</tr>", 1)[0]
        self.assertIn("disabled", owner_row)
        self.assertNotIn("user_profile_to_remove_id", owner_row)
        member_row = page.split("member@example.com</span>", 1)[1].split("</tr>", 1)[0]
        self.assertIn(f'value="{self.member.profile.id}"', member_row)

    def test_org_admin_may_appoint_team_admins_and_delete_the_team(self):
        response = self.as_user(self.org_admin).get(self.url())
        self.assertContains(response, reverse("team_delete", args=[self.team.id]))
        self.assertContains(response, "Make team admin")
        self.assertContains(response, "Remove team admin")

    def test_service_account_row_and_totals_count_seven_days(self):
        Request.objects.create(
            token=self.pipeline_key,
            model="gpt-4.1-nano",
            timestamp=timezone.now(),
            input_tokens=3000,
            cached_input_tokens=1500,
            output_tokens=1000,
            status_code=200,
        )
        response = self.as_user(self.member).get(self.url())
        self.assertContains(response, self.pipeline_key.key_preview)
        self.assertContains(response, '<span class="text-2xl font-semibold">3K</span>', html=True)
        self.assertContains(response, '<span class="text-2xl font-semibold">50%</span>', html=True)
        self.assertContains(response, ">4K<")

    def test_switched_off_models_and_limits_are_listed(self):
        self.team.excluded_models = ["gpt-image-1"]
        self.team.input_tokens_per_minute = 5000
        self.team.save()
        response = self.as_user(self.member).get(self.url())
        self.assertContains(response, "gpt-image-1")
        self.assertContains(
            response,
            '<div class="flex items-baseline gap-2"><dt class="muted">Input tokens per minute</dt>'
            '<dd><span class="font-mono font-semibold">5,000</span></dd></div>',
            html=True,
        )

    def test_outsiders_are_sent_back_to_the_teams_page(self):
        for user, team in [
            (self.outsider, self.team),
            (self.member, self.side_team),
            (self.other_org_admin, self.team),
        ]:
            with self.subTest(user=user.username, team=team.name):
                response = self.as_user(user).get(self.url(team))
                self.assertRedirects(response, reverse("org"), fetch_redirect_response=False)


class TeamMembershipTest(TeamsPagesTestBase):
    def url(self) -> str:
        return reverse("team", args=[self.team.id])

    def members(self) -> set[str]:
        return set(self.team.member_profiles.values_list("user__username", flat=True))

    def test_signed_out_post_changes_nothing(self):
        before = self.members()
        response = self.client.post(
            self.url(), {"user_profile_to_add_id": self.outsider.profile.id}
        )
        self.assert_sent_to_sign_in(response, self.url())
        self.assertEqual(self.members(), before)

    def test_team_admin_adds_and_removes_a_member(self):
        client = self.as_user(self.team_admin)
        client.post(self.url(), {"user_profile_to_add_id": self.outsider.profile.id})
        self.assertIn("outsider", self.members())
        client.post(self.url(), {"user_profile_to_remove_id": self.outsider.profile.id})
        self.assertNotIn("outsider", self.members())

    def test_member_and_outsider_cannot_change_membership(self):
        before = self.members()
        for user in (self.member, self.outsider, self.other_org_admin):
            with self.subTest(user=user.username):
                self.as_user(user).post(
                    self.url(), {"user_profile_to_remove_id": self.member.profile.id}
                )
                self.assertEqual(self.members(), before)


class TeamFormsTest(TeamsPagesTestBase):
    def test_signed_out_goes_to_sign_in(self):
        for name, args in [
            ("team_create", []),
            ("team_edit", [self.team.id]),
            ("team_delete", [self.team.id]),
            ("team_admins_manage", [self.team.id]),
            ("service_account_create", [self.team.id]),
            ("service_account_edit", [self.pipeline.id]),
            ("service_account_transfer", [self.pipeline.id]),
            ("service_account_delete", [self.pipeline.id]),
        ]:
            with self.subTest(name=name):
                url = reverse(name, args=args)
                self.assert_sent_to_sign_in(self.client.get(url), url)
                self.assert_sent_to_sign_in(self.client.post(url, {"name": "x"}), url)
        self.assertEqual(Team.objects.count(), 3)
        self.assertEqual(self.team.service_accounts.count(), 1)

    def test_org_admin_creates_a_team(self):
        client = self.as_user(self.org_admin)
        self.assertContains(client.get(reverse("team_create")), "Create a team")
        response = client.post(reverse("team_create"), {"name": "Vision", "description": ""})
        self.assertRedirects(response, reverse("org"), fetch_redirect_response=False)
        self.assertTrue(Team.objects.filter(name="Vision", org=self.org).exists())

    def test_users_cannot_create_a_team(self):
        for user in (self.team_admin, self.member):
            with self.subTest(user=user.username):
                response = self.as_user(user).post(reverse("team_create"), {"name": "Vision"})
                self.assertRedirects(response, reverse("org"), fetch_redirect_response=False)
        self.assertFalse(Team.objects.filter(name="Vision").exists())

    def test_duplicate_team_name_rerenders_the_form(self):
        response = self.as_user(self.org_admin).post(reverse("team_create"), {"name": "nlp lab"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A team with this name already exists in your organization.")

    def test_team_admin_edits_the_team(self):
        url = reverse("team_edit", args=[self.team.id])
        client = self.as_user(self.team_admin)
        self.assertContains(client.get(url), 'value="NLP Lab"')
        response = client.post(url, {"name": "NLP Group", "description": "new"})
        self.assertRedirects(
            response, reverse("team", args=[self.team.id]), fetch_redirect_response=False
        )
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "NLP Group")

    def test_member_and_outsiders_cannot_edit_the_team(self):
        url = reverse("team_edit", args=[self.team.id])
        for user in (self.member, self.outsider, self.other_org_admin):
            with self.subTest(user=user.username):
                response = self.as_user(user).post(url, {"name": "Hijacked"})
                self.assertEqual(response.status_code, 302)
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "NLP Lab")


class ServiceAccountFormsTest(TeamsPagesTestBase):
    def test_team_admin_creates_a_service_account_and_sees_its_key_once(self):
        client = self.as_user(self.team_admin)
        url = reverse("service_account_create", args=[self.team.id])
        self.assertContains(client.get(url), "Add a service account")
        response = client.post(
            url, {"name": "rag", "description": "", "token_expires_at": ""}, follow=True
        )
        self.assertEqual(response.redirect_chain, [(reverse("team", args=[self.team.id]), 302)])
        self.assertContains(response, "Your key is ready")
        sa = ServiceAccount.objects.get(name="rag", team=self.team)
        self.assertEqual(Token.find_by_key(secret_on(response)), sa.token)

    def test_member_and_outsiders_cannot_create_a_service_account(self):
        url = reverse("service_account_create", args=[self.team.id])
        for user in (self.member, self.outsider, self.other_org_admin):
            with self.subTest(user=user.username):
                response = self.as_user(user).post(url, {"name": "rag", "token_expires_at": ""})
                self.assertEqual(response.status_code, 302)
        self.assertFalse(ServiceAccount.objects.filter(name="rag").exists())

    def test_team_admin_edits_a_service_account(self):
        url = reverse("service_account_edit", args=[self.pipeline.id])
        client = self.as_user(self.team_admin)
        page = client.get(url)
        self.assertContains(page, "Edit service account")
        self.assertContains(page, "Save changes")
        client.post(url, {"name": "pipeline-2", "description": "", "token_expires_at": ""})
        self.pipeline.refresh_from_db()
        self.assertEqual(self.pipeline.name, "pipeline-2")

    def test_past_expiry_rerenders_the_form_with_an_error(self):
        url = reverse("service_account_edit", args=[self.pipeline.id])
        past = (timezone.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        response = self.as_user(self.team_admin).post(
            url, {"name": "pipeline", "description": "", "token_expires_at": past}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Expiration date must be in the future.")
        self.assertContains(response, 'class="input w-full max-w-xs input-error"')
        self.assertContains(response, 'class="input w-full max-w-md"')
        self.pipeline_key.refresh_from_db()
        self.assertIsNone(self.pipeline_key.expires_at)

    def test_member_and_outsiders_cannot_edit_a_service_account(self):
        url = reverse("service_account_edit", args=[self.pipeline.id])
        for user in (self.member, self.outsider, self.other_org_admin):
            with self.subTest(user=user.username):
                response = self.as_user(user).post(
                    url, {"name": "hijacked", "token_expires_at": ""}
                )
                self.assertEqual(response.status_code, 302)
        self.pipeline.refresh_from_db()
        self.assertEqual(self.pipeline.name, "pipeline")

    def test_team_admin_transfers_the_key_to_a_member(self):
        url = reverse("service_account_transfer", args=[self.pipeline.id])
        client = self.as_user(self.team_admin)
        page = client.get(url)
        self.assertContains(
            page, f'<option value="{self.member.profile.id}">member@example.com</option>'
        )
        self.assertNotContains(page, "outsider@example.com")
        response = client.post(url, {"target_profile_id": self.member.profile.id})
        self.assertRedirects(
            response, reverse("team", args=[self.team.id]), fetch_redirect_response=False
        )
        self.pipeline_key.refresh_from_db()
        self.assertEqual(self.pipeline_key.user, self.member)

    def test_member_and_outsiders_cannot_transfer_the_key(self):
        url = reverse("service_account_transfer", args=[self.pipeline.id])
        for user in (self.member, self.outsider, self.other_org_admin):
            with self.subTest(user=user.username):
                self.as_user(user).post(url, {"target_profile_id": user.profile.id})
        self.pipeline_key.refresh_from_db()
        self.assertEqual(self.pipeline_key.user, self.team_admin)
