from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path, reverse
from tos.models import TermsOfService, UserAgreement

from aqueduct.urls import urlpatterns as aqueduct_urlpatterns
from management.models import Org, Team, TeamMembership, UserGroup, UserProfile
from management.tests.helpers import PAGES, make_token, make_user

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
        return make_user(name, org or self.org, group)

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

    def test_admin_viewing_another_org_sees_their_own(self):
        self.client.force_login(self.user("root", UserGroup.ADMIN))
        response = self.client.get(reverse("settings"), {"org": self.other_org.id})
        self.assertContains(response, "<dd>Informatics</dd>", html=True)
        self.assertNotContains(response, "<dd>Elsewhere</dd>", html=True)

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


LANGUAGE_COOKIE = settings.LANGUAGE_COOKIE_NAME


def html_lang(response) -> str:
    return response.content.decode().split('<html lang="', 1)[1].split('"', 1)[0]


@PAGES
class LanguageTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ana", email="ana@example.com")
        UserProfile.objects.create(
            user=self.user, org=Org.objects.create(name="Informatics")
        ).group = UserGroup.USER

    def choose(self, language: str, next_url: str = ""):
        return self.client.post(reverse("language"), {"language": language, "next": next_url})

    def saved(self) -> str:
        self.user.profile.refresh_from_db()
        return self.user.profile.language

    def test_card_offers_each_language_in_its_own_words_and_marks_the_shown_one(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("settings"))
        self.assertContains(page, "Follows your browser until you choose one.")
        self.assertRegex(page.content.decode(), r'lang="de" class="[^"]*"[^>]*>Deutsch</button>')
        self.assertRegex(
            page.content.decode(), r'lang="en" class="[^"]*btn-primary" aria-pressed="true">English'
        )
        self.choose("de")
        page = self.client.get(reverse("settings"))
        self.assertRegex(
            page.content.decode(), r'lang="de" class="[^"]*btn-primary" aria-pressed="true">Deutsch'
        )
        self.assertContains(page, ">English</button>")
        self.assertNotContains(page, "Folgt Ihrem Browser")

    def test_choice_is_saved_in_the_profile_and_the_cookie_and_returns_to_the_page(self):
        self.client.force_login(self.user)
        back = f"{reverse('usage')}?span=7d"
        response = self.choose("de", back)
        self.assertRedirects(response, back, fetch_redirect_response=False)
        self.assertEqual(self.saved(), "de")
        self.assertEqual(response.cookies[LANGUAGE_COOKIE].value, "de")
        self.assertEqual(html_lang(self.client.get(reverse("settings"))), "de")

        self.choose("en")
        self.assertEqual(self.saved(), "en")
        self.assertEqual(html_lang(self.client.get(reverse("settings"))), "en")

    def test_next_outside_the_site_returns_to_settings(self):
        self.client.force_login(self.user)
        for unsafe in ("https://evil.example.com/", "//evil.example.com/", ""):
            with self.subTest(next=unsafe):
                response = self.choose("de", unsafe)
                self.assertRedirects(response, reverse("settings"), fetch_redirect_response=False)

    def test_unknown_language_changes_nothing(self):
        self.client.force_login(self.user)
        for language in ("fr", "", "de-at", "Deutsch"):
            with self.subTest(language=language):
                response = self.choose(language)
                self.assertRedirects(response, reverse("settings"), fetch_redirect_response=False)
                self.assertNotIn(LANGUAGE_COOKIE, response.cookies)
                self.assertEqual(self.saved(), "")
        page = self.client.get(reverse("settings"))
        self.assertContains(page, "Choose one of the offered languages.")

    def test_signed_out_visitor_cannot_save_a_language(self):
        response = self.choose("de")
        self.assertRedirects(
            response, f"{reverse('sso')}?next={reverse('language')}", fetch_redirect_response=False
        )
        self.assertNotIn(LANGUAGE_COOKIE, response.cookies)
        self.assertEqual(self.saved(), "")

    def test_page_language_follows_the_cookie_then_the_browser_then_english(self):
        self.client.force_login(self.user)
        url = reverse("settings")
        self.assertEqual(html_lang(self.client.get(url)), "en")
        self.assertEqual(html_lang(self.client.get(url, HTTP_ACCEPT_LANGUAGE="fr, de;q=0.8")), "de")
        self.assertEqual(html_lang(self.client.get(url, HTTP_ACCEPT_LANGUAGE="fr")), "en")
        self.client.cookies[LANGUAGE_COOKIE] = "en"
        self.assertEqual(html_lang(self.client.get(url, HTTP_ACCEPT_LANGUAGE="de")), "en")

    def test_german_page_uses_the_german_catalog(self):
        self.client.force_login(self.user)
        self.client.cookies[LANGUAGE_COOKIE] = "de"
        page = self.client.get(reverse("settings"))
        self.assertContains(page, "Sprache</h2>")
        self.assertContains(page, "Folgt Ihrem Browser, bis Sie eine Sprache wählen.")
