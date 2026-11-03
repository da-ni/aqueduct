from django.urls import reverse

from management.tests.helpers import RoleWorld


class SignedInLoginVisitTest(RoleWorld):
    def test_follows_a_next_on_this_site(self):
        self.client.force_login(self.solo)
        page = f"{reverse('requests')}?span=1d&status=500"
        self.assertRedirects(
            self.client.get(reverse("sso"), {"next": page}), page, fetch_redirect_response=False
        )

    def test_ignores_a_next_that_leaves_the_site(self):
        self.client.force_login(self.solo)
        for unsafe in (
            "https://evil.example",
            "//evil.example",
            "/\\evil.example",
            "http:evil.example",
            "javascript:alert(1)",
            "",
        ):
            with self.subTest(next=unsafe):
                response = self.client.get(reverse("sso"), {"next": unsafe})
                self.assertRedirects(response, reverse("usage"), fetch_redirect_response=False)
