from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path, reverse
from tos.models import TermsOfService, UserAgreement

from aqueduct.urls import urlpatterns as aqueduct_urlpatterns
from management.models import Org, Team, TeamMembership, UserGroup, UserProfile
from management.tests.test_api_keys_pages import PAGES, make_token

User = get_user_model()

urlpatterns = [path("terms-of-service/", include("tos.urls")), *aqueduct_urlpatterns]


@PAGES
class SettingsPageTest(TestCase):
    def setUp(self):
        self.org = Org.objects.create(name="Informatics", requests_per_minute=600)
        self.nlp = Team.objects.create(name="NLP Lab", org=self.org)
        self.teaching = Team.objects.create(name="Teaching", org=self.org)
        self.member = self.user("member")
        TeamMembership.objects.create(
            user_profile=self.member.profile, team=self.nlp, is_admin=True
        )
        TeamMembership.objects.create(user_profile=self.member.profile, team=self.teaching)
        self.other_org = Org.objects.create(name="Elsewhere")
        self.outsider = self.user("outsider", org=self.other_org)
        TeamMembership.objects.create(
            user_profile=self.outsider.profile,
            team=Team.objects.create(name="Foreign", org=self.other_org),
        )
        make_token(self.outsider, "outsider-key")

    def user(self, name: str, group: UserGroup = UserGroup.USER, org: Org | None = None):
        user = User.objects.create_user(username=name, email=f"{name}@example.com")
        UserProfile.objects.create(user=user, org=org or self.org).group = group
        return user

    def page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("settings"))

    def test_signed_out_goes_to_sign_in(self):
        url = reverse("settings")
        self.assertRedirects(
            self.client.get(url), f"{reverse('sso')}?next={url}", fetch_redirect_response=False
        )

    def test_shows_the_viewers_account(self):
        response = self.page(self.user("boss", UserGroup.ORG_ADMIN))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "boss@example.com")
        self.assertContains(response, "<dd>Informatics</dd>", html=True)
        self.assertContains(response, "Org admin</span>")
        self.assertContains(response, '<span class="muted">None</span>', html=True)
        self.assertContains(response, "Set by your sign-in provider.")
        for gone in ("Sign-in</dt>", "Appearance", "e-mail"):
            self.assertNotContains(response, gone)

    def test_lists_only_the_viewers_teams_with_role_and_link(self):
        response = self.page(self.member)
        nlp, teaching = (
            reverse("team", args=[self.nlp.id]),
            reverse("team", args=[self.teaching.id]),
        )
        self.assertContains(response, f'<a class="link-plain" href="{nlp}">NLP Lab</a>', html=True)
        self.assertContains(response, f'href="{teaching}"')
        self.assertRegex(
            response.content.decode(), r"NLP Lab</a>\s*<span class=\"muted\">Team admin"
        )
        self.assertRegex(response.content.decode(), r"Teaching</a>\s*<span class=\"muted\">Member")
        self.assertNotContains(response, "Foreign")
        self.assertNotContains(response, "outsider@example.com")

    def test_counts_only_the_viewers_personal_keys(self):
        make_token(self.member, "laptop")
        with self.settings(MAX_USER_TOKENS=5):
            response = self.page(self.member)
        self.assertContains(response, ">1 of 5</span></dd>")

    def test_limits_come_from_the_org_unless_set_for_the_account(self):
        response = self.page(self.member)
        self.assertContains(response, ">600</span></dd>")
        self.assertContains(response, "Personal key limits</h2>")
        self.assertContains(response, ">Organization default</p>")

        profile = self.member.profile
        profile.requests_per_minute = 30
        profile.save()
        response = self.page(self.member)
        self.assertContains(response, ">30</span></dd>")
        self.assertNotContains(response, ">600</span></dd>")
        self.assertContains(response, "Set for your account. Others use the organization default.")

    def test_page_has_no_write_action(self):
        self.client.force_login(self.member)
        response = self.client.post(reverse("settings"))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.member.profile.teammembership_set.count(), 2)

    def test_terms_card_hidden_without_the_terms_route(self):
        self.assertNotContains(self.page(self.member), "Terms of service</h2>")

    @override_settings(ROOT_URLCONF=__name__)
    def test_terms_card_shows_when_the_viewer_accepted_the_current_terms(self):
        old = TermsOfService.objects.create(active=True, content="old")
        current = TermsOfService.objects.create(active=True, content="new")
        response = self.page(self.member)
        self.assertContains(response, "Terms of service</h2>")
        self.assertContains(response, ">Not accepted</span>")

        UserAgreement.objects.create(terms_of_service=old, user=self.member)
        self.assertContains(self.page(self.member), ">Not accepted</span>")

        agreement = UserAgreement.objects.create(terms_of_service=current, user=self.member)
        accepted = agreement.created
        response = self.page(self.member)
        self.assertContains(
            response,
            "Accepted "
            f'<time datetime="{accepted.isoformat()}" title="{accepted:%Y-%m-%d %H:%M:%S} UTC">'
            f"{accepted.day} {accepted:%b %Y}</time></span>",
        )
        self.assertContains(response, f'href="{reverse("tos")}"')
