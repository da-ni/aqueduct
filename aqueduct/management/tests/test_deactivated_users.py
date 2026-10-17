import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from management.models import Org, UserProfile
from management.tests.test_api_keys_pages import PAGES

User = get_user_model()


@PAGES
@override_settings(OIDC_OP_JWKS_ENDPOINT="https://sso.example.com/jwks")
class DeactivatedUserSessionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="leaver", email="leaver@example.com")
        UserProfile.objects.create(user=self.user, org=Org.objects.create(name="Informatics"))

    def test_deactivation_signs_the_user_out_on_the_next_request(self):
        page = reverse("settings")
        for backend in settings.AUTHENTICATION_BACKENDS:
            with self.subTest(backend):
                User.objects.filter(pk=self.user.pk).update(is_active=True)
                self.client.force_login(self.user, backend=backend)
                session = self.client.session
                session["oidc_id_token_expiration"] = time.time() + 3600
                session.save()
                self.assertEqual(self.client.get(page).status_code, 200)

                User.objects.filter(pk=self.user.pk).update(is_active=False)
                self.assertRedirects(
                    self.client.get(page),
                    f"{reverse('sso')}?next={page}",
                    fetch_redirect_response=False,
                )
