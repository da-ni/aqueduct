import json
import time
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gateway.config import get_mcp_config
from gateway.tests.utils.test_runner import get_shared_mock_server
from management.models import (
    Batch,
    FileObject,
    Org,
    ServiceAccount,
    Team,
    TeamMembership,
    Token,
    UserGroup,
    UserProfile,
    VectorStore,
    VectorStoreFile,
)
from mock_api.mock_configs import MockConfig

User = get_user_model()

PAGES = override_settings(MCP_CONFIG_FILE_PATH=Path(settings.BASE_DIR).parent / "test-mcp.json")
NOW = int(time.time())


def make_token(user, name: str, **kwargs) -> Token:
    token = Token(name=name, user=user, **kwargs)
    token._set_new_key()
    token.save()
    return token


@PAGES
class ResourcePagesTestBase(TestCase):
    """`owner` and `teammate` share a team. `teammate` owns its service account key.

    `outsider` is in the same org but not the team, `org_admin` administers the org, and
    `foreign_admin` administers another org.
    """

    def setUp(self):
        self.org = Org.objects.create(name="lab-org")
        self.team = Team.objects.create(name="NLP Lab", org=self.org)
        self.owner = self.user("owner")
        self.teammate = self.user("teammate")
        self.outsider = self.user("outsider")
        self.org_admin = self.user("org-admin", group=UserGroup.ORG_ADMIN)
        self.foreign_admin = self.user(
            "foreign-admin", org=Org.objects.create(name="far-org"), group=UserGroup.ORG_ADMIN
        )
        for user in (self.owner, self.teammate):
            TeamMembership.objects.create(user_profile=user.profile, team=self.team)

        self.owner_key = make_token(self.owner, "laptop")
        pipeline = ServiceAccount.objects.create(name="pipeline", team=self.team)
        self.team_key = make_token(self.teammate, "pipeline", service_account=pipeline)
        self.outsider_key = make_token(self.outsider, "outsider-laptop")

    def user(self, name: str, org: Org | None = None, group: UserGroup = UserGroup.USER):
        user = User.objects.create_user(username=name, email=f"{name}@example.com")
        UserProfile.objects.create(user=user, org=org or self.org).group = group
        return user

    def file(self, token: Token, filename: str, **fields) -> FileObject:
        return FileObject.objects.create(
            id=f"file-{filename}",
            token=token,
            filename=filename,
            bytes=2048,
            created_at=fields.pop("created_at", NOW),
            purpose=fields.pop("purpose", "batch"),
            **fields,
        )

    def batch(self, token: Token, batch_id: str, status: str, **fields) -> Batch:
        return Batch.objects.create(
            id=batch_id,
            token=token,
            input_file=self.file(token, f"{batch_id}.jsonl"),
            completion_window="24h",
            created_at=NOW,
            endpoint="/v1/chat/completions",
            status=status,
            **fields,
        )

    def store(self, token: Token, name: str) -> VectorStore:
        return VectorStore.objects.create(
            id=f"vs_{name}", token=token, name=name, status="completed", created_at=NOW
        )

    def as_user(self, user):
        self.client.force_login(user)
        return self.client

    def assert_goes_to_sign_in(self, response, url: str):
        self.assertRedirects(
            response, f"{reverse('sso')}?next={url}", fetch_redirect_response=False
        )


class FilesPageTest(ResourcePagesTestBase):
    def setUp(self):
        super().setUp()
        self.own_file = self.file(self.owner_key, "prompts.jsonl", preview='{"a": 1}')
        self.team_file = self.file(self.team_key, "team-corpus.jsonl", purpose="user_data")
        self.outsider_file = self.file(self.outsider_key, "secret.jsonl")

    def test_signed_out_goes_to_sign_in(self):
        url = reverse("files")
        self.assert_goes_to_sign_in(self.client.get(url), url)

    def test_owner_sees_own_and_team_files_but_deletes_only_their_own(self):
        response = self.as_user(self.owner).get(reverse("files"))
        self.assertContains(response, "prompts.jsonl")
        self.assertContains(response, "Batch input")
        self.assertContains(response, "team-corpus.jsonl")
        self.assertContains(response, "User data")
        self.assertNotContains(response, "secret.jsonl")
        self.assertContains(response, reverse("file_delete", args=[self.own_file.id]))
        self.assertNotContains(response, reverse("file_delete", args=[self.team_file.id]))
        self.assertContains(response, "Preview prompts.jsonl")
        self.assertContains(response, "2 files · 4.0\xa0KB · max 8 MB each")

    def test_files_are_paged_newest_first_and_the_summary_counts_them_all(self):
        for n in range(50):
            self.file(self.owner_key, f"older-{n}.jsonl", created_at=NOW - 100 - n)
        client = self.as_user(self.owner)
        first = client.get(reverse("files"))
        self.assertContains(first, "prompts.jsonl")
        self.assertContains(first, "older-47.jsonl")
        self.assertNotContains(first, "older-48.jsonl")
        self.assertContains(first, 'href="?page=2">Next</a>')
        second = client.get(reverse("files"), {"page": 2})
        self.assertContains(second, "older-48.jsonl")
        self.assertContains(second, "older-49.jsonl")
        self.assertNotContains(second, "prompts.jsonl")
        for response in (first, second):
            self.assertContains(response, "52 files · 104.0\xa0KB · max 8 MB each")

    def test_a_file_link_opens_the_page_that_holds_the_file(self):
        for n in range(50):
            self.file(self.owner_key, f"newer-{n}.jsonl", created_at=NOW + 100 + n)
        response = self.as_user(self.owner).get(reverse("files"), {"file": self.own_file.id})
        self.assertContains(response, f'id="{self.own_file.id}"')
        self.assertNotContains(response, "newer-49.jsonl")
        # Paging away from the linked file drops `file`, so the link moves.
        self.assertContains(response, 'href="?page=1">Previous</a>')
        # A file the viewer cannot see opens the first page.
        hidden = self.as_user(self.owner).get(reverse("files"), {"file": self.outsider_file.id})
        self.assertContains(hidden, "newer-49.jsonl")
        self.assertNotContains(hidden, "secret.jsonl")

    def test_a_page_that_is_not_a_number_opens_the_first_page(self):
        for page in ("\u00b2", "x", "-1", "0", "999"):
            with self.subTest(page=page):
                response = self.as_user(self.owner).get(reverse("files"), {"page": page})
                self.assertContains(response, "prompts.jsonl")

    def test_outsider_sees_only_their_own_file(self):
        response = self.as_user(self.outsider).get(reverse("files"))
        self.assertContains(response, "secret.jsonl")
        self.assertNotContains(response, "prompts.jsonl")
        self.assertNotContains(response, "team-corpus.jsonl")


class FileDeleteTest(ResourcePagesTestBase):
    def setUp(self):
        super().setUp()
        self.own_file = self.file(self.owner_key, "prompts.jsonl")
        self.team_file = self.file(self.team_key, "team-corpus.jsonl")
        self.mock_server = mock_server = get_shared_mock_server()
        files_api = override_settings(AQUEDUCT_FILES_API_URL=mock_server.base_url)
        files_api.enable()
        self.addCleanup(files_api.disable)

    def url(self, file: FileObject) -> str:
        return reverse("file_delete", args=[file.id])

    def test_signed_out_goes_to_sign_in_and_keeps_the_file(self):
        url = self.url(self.own_file)
        self.assert_goes_to_sign_in(self.client.post(url), url)
        self.assertTrue(FileObject.objects.filter(id=self.own_file.id).exists())

    def test_owner_confirms_and_deletes_their_file(self):
        client = self.as_user(self.owner)
        confirm = client.get(self.url(self.own_file))
        self.assertContains(confirm, "Delete prompts.jsonl?</h1>")
        self.assertContains(confirm, "Batches that use it as input are deleted with it")
        self.assertEqual(confirm.context["shell"].current.label, "Files")
        response = client.post(self.url(self.own_file), follow=True)
        self.assertRedirects(response, reverse("files"))
        self.assertContains(response, "File prompts.jsonl deleted.")
        self.assertFalse(FileObject.objects.filter(id=self.own_file.id).exists())

    def test_deleting_returns_to_the_page_the_owner_was_on(self):
        for n in range(50):
            self.file(self.owner_key, f"older-{n}.jsonl", created_at=NOW - 100 - n)
        client = self.as_user(self.owner)
        listing = client.get(reverse("files"), {"page": 2})
        url = f"{self.url(FileObject.objects.get(id='file-older-49.jsonl'))}?page=2"
        self.assertContains(listing, f'href="{url}"')
        self.assertContains(client.get(url), f'href="{reverse("files")}?page=2"')
        response = client.post(url, follow=True)
        self.assertRedirects(response, f"{reverse('files')}?page=2")
        self.assertContains(response, "older-48.jsonl")

    def test_other_users_and_teammates_get_404_and_keep_the_file(self):
        cases = ((self.outsider, self.own_file), (self.org_admin, self.own_file))
        cases += ((self.owner, self.team_file),)
        for user, file in cases:
            with self.subTest(user=user.username, file=file.filename):
                self.assertEqual(self.as_user(user).post(self.url(file)).status_code, 404)
        kept = FileObject.objects.filter(id__in=[self.own_file.id, self.team_file.id])
        self.assertEqual(kept.count(), 2)

    def test_service_account_key_creator_cannot_delete_team_files(self):
        page = self.as_user(self.teammate).get(reverse("files"))
        self.assertContains(page, "team-corpus.jsonl")
        self.assertNotContains(page, self.url(self.team_file))
        self.assertEqual(
            self.as_user(self.teammate).post(self.url(self.team_file)).status_code, 404
        )
        TeamMembership.objects.filter(user_profile=self.teammate.profile).delete()
        self.assertEqual(
            self.as_user(self.teammate).post(self.url(self.team_file)).status_code, 404
        )
        self.assertTrue(FileObject.objects.filter(id=self.team_file.id).exists())

    def test_upstream_failure_keeps_the_file(self):
        with self.mock_server.patch_external_api(
            f"files/{self.own_file.id}",
            MockConfig(status_code=400, response_data={"error": {"message": "boom"}}),
        ):
            response = self.as_user(self.owner).post(self.url(self.own_file), follow=True)
        self.assertContains(response, "Could not delete prompts.jsonl.")
        self.assertTrue(FileObject.objects.filter(id=self.own_file.id).exists())

    def test_unconfigured_files_api_keeps_the_file(self):
        with self.settings(AQUEDUCT_FILES_API_URL=None):
            response = self.as_user(self.owner).post(self.url(self.own_file), follow=True)
        self.assertContains(response, "The Files API is not configured.")
        self.assertTrue(FileObject.objects.filter(id=self.own_file.id).exists())


class BatchesPageTest(ResourcePagesTestBase):
    def setUp(self):
        super().setUp()
        self.running = self.batch(
            self.owner_key,
            "batch-running",
            "in_progress",
            request_counts={"total": 4000, "completed": 1228, "failed": 12},
        )
        self.team_batch = self.batch(self.team_key, "batch-team", "in_progress")
        self.batch(self.outsider_key, "batch-outsider", "completed")

    def test_signed_out_goes_to_sign_in(self):
        url = reverse("batches")
        self.assert_goes_to_sign_in(self.client.get(url), url)

    def test_owner_sees_progress_and_cancels_only_their_own_batch(self):
        response = self.as_user(self.owner).get(reverse("batches"))
        self.assertContains(response, "batch-running")
        self.assertContains(response, "In progress")
        self.assertContains(response, "1,240 of 4,000 · 12 failed")
        file_id = self.running.input_file.id
        self.assertContains(response, f'href="{reverse("files")}?file={file_id}#{file_id}"')
        self.assertContains(response, "batch-team")
        self.assertNotContains(response, "batch-outsider")
        self.assertContains(response, reverse("batch_cancel", args=[self.running.id]))
        self.assertNotContains(response, reverse("batch_cancel", args=[self.team_batch.id]))
        self.assertContains(response, "Running: you 1/10 · NLP Lab 1/25")

    def test_team_batches_count_against_the_team_not_the_key_s_creator(self):
        response = self.as_user(self.teammate).get(reverse("batches"))
        self.assertContains(response, "Running: you 0/10 · NLP Lab 1/25")

    def test_cancel_asks_with_a_dismiss_button_that_does_not_say_cancel(self):
        response = self.as_user(self.owner).get(reverse("batches"))
        self.assertContains(
            response,
            'data-confirm="Cancel batch-running?" '
            'data-confirm-detail="Requests it has not run yet are dropped." '
            'data-confirm-action="Cancel batch" data-confirm-dismiss="Keep running"',
        )

    def test_a_running_batch_shows_when_it_must_finish_and_a_stopped_one_shows_nothing(self):
        def local(timestamp: int) -> datetime:
            return timezone.localtime(datetime.fromtimestamp(timestamp, tz=UTC))

        soon, later = NOW + 60, NOW + 3 * 86_400
        Batch.objects.filter(id=self.running.id).update(expires_at=soon)
        self.batch(self.owner_key, "batch-later", "validating", expires_at=later)
        self.batch(self.owner_key, "batch-done", "completed", expires_at=soon)
        response = self.as_user(self.owner).get(reverse("batches"))
        today = local(soon).date() == timezone.localdate()
        first = local(soon).strftime("%H:%M" if today else "%-d %b %H:%M")
        self.assertContains(response, f"Finish by {first}")
        self.assertContains(response, f">{local(later):%-d %b %H:%M}</time>")
        self.assertContains(response, "Finish by", count=2)
        self.assertNotContains(response, "Today")
        self.assertContains(response, "Output pending")


class BatchCancelTest(ResourcePagesTestBase):
    def setUp(self):
        super().setUp()
        self.running = self.batch(self.owner_key, "batch-running", "in_progress")
        mock_server = get_shared_mock_server()
        files_api = override_settings(AQUEDUCT_FILES_API_URL=mock_server.base_url)
        files_api.enable()
        self.addCleanup(files_api.disable)

    def status_of(self, batch: Batch) -> str:
        batch.refresh_from_db()
        return batch.status

    def test_signed_out_goes_to_sign_in_and_keeps_the_batch_running(self):
        url = reverse("batch_cancel", args=[self.running.id])
        self.assert_goes_to_sign_in(self.client.post(url), url)
        self.assertEqual(self.status_of(self.running), "in_progress")

    def test_owner_cancels_their_batch(self):
        url = reverse("batch_cancel", args=[self.running.id])
        response = self.as_user(self.owner).post(url, follow=True)
        self.assertRedirects(response, reverse("batches"))
        self.assertContains(response, "is being cancelled")
        self.assertEqual(self.status_of(self.running), "cancelled")

    def test_cancelling_returns_to_the_page_the_owner_was_on(self):
        url = reverse("batch_cancel", args=[self.running.id])
        response = self.as_user(self.owner).post(url, {"page": "2"})
        self.assertRedirects(
            response, f"{reverse('batches')}?page=2", fetch_redirect_response=False
        )
        self.assertEqual(self.status_of(self.running), "cancelled")

    def test_others_get_404_and_the_batch_keeps_running(self):
        team_batch = self.batch(self.team_key, "batch-team", "in_progress")
        cases = ((self.outsider, self.running), (self.org_admin, self.running))
        cases += ((self.owner, team_batch),)
        for user, batch in cases:
            with self.subTest(user=user.username, batch=batch.id):
                url = reverse("batch_cancel", args=[batch.id])
                self.assertEqual(self.as_user(user).post(url).status_code, 404)
                self.assertEqual(self.status_of(batch), "in_progress")

    def test_service_account_key_creator_cannot_cancel_team_batches(self):
        team_batch = self.batch(self.team_key, "batch-team", "in_progress")
        url = reverse("batch_cancel", args=[team_batch.id])
        page = self.as_user(self.teammate).get(reverse("batches"))
        self.assertContains(page, "batch-team")
        self.assertNotContains(page, url)
        self.assertEqual(self.as_user(self.teammate).post(url).status_code, 404)
        TeamMembership.objects.filter(user_profile=self.teammate.profile).delete()
        self.assertEqual(self.as_user(self.teammate).post(url).status_code, 404)
        self.assertEqual(self.status_of(team_batch), "in_progress")

    def test_finished_batch_is_not_cancelled(self):
        done = self.batch(self.owner_key, "batch-done", "completed")
        response = self.as_user(self.owner).post(
            reverse("batch_cancel", args=[done.id]), follow=True
        )
        self.assertContains(response, "cannot be cancelled")
        self.assertEqual(self.status_of(done), "completed")

    def test_get_is_not_allowed(self):
        url = reverse("batch_cancel", args=[self.running.id])
        self.assertEqual(self.as_user(self.owner).get(url).status_code, 405)


class VectorStoresPageTest(ResourcePagesTestBase):
    def setUp(self):
        super().setUp()
        self.own_store = self.store(self.owner_key, "thesis-corpus")
        self.thesis = self.file(self.owner_key, "thesis.pdf", purpose="user_data")
        VectorStoreFile.objects.create(
            id="vsf-1",
            vector_store=self.own_store,
            file_obj=self.thesis,
            status="completed",
            created_at=NOW,
        )
        self.team_store = self.store(self.team_key, "group-papers")
        self.outsider_store = self.store(self.outsider_key, "outsider-notes")

    def test_signed_out_goes_to_sign_in(self):
        for url in (
            reverse("vector_stores"),
            reverse("vector_store_detail", args=[self.own_store.id]),
        ):
            with self.subTest(url=url):
                self.assert_goes_to_sign_in(self.client.get(url), url)

    def test_owner_sees_own_and_team_stores_with_their_owner(self):
        response = self.as_user(self.owner).get(reverse("vector_stores"))
        self.assertContains(response, "thesis-corpus")
        self.assertContains(response, "group-papers")
        self.assertContains(response, "<td>NLP Lab</td>", html=False)
        self.assertContains(response, "<td>You</td>", html=False)
        self.assertNotContains(response, "outsider-notes")
        self.assertContains(response, "Vector stores: you 1/10 · NLP Lab 1/50")

    def test_quotas_cover_only_the_viewers_keys_and_their_own_teams(self):
        response = self.as_user(self.org_admin).get(reverse("vector_stores"))
        self.assertContains(response, "group-papers")
        self.assertContains(response, "<td>outsider@example.com</td>", html=False)
        self.assertContains(response, "Vector stores: you 0/10<")

    def test_detail_pages_the_store_files(self):
        for n in range(50):
            entry_file = self.file(self.owner_key, f"chunk-{n}.pdf", purpose="user_data")
            VectorStoreFile.objects.create(
                id=f"vsf-chunk-{n}",
                vector_store=self.own_store,
                file_obj=entry_file,
                status="completed",
                created_at=NOW + 1 + n,
            )
        url = reverse("vector_store_detail", args=[self.own_store.id])
        first = self.as_user(self.owner).get(url)
        self.assertContains(first, "chunk-49.pdf")
        self.assertNotContains(first, "thesis.pdf")
        self.assertContains(first, "51 files")
        self.assertContains(self.as_user(self.owner).get(url, {"page": 2}), "thesis.pdf")

    def test_detail_lists_the_store_files(self):
        url = reverse("vector_store_detail", args=[self.own_store.id])
        response = self.as_user(self.owner).get(url)
        self.assertContains(response, "thesis-corpus")
        self.assertContains(response, "thesis.pdf")
        self.assertContains(
            response, f'href="{reverse("files")}?file={self.thesis.id}#{self.thesis.id}"'
        )
        self.assertContains(response, "Ready")
        self.assertContains(response, "1 file<")
        self.assertContains(response, "Never expires")
        self.assertNotContains(response, "File batches")

    def test_detail_links_a_file_only_when_the_files_page_lists_it(self):
        team_file = self.file(self.team_key, "papers.pdf", purpose="user_data")
        foreign_file = self.file(self.outsider_key, "borrowed.pdf", purpose="user_data")
        for n, file in enumerate((team_file, foreign_file)):
            VectorStoreFile.objects.create(
                id=f"vsf-{n + 2}",
                vector_store=self.team_store,
                file_obj=file,
                status="completed",
                created_at=NOW,
            )
        url = reverse("vector_store_detail", args=[self.team_store.id])
        link = f'href="{reverse("files")}?file={{}}#{{}}"'.format
        member = self.as_user(self.teammate).get(url)
        self.assertContains(member, link(team_file.id, team_file.id))
        self.assertContains(member, "borrowed.pdf")
        self.assertNotContains(member, link(foreign_file.id, foreign_file.id))
        admin = self.as_user(self.org_admin).get(url)
        self.assertContains(admin, link(foreign_file.id, foreign_file.id))

    def test_detail_of_a_store_outside_scope_shows_not_found(self):
        url = reverse("vector_store_detail", args=[self.own_store.id])
        for user in (self.outsider, self.foreign_admin):
            with self.subTest(user=user.username):
                response = self.as_user(user).get(url)
                self.assertContains(response, "Vector store not found")
                self.assertContains(response, "It doesn't exist or you don't have access.")
                self.assertNotContains(response, "thesis.pdf")


class ResourceVisibilityTest(ResourcePagesTestBase):
    """Each list shows what the request log shows: a user's own and their teams' items, every
    item of the org to its admin, and what the org switch picks to a platform admin."""

    PAGES = ("files", "batches", "vector_stores")

    def setUp(self):
        super().setUp()
        self.far_org = self.foreign_admin.profile.org
        stranger = self.user("stranger", org=self.far_org)
        self.platform_admin = self.user("platform", group=UserGroup.ADMIN)
        for tag, token in (
            ("alpha", self.owner_key),
            ("bravo", self.team_key),
            ("charlie", self.outsider_key),
            ("delta", make_token(stranger, "far-laptop")),
        ):
            self.file(token, f"{tag}.txt", purpose="user_data")
            self.batch(token, f"batch-{tag}", "in_progress")
            self.store(token, f"{tag}-store")

    def assert_lists(self, user, tags: set[str], **params):
        everything = {"alpha", "bravo", "charlie", "delta"}
        names = {"files": "{}.txt", "batches": "batch-{}", "vector_stores": "{}-store"}
        client = self.as_user(user)
        for page in self.PAGES:
            response = client.get(reverse(page), params)
            for tag in sorted(everything):
                with self.subTest(user=user.username, page=page, tag=tag, **params):
                    name = names[page].format(tag)
                    if tag in tags:
                        self.assertContains(response, name)
                    else:
                        self.assertNotContains(response, name)

    def test_user_sees_their_own_items(self):
        self.assert_lists(self.outsider, {"charlie"})

    def test_team_member_sees_their_own_and_their_teams_items(self):
        self.assert_lists(self.owner, {"alpha", "bravo"})
        self.assert_lists(self.teammate, {"bravo"})

    def test_org_admin_sees_every_item_of_their_org(self):
        self.assert_lists(self.org_admin, {"alpha", "bravo", "charlie"})
        self.assert_lists(self.foreign_admin, {"delta"})

    def test_platform_admin_follows_the_org_switch(self):
        self.assert_lists(self.platform_admin, {"alpha", "bravo", "charlie"}, org=self.org.id)
        self.assert_lists(self.platform_admin, {"delta"}, org=self.far_org.id)
        self.assert_lists(self.platform_admin, {"alpha", "bravo", "charlie", "delta"}, org="all")

    def test_others_items_name_their_owner_and_offer_no_delete_or_cancel(self):
        client = self.as_user(self.org_admin)
        files = client.get(reverse("files"))
        self.assertContains(files, " · outsider@example.com")
        self.assertContains(files, " · NLP Lab")
        self.assertNotContains(files, "/delete/")
        batches = client.get(reverse("batches"))
        self.assertContains(batches, "batch-charlie")
        self.assertNotContains(batches, "/cancel/")

    def test_a_german_viewer_s_own_items_name_no_owner(self):
        client = self.as_user(self.owner)
        client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
        for page in ("files", "batches"):
            with self.subTest(page=page):
                response = client.get(reverse(page))
                self.assertContains(response, " · NLP Lab")
                self.assertNotContains(response, " · Sie")

    def test_empty_lists_say_whose_items_they_list(self):
        newcomer = self.user("newcomer")
        lonely_admin = self.user(
            "lonely", org=Org.objects.create(name="empty-org"), group=UserGroup.ORG_ADMIN
        )
        expected = (
            (newcomer, "No files from your keys or your teams' keys yet."),
            (lonely_admin, "No files in empty-org yet."),
        )
        for user, text in expected:
            with self.subTest(user=user.username):
                response = self.as_user(user).get(reverse("files"))
                self.assertIn(text, unescape(response.content.decode()))
                self.assertContains(
                    response, 'href="https://tu-wien-datalab.github.io/aqueduct/api/files/"'
                )
        batches = self.as_user(newcomer).get(reverse("batches")).content.decode()
        self.assertIn("No batches from your keys or your teams' keys yet.", unescape(batches))
        stores = self.as_user(lonely_admin).get(reverse("vector_stores"))
        self.assertContains(stores, "No vector stores in empty-org yet.")


class VectorStoreSyncTest(ResourcePagesTestBase):
    """Syncing stays with the keys a viewer created and the teams `get_teams_for_user` gives:
    their own teams, or every team of the org to an org admin. Other users' stores are
    view-only."""

    def setUp(self):
        super().setUp()
        self.team_store = self.store(self.team_key, "group-papers")
        self.outsider_store = self.store(self.outsider_key, "outsider-notes")

    def list_page(self, user) -> str:
        return self.as_user(user).get(reverse("vector_stores")).content.decode()

    def test_org_admin_syncs_team_stores_but_only_views_other_users_stores(self):
        page = self.list_page(self.org_admin)
        self.assertIn('aria-label="Sync status of group-papers"', page)
        self.assertIn("outsider-notes", page)
        self.assertNotIn('aria-label="Sync status of outsider-notes"', page)
        detail = self.as_user(self.org_admin).get(
            reverse("vector_store_detail", args=[self.outsider_store.id])
        )
        self.assertContains(detail, "outsider-notes")
        self.assertNotContains(detail, "Sync status")

    def test_a_forbidden_sync_changes_nothing(self):
        for name in ("vector_store_card_refresh", "vector_store_detail_refresh"):
            with self.subTest(view=name):
                url = reverse(name, args=[self.outsider_store.id])
                response = self.as_user(self.org_admin).post(url, follow=True)
                self.assertContains(response, "Vector store not found.")
                self.outsider_store.refresh_from_db()
                self.assertEqual(self.outsider_store.status, "completed")
                self.assertEqual(self.outsider_store.name, "outsider-notes")

    def test_owner_syncs_their_store(self):
        self.assertIn('aria-label="Sync status of outsider-notes"', self.list_page(self.outsider))

    def test_creator_of_a_team_key_cannot_sync_its_store_after_leaving_the_team(self):
        TeamMembership.objects.filter(user_profile=self.teammate.profile).delete()
        client = self.as_user(self.teammate)
        with mock.patch("management.views.vector_stores._refresh_vector_store") as refresh:
            for name in ("vector_store_card_refresh", "vector_store_detail_refresh"):
                with self.subTest(view=name):
                    url = reverse(name, args=[self.team_store.id])
                    self.assertContains(client.post(url, follow=True), "Vector store not found.")
        refresh.assert_not_called()


class MCPServersPageTest(ResourcePagesTestBase):
    def test_signed_out_goes_to_sign_in(self):
        url = reverse("mcp_servers")
        self.assert_goes_to_sign_in(self.client.get(url), url)

    def test_user_sees_servers_with_their_url_and_a_client_config(self):
        response = self.as_user(self.owner).get(reverse("mcp_servers"))
        url = "http://testserver/mcp-servers/test-server/mcp"
        self.assertContains(response, f'data-copy="{url}"')
        self.assertNotContains(response, "production")
        self.assertNotContains(response, "Off for")
        self.assertContains(response, f'href="{reverse("tokens")}">API key</a>')

    def test_client_config_lists_every_server_the_viewer_may_use(self):
        def configured(response) -> dict:
            return json.loads(response.context["client_config"])["mcpServers"]

        servers = configured(self.as_user(self.owner).get(reverse("mcp_servers")))
        self.assertEqual(set(servers), set(get_mcp_config()))
        self.assertEqual(
            servers["test-server"],
            {
                "url": "http://testserver/mcp-servers/test-server/mcp",
                "headers": {"Authorization": "Bearer sk-..."},
            },
        )
        self.org.excluded_mcp_servers = ["aqueduct"]
        self.org.save()
        profile = self.owner.profile
        profile.merge_mcp_server_exclusion_lists = True
        profile.save()
        servers = configured(self.as_user(self.owner).get(reverse("mcp_servers")))
        self.assertEqual(set(servers), set(get_mcp_config()) - {"aqueduct"})

    def test_server_unavailable_to_every_key_is_not_listed(self):
        self.org.excluded_mcp_servers = ["aqueduct"]
        self.org.save()
        profile = self.owner.profile
        profile.merge_mcp_server_exclusion_lists = True
        profile.save()
        response = self.as_user(self.owner).get(reverse("mcp_servers"))
        self.assertNotIn("aqueduct", [server.name for server in response.context["servers"]])
        self.assertNotContains(response, "Available with")

    @override_settings(MCP_CONFIG_FILE_PATH="/nonexistent/mcp.json")
    def test_missing_config_shows_the_error(self):
        response = self.as_user(self.owner).get(reverse("mcp_servers"))
        self.assertContains(response, "Unable to load MCP config")
