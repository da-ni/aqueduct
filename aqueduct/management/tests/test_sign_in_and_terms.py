from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, modify_settings, override_settings
from django.urls import include, path, reverse
from tos.models import TermsOfService

from aqueduct.urls import urlpatterns as aqueduct_urlpatterns
from management.models import Org, UserProfile
from management.tests.test_api_keys_pages import PAGES

User = get_user_model()

urlpatterns = [path("terms-of-service/", include("tos.urls")), *aqueduct_urlpatterns]


@PAGES
class SignInPageTest(TestCase):
    def test_asks_only_to_sign_in(self):
        page = self.client.get(reverse("sso"))
        self.assertContains(page, ">Sign in to Aqueduct</h1>")
        self.assertContains(page, ">Sign in with SSO</a>")
        self.assertContains(page, "<title>Sign in · Aqueduct AI Gateway</title>")
        main = page.content.decode().split("<main", 1)[1].split("</main>", 1)[0]
        self.assertNotIn("<p", main)


@PAGES
@override_settings(ROOT_URLCONF=__name__)
@modify_settings(MIDDLEWARE={"append": "management.middleware.UserAgreementMiddleware"})
class TermsPromptTest(TestCase):
    def setUp(self):
        self.terms = TermsOfService.objects.create(active=True, content="<p>Be kind.</p>")
        TermsOfService.objects.filter(pk=self.terms.pk).update(
            created=datetime(2026, 10, 17, 9, tzinfo=UTC)
        )
        self.user = User.objects.create_user(username="new", email="new@example.com")
        UserProfile.objects.create(user=self.user, org=Org.objects.create(name="Informatics"))
        self.client.force_login(self.user)

    def test_prompt_has_the_logo_and_footer_but_no_navigation(self):
        page = self.client.get(reverse("tos_check_tos"), {"next": reverse("settings")})
        self.assertContains(page, ">Accept the terms to continue</h1>")
        self.assertContains(page, "Version of <time")
        self.assertContains(page, ">17 Oct 2026</time>")
        self.assertContains(page, "Be kind.")
        self.assertContains(
            page, 'name="accept" value="accept" class="btn btn-primary">Accept</button>'
        )
        self.assertContains(
            page, f'formaction="{reverse("oidc_logout")}">Decline and sign out</button>'
        )
        self.assertContains(page, "Aqueduct</span>")
        self.assertContains(page, "<footer")
        self.assertNotContains(page, "drawer-side")

    def test_accepting_opens_the_page_that_asked(self):
        self.client.get(reverse("settings"))
        response = self.client.post(
            reverse("tos_check_tos"), {"accept": "accept", "next": reverse("settings")}
        )
        self.assertRedirects(response, reverse("settings"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("settings")).status_code, 200)

    def test_declining_signs_out(self):
        self.client.post(reverse("oidc_logout"))
        response = self.client.get(reverse("settings"))
        self.assertRedirects(
            response, f"{reverse('sso')}?next={reverse('settings')}", fetch_redirect_response=False
        )

    def test_terms_page_shows_the_version_in_the_app_date_format(self):
        self.client.logout()
        page = self.client.get(reverse("tos"))
        self.assertContains(page, ">Terms of service</h1>")
        self.assertContains(page, ">17 Oct 2026</time>")
