from django.urls import reverse

from management.tests.helpers import RoleWorld


class UsageTokenSelectionTests(RoleWorld):
    def _selected(self, user, key):
        self.client.force_login(user)
        response = self.client.get(reverse("usage"), {"token": key.id})
        self.assertEqual(response.status_code, 200)
        return response

    def test_member_cannot_select_another_members_key(self):
        response = self._selected(self.solo, self.member_key)
        self.assertIsNone(response.context["selected_token"])
        self.assertNotContains(response, self.member_key.name)

    def test_member_selects_own_key_and_team_service_account_key(self):
        for user, key in ((self.member, self.member_key), (self.member, self.pipeline_key)):
            with self.subTest(user=user.username, key=key.name):
                self.assertEqual(self._selected(user, key).context["selected_token"], key)

    def test_org_admin_selects_any_key_of_the_org(self):
        self.assertEqual(
            self._selected(self.org_admin, self.solo_key).context["selected_token"], self.solo_key
        )

    def test_org_admin_cannot_select_a_key_of_another_org(self):
        self.assertIsNone(
            self._selected(self.org_admin, self.foreign_key).context["selected_token"]
        )
