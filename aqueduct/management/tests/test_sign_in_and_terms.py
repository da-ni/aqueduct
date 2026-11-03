from django.test import modify_settings, override_settings
from django.urls import reverse
from tos.models import TermsOfService

from aqueduct.urls import terms_of_service
from aqueduct.urls import urlpatterns as aqueduct_urlpatterns
from management.tests.helpers import RoleWorld

urlpatterns = [terms_of_service, *aqueduct_urlpatterns]


@override_settings(ROOT_URLCONF=__name__)
@modify_settings(MIDDLEWARE={"append": "management.middleware.UserAgreementMiddleware"})
class TermsPromptTest(RoleWorld):
    def setUp(self):
        TermsOfService.objects.create(active=True, content="<p>Be kind.</p>")
        self.client.force_login(self.solo)

    def accept(self, next_url: str):
        self.client.get(reverse("tokens"))
        return self.client.post(reverse("tos_check_tos"), {"accept": "accept", "next": next_url})

    def test_accepting_opens_the_page_that_asked(self):
        page = f"{reverse('requests')}?span=1d"
        self.assertRedirects(self.accept(page), page, fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("tokens")).status_code, 200)

    def test_accepting_never_leaves_the_site(self):
        for target in (
            "https://evil.example",
            "//evil.example",
            "/\\evil.example",
            "http:evil.example",
            "",
        ):
            with self.subTest(target=target):
                self.assertRedirects(
                    self.accept(target), reverse("usage"), fetch_redirect_response=False
                )

    def test_accepting_with_a_javascript_url_is_refused_by_django(self):
        response = self.accept("javascript:alert(1)")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("Location", response)
