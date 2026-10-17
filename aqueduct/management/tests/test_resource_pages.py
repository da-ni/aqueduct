import json
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from gateway.config import get_files_api_client, get_mcp_config
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

PAGES = override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    MCP_CONFIG_FILE_PATH=Path(settings.BASE_DIR).parent / "test-mcp.json",
)
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
        self.assertContains(response, "2 files · 4.0\xa0KB · up to 8 MB per file")

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
            self.assertContains(response, "52 files · 104.0\xa0KB · up to 8 MB per file")

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
        get_files_api_client.cache_clear()
        self.addCleanup(get_files_api_client.cache_clear)
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
        self.assertContains(
            response, "2 batches · Your keys: 1 of 10 running · NLP Lab: 1 of 25 running"
        )


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
        self.assertContains(response, "NLP Lab")
        self.assertContains(response, "Yours")
        self.assertContains(response, "1 of 1,000")
        self.assertNotContains(response, "outsider-notes")
        self.assertContains(
            response, "Your keys: 1 of 10 vector stores · NLP Lab: 1 of 50 vector stores"
        )

    def test_org_admin_sees_the_team_stores_of_their_org_only(self):
        response = self.as_user(self.org_admin).get(reverse("vector_stores"))
        self.assertContains(response, "group-papers")
        self.assertNotContains(response, "thesis-corpus")
        foreign = self.as_user(self.foreign_admin).get(reverse("vector_stores"))
        self.assertNotContains(foreign, "group-papers")

    def test_quotas_cover_only_the_viewers_keys_and_their_own_teams(self):
        response = self.as_user(self.org_admin).get(reverse("vector_stores"))
        self.assertContains(response, "group-papers")
        self.assertContains(response, "Your keys: 0 of 10 vector stores")
        self.assertNotContains(response, "NLP Lab: 1 of 50 vector stores")

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
        self.assertContains(first, "51 <span")
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

    def test_detail_links_a_file_only_when_the_files_page_lists_it(self):
        team_file = self.file(self.team_key, "papers.pdf", purpose="user_data")
        VectorStoreFile.objects.create(
            id="vsf-2",
            vector_store=self.team_store,
            file_obj=team_file,
            status="completed",
            created_at=NOW,
        )
        url = reverse("vector_store_detail", args=[self.team_store.id])
        link = f'href="{reverse("files")}?file={team_file.id}#{team_file.id}"'
        member = self.as_user(self.teammate).get(url)
        self.assertContains(member, link)
        # An org admin sees the team's store but not the team's files.
        admin = self.as_user(self.org_admin).get(url)
        self.assertContains(admin, "papers.pdf")
        self.assertNotContains(admin, link)
        self.assertNotContains(self.as_user(self.org_admin).get(reverse("files")), "papers.pdf")

    def test_detail_of_a_store_outside_scope_shows_not_found(self):
        url = reverse("vector_store_detail", args=[self.own_store.id])
        for user in (self.outsider, self.foreign_admin):
            with self.subTest(user=user.username):
                response = self.as_user(user).get(url)
                self.assertContains(response, "Vector store not found")
                self.assertNotContains(response, "thesis.pdf")


class MCPServersPageTest(ResourcePagesTestBase):
    def setUp(self):
        super().setUp()
        get_mcp_config.cache_clear()
        self.addCleanup(get_mcp_config.cache_clear)

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
