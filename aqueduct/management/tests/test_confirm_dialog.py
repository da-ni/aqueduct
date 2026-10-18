from django.template import Context, Template
from django.test import SimpleTestCase
from django.urls import reverse

from management.models import Team, Token
from management.tests.helpers import RoleWorld, make_token

KEY = "Apps using it stop working, and its requests, files, batches, and vector stores are deleted."
SERVICE_ACCOUNT = (
    "Its key stops working, and the key&#x27;s requests, files, batches, and vector stores are "
    "deleted."
)


def asks(title: str, detail: str, action: str = "Delete") -> str:
    return f'data-confirm="{title}" data-confirm-detail="{detail}" data-confirm-action="{action}"'


class ConfirmDialogTest(RoleWorld):
    def test_signed_in_pages_carry_one_dialog_and_signed_out_pages_none(self):
        page = self.as_user(self.member).get(reverse("tokens"))
        self.assertContains(page, '<dialog id="confirm-dialog"', count=1)
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("sso")), "confirm-dialog")

    def test_key_delete_asks_on_the_list_the_detail_and_the_fallback_page(self):
        key = make_token(self.member, "laptop")
        delete = reverse("token_delete", args=[key.id])
        client = self.as_user(self.member)
        for page in (reverse("tokens"), reverse("token_detail", args=[key.id])):
            with self.subTest(page=page):
                self.assertContains(
                    client.get(page), f'href="{delete}" {asks("Delete laptop?", KEY)}'
                )
        fallback = client.get(delete)
        self.assertContains(fallback, ">Delete laptop?</h1>")
        self.assertContains(fallback, f"<p>{KEY}</p>")
        self.assertContains(fallback, '<button type="submit" class="btn btn-error">Delete</button>')
        response = client.post(delete, follow=True)
        self.assertRedirects(response, reverse("tokens"))
        self.assertContains(response, "API key laptop deleted.")
        self.assertFalse(Token.objects.filter(id=key.id).exists())

    def test_service_account_delete_asks_on_the_team_page_and_the_fallback_page(self):
        delete = reverse("service_account_delete", args=[self.pipeline.id])
        client = self.as_user(self.team_admin)
        team_page = client.get(reverse("team", args=[self.team.id]))
        self.assertContains(
            team_page, f'href="{delete}" {asks("Delete pipeline?", SERVICE_ACCOUNT)}'
        )
        self.assertContains(client.get(delete), f"<p>{SERVICE_ACCOUNT}</p>")
        response = client.post(delete, follow=True)
        self.assertRedirects(response, reverse("team", args=[self.team.id]))
        self.assertContains(response, "Service account pipeline deleted.")

    def test_team_delete_counts_the_service_accounts_that_stop_working(self):
        delete = reverse("team_delete", args=[self.team.id])
        client = self.as_user(self.org_admin)
        one = (
            "Its 1 service account is deleted with its key and the key&#x27;s requests, files, "
            "batches, and vector stores. Members keep their personal keys."
        )
        self.assertContains(
            client.get(reverse("team_edit", args=[self.team.id])),
            f'href="{delete}" {asks("Delete NLP Lab?", one)}',
        )
        self.assertContains(
            client.get(reverse("team_edit", args=[self.side_team.id])),
            asks("Delete Robotics?", "Members keep their personal keys."),
        )
        self.assertContains(client.get(delete), f"<p>{one}</p>")
        self.assertContains(
            client.get(delete), f'href="{reverse("team", args=[self.team.id])}">Cancel</a>'
        )
        client.post(delete)
        self.assertFalse(Team.objects.filter(id=self.team.id).exists())

    def test_member_removal_asks_in_the_dialog(self):
        page = self.as_user(self.team_admin).get(reverse("team", args=[self.team.id]))
        question = asks(
            "Remove member@example.com?",
            "They lose access to the team and its service accounts.",
            "Remove",
        )
        self.assertContains(page, question)
        self.assertNotContains(page, "confirm('Remove member")

    def test_question_escapes_names(self):
        make_token(self.member, '<b>"x"</b>')
        page = self.as_user(self.member).get(reverse("tokens"))
        self.assertContains(page, 'data-confirm="Delete &lt;b&gt;&quot;x&quot;&lt;/b&gt;?"')


class ConfirmTagTest(SimpleTestCase):
    def render(self, tag: str, **context: str) -> str:
        return Template("{% load ui %}" + tag).render(Context(context))

    def test_the_dismiss_button_says_cancel_unless_told_otherwise(self):
        self.assertEqual(self.render('{% confirm "Delete x?" %}'), asks("Delete x?", ""))
        self.assertEqual(
            self.render('{% confirm "Stop x?" "" "Stop" dismiss=keep %}', keep="Keep <going>"),
            asks("Stop x?", "", "Stop") + ' data-confirm-dismiss="Keep &lt;going&gt;"',
        )
