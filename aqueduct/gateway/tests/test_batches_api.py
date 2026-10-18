import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from gateway.config import get_router_config
from gateway.tests.utils import _build_chat_headers, _build_chat_payload
from gateway.tests.utils.base import GatewayBatchesTestCase
from management.models import Batch as BatchModel
from management.models import BatchStatus, FileObject, Org, ServiceAccount, Team, Token, UserProfile
from management.models import Request as RequestModel
from management.tests.helpers import router_config
from mock_api.mock_configs import MockConfig, MockPlainTextConfig, batch_detail_response

User = get_user_model()


class TestBatchesAPI(GatewayBatchesTestCase):
    def test_invalid_json(self):
        """POST /batches with invalid JSON returns 400."""
        # Send malformed JSON in the body
        resp = self.client.post(
            "/batches", data="{not: 'json'}", headers=self.headers, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("error", body)
        # Should indicate invalid JSON
        error_message = body.get("error", {}).get("message", "")
        self.assertTrue(
            "Invalid JSON" in error_message, f"Unexpected error message: {error_message}"
        )

    def test_nonexistent_input_file(self):
        """POST /batches with a non-existent file ID returns 404."""
        payload = {
            "input_file_id": "does_not_exist",
            "completion_window": "24h",
            "endpoint": self.url_chat,
        }
        resp = self.client.post(
            "/batches",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        err = resp.json()
        self.assertIn("error", err)
        error = err.get("error", {})
        self.assertEqual(error.get("message"), "Input file not found.")

    def test_list_empty_batches(self):
        """GET /batches when there are no batches should return empty list."""
        resp = self.client.get("/batches", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("data", data)
        self.assertEqual(data.get("data"), [])

    def test_list_batches_different_tokens(self):

        # Create batch under token1 (personal token, self.headers)
        fid1 = self._create_jsonl_file()
        token1 = Token.objects.get(pk=1)
        b1 = BatchModel.objects.create(
            completion_window="24h",
            created_at=1773058900,
            endpoint=self.url_chat,
            id="batch-mock-1",
            input_file_id=fid1,
            status=BatchStatus.IN_PROGRESS,
            token=token1,
        )

        # Build headers for a second token (from fixture pk=2, SA token for TUna)
        token2 = Token.objects.get(pk=2)
        # Generate a fresh secret for token2 (update key_hash/preview) and authenticate with raw key
        secret2 = token2._set_new_key()
        token2.save(update_fields=["key_hash", "key_preview"])
        headers2 = _build_chat_headers(secret2)
        headers2.pop("Content-Type", None)

        # Create batch under token2 (SA token)
        fid2 = self._create_jsonl_file(headers=headers2)
        b2 = BatchModel.objects.create(
            completion_window="24h",
            created_at=1773058900,
            endpoint=self.url_chat,
            id="batch-mock-2",
            input_file_id=fid2,
            status=BatchStatus.IN_PROGRESS,
            token=token2,
        )

        resp = self.client.get("/batches", headers=self.headers)
        ids1 = [b["id"] for b in resp.json().get("data", [])]
        self.assertCountEqual(ids1, [b1.id])

        # SA token (token2) only sees team-scoped batches (only b2, not the personal b1)
        resp = self.client.get("/batches", headers=headers2)
        ids2 = [b["id"] for b in resp.json().get("data", [])]
        self.assertCountEqual(ids2, [b2.id])

    def test_list_batches_with_output_and_error_files(self):
        """GET /batches returns 200 when a finished batch has output/error files."""
        token1 = Token.objects.get(pk=1)
        fid = self._create_jsonl_file()
        now = 1773058900
        f_out = FileObject.objects.create(
            id="file-test-output",
            bytes=100,
            created_at=now,
            filename="output.jsonl",
            purpose="batch_output",
            token=token1,
        )
        f_err = FileObject.objects.create(
            id="file-test-error",
            bytes=50,
            created_at=now,
            filename="error.jsonl",
            purpose="batch_output",
            token=token1,
        )
        b1 = BatchModel.objects.create(
            completion_window="24h",
            created_at=now,
            endpoint=self.url_chat,
            id="batch-test-completed",
            input_file_id=fid,
            output_file=f_out,
            error_file=f_err,
            status=BatchStatus.COMPLETED,
            token=token1,
        )

        resp = self.client.get("/batches", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        data = resp.json().get("data", [])
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], b1.id)
        self.assertEqual(data[0]["output_file_id"], f_out.id)
        self.assertEqual(data[0]["error_file_id"], f_err.id)

    def test_max_user_batches_limit(self):
        """POST /batches should enforce MAX_USER_BATCHES and reject the fourth batch."""

        # Create MAX_USER_BATCHES batches in the database
        file_id = self._create_jsonl_file(name="limit")
        created_ids = []
        existing_batches = []
        token = Token.objects.get(pk=1)
        for i in range(settings.MAX_USER_BATCHES):
            existing_batches.append(
                BatchModel(
                    completion_window="24h",
                    created_at=1773058900,
                    endpoint=self.url_chat,
                    id=f"batch-mock-{i}",
                    input_file_id=file_id,
                    status=BatchStatus.IN_PROGRESS,
                    token=token,
                )
            )
            created_ids.append(f"batch-mock-{i}")
        BatchModel.objects.bulk_create(existing_batches)

        # The next batch should be rejected with 403
        batch_payload = {
            "input_file_id": file_id,
            "completion_window": "24h",
            "endpoint": self.url_chat,
        }
        over = self.client.post(
            "/batches",
            data=json.dumps(batch_payload),
            headers=self.headers,
            content_type="application/json",
        )
        self.assertEqual(over.status_code, 403)
        err = over.json()
        error = err.get("error", {})
        self.assertEqual(error.get("message"), f"Batch limit reached ({settings.MAX_USER_BATCHES})")

        # Verify that only MAX_USER_BATCHES batches exist
        resp = self.client.get("/batches", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        ids_list = [b["id"] for b in resp.json().get("data", [])]
        self.assertCountEqual(ids_list, created_ids)

    def test_max_user_batches_limit_after_cancel(self):
        """Cancelling an active batch frees up a slot for a new batch."""

        # Create MAX_USER_BATCHES batches in the database
        file_id = self._create_jsonl_file(name="limit")
        token = Token.objects.get(pk=1)
        existing_batches = [
            BatchModel(
                completion_window="24h",
                created_at=1773058900,
                endpoint=self.url_chat,
                id=f"batch-mock-{i}",
                input_file_id=file_id,
                status=BatchStatus.IN_PROGRESS,
                token=token,
            )
            for i in range(settings.MAX_USER_BATCHES)
        ]
        BatchModel.objects.bulk_create(existing_batches)

        # Next batch should be blocked
        batch_payload = {
            "input_file_id": file_id,
            "completion_window": "24h",
            "endpoint": self.url_chat,
        }
        blocked = self.client.post(
            "/batches",
            data=json.dumps(batch_payload),
            headers=self.headers,
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 403)

        # Cancel one of the active batches
        to_cancel = existing_batches[0].id
        cancel_resp = self.client.post(f"/batches/{to_cancel}/cancel", headers=self.headers)
        self.assertEqual(cancel_resp.status_code, 200)

        # Now a new batch should succeed
        again = self.client.post(
            "/batches",
            data=json.dumps(batch_payload),
            headers=self.headers,
            content_type="application/json",
        )
        self.assertEqual(again.status_code, 200)

    def test_get_nonexistent_batch(self):
        """GET /batches/{id} for a non-existent batch returns 404."""
        resp = self.client.get("/batches/nonexistent", headers=self.headers)
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        error = body.get("error", {})
        self.assertEqual(error.get("message"), "Batch not found.")

    def test_cancel_nonexistent_batch(self):
        """POST /batches/{id}/cancel for non-existent batch returns 404."""
        resp = self.client.post("/batches/nonexistent/cancel", headers=self.headers)
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        error = body.get("error", {})
        self.assertEqual(error.get("message"), "Batch not found.")

    def test_cancel_batch_with_session_auth(self):
        """POST /batches/{id}/cancel works for authenticated UI session without Bearer token."""
        file_id = self._create_jsonl_file(name="session-auth")
        token = Token.objects.get(pk=1)
        batch = BatchModel.objects.create(
            completion_window="24h",
            created_at=1773058900,
            endpoint=self.url_chat,
            id="batch-session-auth",
            input_file_id=file_id,
            status=BatchStatus.IN_PROGRESS,
            token=token,
        )

        self.client.force_login(User.objects.get(pk=1))
        resp = self.client.post(f"/batches/{batch.id}/cancel")

        self.assertEqual(resp.status_code, 200)
        batch.refresh_from_db()
        self.assertEqual(batch.status, BatchStatus.CANCELLED)

    def test_session_cancel_acts_through_the_user_s_personal_key(self):
        user = User.objects.create_user(username="leaver", email="leaver@example.com")
        UserProfile.objects.create(user=user, org=Org.objects.get(name="E060"))
        old_team = Team.objects.create(name="Old team", org=Org.objects.get(name="E060"))
        sa = ServiceAccount.objects.create(name="old", team=old_team)
        sa_token = Token(name="old team key", user=user, service_account=sa)
        sa_token._set_new_key()
        sa_token.save()
        personal = Token(name="laptop", user=user)
        personal._set_new_key()
        personal.save()
        batch = BatchModel.objects.create(
            completion_window="24h",
            created_at=1773058900,
            endpoint=self.url_chat,
            id="batch-personal",
            input_file=FileObject.objects.create(
                id="file-personal",
                bytes=1,
                created_at=1773058900,
                filename="in.jsonl",
                purpose="batch",
                token=personal,
                upstream_url=settings.AQUEDUCT_FILES_API_URL,
            ),
            status=BatchStatus.IN_PROGRESS,
            token=personal,
        )

        self.client.force_login(user)
        resp = self.client.post(f"/batches/{batch.id}/cancel")

        self.assertEqual(resp.status_code, 200, resp.content)

    def _session_batch(self, *key_expiries) -> tuple:
        user = User.objects.create_user(username="keeper", email="keeper@example.com")
        UserProfile.objects.create(user=user, org=Org.objects.get(name="E060"))
        keys = []
        for i, expires_at in enumerate(key_expiries):
            key = Token(name=f"key {i}", user=user, expires_at=expires_at)
            key._set_new_key()
            key.save()
            keys.append(key)
        batch = BatchModel.objects.create(
            completion_window="24h",
            created_at=1773058900,
            endpoint=self.url_chat,
            id="batch-session",
            input_file=FileObject.objects.create(
                id="file-session",
                bytes=1,
                created_at=1773058900,
                filename="in.jsonl",
                purpose="batch",
                token=keys[0],
                upstream_url=settings.AQUEDUCT_FILES_API_URL,
            ),
            status=BatchStatus.IN_PROGRESS,
            token=keys[0],
        )
        self.client.force_login(user)
        return batch, keys

    def test_session_skips_an_expired_personal_key(self):
        batch, (_, live) = self._session_batch(timezone.now() - timedelta(days=1), None)

        resp = self.client.post(f"/batches/{batch.id}/cancel")

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(RequestModel.objects.get(path=f"/batches/{batch.id}/cancel").token, live)

    def test_session_whose_personal_keys_all_expired_is_refused(self):
        batch, _ = self._session_batch(timezone.now() - timedelta(days=1))

        resp = self.client.post(f"/batches/{batch.id}/cancel")

        self.assertEqual(resp.status_code, 401, resp.content)
        batch.refresh_from_db()
        self.assertEqual(batch.status, BatchStatus.IN_PROGRESS)


class TestBatchesServiceAccountAPI(GatewayBatchesTestCase):
    """Tests for service account team-scoped access control on the batches API."""

    def _setup_two_sa_tokens_same_team(
        self,
    ) -> tuple[dict, dict, ServiceAccount, ServiceAccount, Token, Token]:
        """
        Set up two service account tokens on the same team (Whale).

        Returns (sa_token1_headers, sa_token2_headers, sa1, sa2, token1, token2).
        Token pk=2 is already SA for TUna on team Whale (from fixture).
        We create a second SA on the same team with a new token.
        """
        team = Team.objects.get(name="Whale")

        # Token pk=2 is already a SA token for TUna on team Whale
        token1 = Token.objects.get(pk=2)
        sa1 = token1.service_account
        secret1 = token1._set_new_key()
        token1.save(update_fields=["key_hash", "key_preview"])
        headers1 = _build_chat_headers(secret1)
        headers1.pop("Content-Type", None)

        # Create a second service account on the same team
        sa2 = ServiceAccount.objects.create(name="SecondSA", team=team)
        user = token1.user  # same user for simplicity
        token2 = Token(name="Token for SecondSA", user=user, service_account=sa2)
        secret2 = token2._set_new_key()
        token2.save()
        headers2 = _build_chat_headers(secret2)
        headers2.pop("Content-Type", None)

        return headers1, headers2, sa1, sa2, token1, token2

    def _setup_cross_team_sa_token(self) -> tuple[dict, ServiceAccount, Token]:
        """
        Set up a service account token on a different team.

        Returns (other_team_headers, other_sa, other_token).
        """

        org = Org.objects.get(name="E060")
        other_team = Team.objects.create(name="Dolphin", org=org)

        # Create a new user for the other team
        other_user = User.objects.create_user(username="OtherTeamUser", email="other@team.com")
        UserProfile.objects.create(user=other_user, org=org)

        other_sa = ServiceAccount.objects.create(name="OtherTeamSA", team=other_team)
        other_token = Token(name="Other team token", user=other_user, service_account=other_sa)
        other_secret = other_token._set_new_key()
        other_token.save()
        other_headers = _build_chat_headers(other_secret)
        other_headers.pop("Content-Type", None)

        return other_headers, other_sa, other_token

    def test_create_batch_sa_cross_token_file(self):
        """SA token can create batch using file uploaded by another SA token on same team."""

        headers1, headers2, _sa1, _sa2, _token1, token2 = self._setup_two_sa_tokens_same_team()

        # Upload file with SA token 1
        file_id = self._create_jsonl_file(name="sa1", headers=headers1)

        # Create batch with SA token 2 using SA token 1's file
        resp = self.client.post(
            "/batches",
            data=json.dumps(
                {"input_file_id": file_id, "completion_window": "24h", "endpoint": self.url_chat}
            ),
            headers=headers2,
            content_type="application/json",
        )
        self.assertEqual(
            resp.status_code, 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        )
        batch_id = resp.json()["id"]
        batch = BatchModel.objects.get(id=batch_id)
        self.assertEqual(batch.token, token2)
        self.assertEqual(batch.input_file_id, file_id)

    def test_create_batch_sa_cross_team_denied(self):
        """SA token cannot create a batch using a file from a different team."""

        headers1, _, _sa1, _sa2, _token1, _token2 = self._setup_two_sa_tokens_same_team()
        other_headers, _other_sa, _other_token = self._setup_cross_team_sa_token()

        # Upload file with SA token on team Whale
        file_id = self._create_jsonl_file(headers=headers1)

        # Try to create batch with SA token on team Dolphin using Whale's file
        resp = self.client.post(
            "/batches",
            data=json.dumps(
                {"input_file_id": file_id, "completion_window": "24h", "endpoint": self.url_chat}
            ),
            headers=other_headers,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["message"], "Input file not found.")

    def test_list_batches_sa_team_scope(self):
        """SA tokens on the same team can see each other's batches."""

        headers1, headers2, _sa1, _sa2, token1, _token2 = self._setup_two_sa_tokens_same_team()

        # Upload file and create batch with SA token 1
        file_id1 = self._create_jsonl_file(name="sa1", headers=headers1)
        b1 = BatchModel.objects.create(
            completion_window="24h",
            created_at=1773058900,
            endpoint=self.url_chat,
            id="batch-mock-1",
            input_file_id=file_id1,
            status=BatchStatus.IN_PROGRESS,
            token=token1,
        )
        batch_id1 = b1.id

        # Upload file and create batch with SA token 2
        file_id2 = self._create_jsonl_file(name="sa2", headers=headers2)
        b2 = BatchModel.objects.create(
            completion_window="24h",
            created_at=1773058900,
            endpoint=self.url_chat,
            id="batch-mock-2",
            input_file_id=file_id2,
            status=BatchStatus.IN_PROGRESS,
            token=token1,
        )
        batch_id2 = b2.id

        # SA token 1 should see both batches
        resp = self.client.get("/batches", headers=headers1)
        self.assertEqual(resp.status_code, 200)
        ids = [b["id"] for b in resp.json()["data"]]
        self.assertCountEqual(ids, [batch_id1, batch_id2])

        # SA token 2 should also see both batches
        resp = self.client.get("/batches", headers=headers2)
        self.assertEqual(resp.status_code, 200)
        ids = [b["id"] for b in resp.json()["data"]]
        self.assertCountEqual(ids, [batch_id1, batch_id2])

    def test_retrieve_batch_sa_team_scope(self):
        """SA token can retrieve a batch created by another SA token on the same team."""

        headers1, headers2, _sa1, _sa2, token1, _token2 = self._setup_two_sa_tokens_same_team()

        # Upload file and create batch with SA token 1
        file_id = self._create_jsonl_file(name="sa1", headers=headers1)
        resp = self.client.post(
            "/batches",
            data=json.dumps(
                {"input_file_id": file_id, "completion_window": "24h", "endpoint": self.url_chat}
            ),
            headers=headers1,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        batch_id = resp.json()["id"]

        # SA token 2 should be able to retrieve it
        resp = self.client.get(f"/batches/{batch_id}", headers=headers2)
        self.assertEqual(resp.status_code, 200)
        batch = BatchModel.objects.get(id=batch_id)
        self.assertEqual(batch.token, token1)

    def test_two_team_keys_open_a_finished_batch_and_share_its_output_file(self):
        headers1, headers2, *_ = self._setup_two_sa_tokens_same_team()
        file_id = self._create_jsonl_file(name="sa1", headers=headers1)
        resp = self.client.post(
            "/batches",
            data=json.dumps(
                {"input_file_id": file_id, "completion_window": "24h", "endpoint": self.url_chat}
            ),
            headers=headers1,
            content_type="application/json",
        )
        batch_id = resp.json()["id"]
        finished = {
            **batch_detail_response((batch_id,)),
            "status": "completed",
            "output_file_id": "file-output",
        }

        with self.mock_server.patch_external_api(
            f"batches/{batch_id}", MockConfig(response_data=finished)
        ):
            for headers in (headers1, headers2):
                resp = self.client.get(f"/batches/{batch_id}", headers=headers)
                self.assertEqual(resp.status_code, 200, resp.content)
                self.assertEqual(resp.json()["output_file_id"], "file-output")

        self.assertEqual(FileObject.objects.filter(id="file-output").count(), 1)
        self.assertEqual(BatchModel.objects.get(id=batch_id).output_file_id, "file-output")

    def test_cancel_batch_sa_team_scope(self):
        """SA token can cancel a batch created by another SA token on the same team."""

        headers1, headers2, _sa1, _sa2, _token1, _token2 = self._setup_two_sa_tokens_same_team()

        # Upload file and create batch with SA token 1
        file_id = self._create_jsonl_file(name="sa1", headers=headers1)
        resp = self.client.post(
            "/batches",
            data=json.dumps(
                {"input_file_id": file_id, "completion_window": "24h", "endpoint": self.url_chat}
            ),
            headers=headers1,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        batch_id = resp.json()["id"]

        # SA token 2 should be able to cancel it
        resp = self.client.post(f"/batches/{batch_id}/cancel", headers=headers2)
        self.assertEqual(resp.status_code, 200)
        batch = BatchModel.objects.get(id=batch_id)
        self.assertEqual(batch.status, BatchStatus.CANCELLED)

    def test_personal_token_cannot_see_sa_team_batches(self):
        """A personal token cannot see batches created by SA tokens, even if same user."""

        # Token pk=2 is a SA token (TUna, team Whale, user=1)
        sa_token = Token.objects.get(pk=2)
        sa_secret = sa_token._set_new_key()
        sa_token.save(update_fields=["key_hash", "key_preview"])
        sa_headers = _build_chat_headers(sa_secret)
        sa_headers.pop("Content-Type", None)

        # Upload file and create batch with SA token
        file_id = self._create_jsonl_file(name="sa", headers=sa_headers)
        resp = self.client.post(
            "/batches",
            data=json.dumps(
                {"input_file_id": file_id, "completion_window": "24h", "endpoint": self.url_chat}
            ),
            headers=sa_headers,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        sa_batch_id = resp.json()["id"]

        # Create a new personal token for a different user so it can't see SA batches
        personal_secret, _ = self.create_new_user()
        personal_headers = _build_chat_headers(personal_secret)
        personal_headers.pop("Content-Type", None)

        # Personal token should not see the SA batch
        resp = self.client.get("/batches", headers=personal_headers)
        self.assertEqual(resp.status_code, 200)
        ids = [b["id"] for b in resp.json()["data"]]
        self.assertNotIn(sa_batch_id, ids)

        # Personal token should not be able to retrieve the SA batch
        resp = self.client.get(f"/batches/{sa_batch_id}", headers=personal_headers)
        self.assertEqual(resp.status_code, 404)

        # Personal token should not be able to cancel the SA batch
        resp = self.client.post(f"/batches/{sa_batch_id}/cancel", headers=personal_headers)
        self.assertEqual(resp.status_code, 404)

    def test_creator_s_personal_key_cannot_reach_the_team_s_batches(self):
        sa_token = Token.objects.get(pk=2)
        sa_headers = {"Authorization": f"Bearer {sa_token._set_new_key()}"}
        sa_token.save(update_fields=["key_hash", "key_preview"])
        team_file = self._create_jsonl_file(name="team", headers=sa_headers)
        own_file = self._create_jsonl_file(name="own")
        batch_ids = {}
        for headers, file_id in ((sa_headers, team_file), (self.headers, own_file)):
            resp = self.client.post(
                "/batches",
                data=json.dumps(
                    {
                        "input_file_id": file_id,
                        "completion_window": "24h",
                        "endpoint": self.url_chat,
                    }
                ),
                headers=headers,
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 200)
            batch_ids[file_id] = resp.json()["id"]

        for headers, other_file in ((self.headers, team_file), (sa_headers, own_file)):
            with self.subTest(other_file=other_file):
                other_batch = batch_ids[other_file]
                listed = self.client.get("/batches", headers=headers).json()["data"]
                self.assertNotIn(other_batch, [b["id"] for b in listed])
                resp = self.client.get(f"/batches/{other_batch}", headers=headers)
                self.assertEqual(resp.status_code, 404)
                resp = self.client.post(f"/batches/{other_batch}/cancel", headers=headers)
                self.assertEqual(resp.status_code, 404)
                resp = self.client.post(
                    "/batches",
                    data=json.dumps(
                        {
                            "input_file_id": other_file,
                            "completion_window": "24h",
                            "endpoint": self.url_chat,
                        }
                    ),
                    headers=headers,
                    content_type="application/json",
                )
                self.assertEqual(resp.status_code, 404)
        self.assertEqual(BatchModel.objects.count(), 2)

    def test_team_batches_do_not_count_against_the_creator_s_personal_limit(self):
        sa_token = Token.objects.get(pk=2)
        sa_headers = {"Authorization": f"Bearer {sa_token._set_new_key()}"}
        sa_token.save(update_fields=["key_hash", "key_preview"])
        team_file = self._create_jsonl_file(name="team", headers=sa_headers)
        body = {"input_file_id": team_file, "completion_window": "24h", "endpoint": self.url_chat}
        resp = self.client.post(
            "/batches", data=json.dumps(body), headers=sa_headers, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)

        body["input_file_id"] = self._create_jsonl_file(name="own")
        with self.settings(MAX_USER_BATCHES=1):
            resp = self.client.post(
                "/batches",
                data=json.dumps(body),
                headers=self.headers,
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)


class TestCatchRouterExceptionsIntegration(GatewayBatchesTestCase):
    def test_batches_post_bad_request_error(self):
        """Test batches POST view catches Bad Request (400) from upstream."""
        # Create and upload a test file
        content = b'{"custom_id": "test"}\n'
        f = SimpleUploadedFile("test.jsonl", content, content_type="application/jsonl")
        resp = self.client.post(
            self.url_files, {"file": f, "purpose": "batch"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 200)
        file_id = resp.json()["id"]

        # Patch upstream batches endpoint to return 400
        batches_url = reverse("gateway:batches")
        bad_request = MockConfig(
            status_code=400,
            response_data={
                "error": {
                    "message": "Invalid endpoint",
                    "type": "invalid_request_error",
                    "param": "endpoint",
                    "code": "invalid_value",
                }
            },
        )
        with self.mock_server.patch_external_api(batches_url, bad_request):
            # Attempt to create batch
            resp = self.client.post(
                batches_url,
                data=json.dumps(
                    {
                        "input_file_id": file_id,
                        "completion_window": "24h",
                        "endpoint": self.url_chat,
                    }
                ),
                content_type="application/json",
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid endpoint", resp.json()["error"]["message"])

    def test_batch_get_bad_request_error(self):
        """Test batch GET view catches Bad Request (400) from upstream."""
        # Create and upload a test file
        content = b'{"custom_id": "test"}\n'
        f = SimpleUploadedFile("test.jsonl", content, content_type="application/jsonl")
        resp = self.client.post(
            self.url_files, {"file": f, "purpose": "batch"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 200)
        file_id = resp.json()["id"]

        # Create a batch successfully first
        resp = self.client.post(
            reverse("gateway:batches"),
            data=json.dumps(
                {
                    "input_file_id": file_id,
                    "completion_window": "24h",
                    "endpoint": "/v1/chat/completions",
                }
            ),
            content_type="application/json",
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        batch_id = resp.json()["id"]

        # Patch upstream batch retrieval to return 400
        batches_url = reverse("gateway:batch", kwargs={"batch_id": batch_id})
        bad_request = MockConfig(
            status_code=400,
            response_data={
                "error": {
                    "message": "Invalid batch ID",
                    "type": "invalid_request_error",
                    "param": "batch_id",
                    "code": "invalid_value",
                }
            },
        )
        with self.mock_server.patch_external_api(batches_url, bad_request):
            resp = self.client.get(batches_url, headers=self.headers)

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid batch ID", resp.json()["error"]["message"])

    def test_batch_cancel_bad_request_error(self):
        """Test batch.cancel POST view catches Bad Request (400) from upstream."""
        # Create and upload a test file
        content = b'{"custom_id": "test"}\n'
        f = SimpleUploadedFile("test.jsonl", content, content_type="application/jsonl")
        resp = self.client.post(
            self.url_files, {"file": f, "purpose": "batch"}, headers=self.headers
        )
        self.assertEqual(resp.status_code, 200)
        file_id = resp.json()["id"]

        # Create a batch successfully first
        resp = self.client.post(
            reverse("gateway:batches"),
            data=json.dumps(
                {
                    "input_file_id": file_id,
                    "completion_window": "24h",
                    "endpoint": "/v1/chat/completions",
                }
            ),
            content_type="application/json",
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        batch_id = resp.json()["id"]

        # Patch upstream batch cancel to return 400
        cancel_url = reverse("gateway:batch_cancel", args=[batch_id])
        bad_request = MockConfig(
            status_code=400,
            response_data={
                "error": {
                    "message": "Cannot cancel batch",
                    "type": "invalid_request_error",
                    "param": "batch_id",
                    "code": "invalid_value",
                }
            },
        )
        with self.mock_server.patch_external_api(cancel_url, bad_request):
            resp = self.client.post(cancel_url, headers=self.headers)

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Cannot cancel batch", resp.json()["error"]["message"])


class TestBatchUploadModelExclusion(GatewayBatchesTestCase):
    def _upload_batch_file(self, model: str, headers: dict | None = None):
        payload = _build_chat_payload(model, messages=[{"role": "user", "content": "Test"}])
        line = {"custom_id": "1", "method": "POST", "url": self.url_chat, "body": payload}
        f = SimpleUploadedFile(
            "excluded.jsonl", json.dumps(line).encode() + b"\n", content_type="application/jsonl"
        )
        return self.client.post(
            self.url_files, {"file": f, "purpose": "batch"}, headers=headers or self.headers
        )

    def _sa_headers(self) -> dict:
        sa_token = Token.objects.get(pk=2)
        secret = sa_token._set_new_key()
        sa_token.save(update_fields=["key_hash", "key_preview"])
        headers = _build_chat_headers(secret)
        headers.pop("Content-Type", None)
        return headers

    def _assert_upload_rejected(self, resp, requested_model: str):
        self.assertEqual(
            resp.status_code, 400, f"Expected 400, got {resp.status_code}: {resp.content}"
        )
        self.assertEqual(
            resp.json()["error"]["message"],
            f"Batch file validation failed: Unknown model '{requested_model}' for custom_id '1'",
        )
        self.assertFalse(FileObject.objects.exists())

    def test_upload_whose_model_is_not_a_string_is_rejected(self):
        resp = self._upload_batch_file([])

        self._assert_upload_rejected(resp, "[]")

    def test_model_unavailable_to_the_personal_key_profile_rejects_upload(self):
        UserProfile.objects.get(user_id=1).add_excluded_model(self.model)

        resp = self._upload_batch_file(self.model)

        self._assert_upload_rejected(resp, self.model)

    def test_model_unavailable_to_the_personal_key_profile_rejects_alias_upload(self):
        UserProfile.objects.get(user_id=1).add_excluded_model(self.model)

        resp = self._upload_batch_file("main")

        self._assert_upload_rejected(resp, "main")

    def test_model_unavailable_to_the_service_account_team_rejects_upload(self):
        Team.objects.get(name="Whale").add_excluded_model(self.model)

        resp = self._upload_batch_file(self.model, headers=self._sa_headers())

        self._assert_upload_rejected(resp, self.model)

    def _create_batch_after_making_unavailable(self, exclude) -> tuple:
        resp = self._upload_batch_file(self.model)
        self.assertEqual(resp.status_code, 200, resp.content)
        file_id = resp.json()["id"]
        line = {
            "custom_id": "1",
            "method": "POST",
            "url": self.url_chat,
            "body": {"model": self.model},
        }
        exclude()
        return self._create_batch(file_id, json.dumps(line) + "\n")

    def _create_batch(self, file_id: str, stored_content: str):
        stored = MockPlainTextConfig(response_data=stored_content)
        with self.mock_server.patch_external_api(f"files/{file_id}/content", stored):
            return self.client.post(
                "/batches",
                data=json.dumps(
                    {
                        "input_file_id": file_id,
                        "completion_window": "24h",
                        "endpoint": self.url_chat,
                    }
                ),
                headers=self.headers,
                content_type="application/json",
            )

    def test_batch_from_a_file_whose_model_became_unavailable_since_upload_is_rejected(self):
        resp = self._create_batch_after_making_unavailable(
            lambda: UserProfile.objects.get(user_id=1).add_excluded_model(self.model)
        )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(
            resp.json()["error"]["message"],
            f"Batch file validation failed: Unknown model '{self.model}' for custom_id '1'",
        )
        self.assertFalse(BatchModel.objects.exists())

    def test_batch_from_a_file_with_a_line_that_is_not_an_object_is_rejected(self):
        resp = self._upload_batch_file(self.model)
        file_id = resp.json()["id"]
        stored = MockPlainTextConfig(response_data="[1, 2]\n")
        with self.mock_server.patch_external_api(f"files/{file_id}/content", stored):
            resp = self.client.post(
                "/batches",
                data=json.dumps(
                    {
                        "input_file_id": file_id,
                        "completion_window": "24h",
                        "endpoint": self.url_chat,
                    }
                ),
                headers=self.headers,
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(
            resp.json()["error"]["message"],
            "Batch file validation failed: Line 1 is not a JSON object",
        )
        self.assertFalse(BatchModel.objects.exists())

    def test_batch_from_a_file_whose_model_is_not_a_string_is_rejected(self):
        file_id = self._upload_batch_file(self.model).json()["id"]
        line = {"custom_id": "1", "method": "POST", "url": self.url_chat, "body": {"model": []}}

        resp = self._create_batch(file_id, json.dumps(line) + "\n")

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(
            resp.json()["error"]["message"],
            "Batch file validation failed: Unknown model '[]' for custom_id '1'",
        )
        self.assertFalse(BatchModel.objects.exists())

    def test_batch_starts_beside_a_deployment_with_its_own_provider(self):
        local = {
            "model_name": "local-vllm",
            "litellm_params": {
                "model": "served-model",
                "custom_llm_provider": "hosted_vllm",
                "api_base": "http://localhost:1/v1",
            },
            "model_info": {"id": "local-vllm"},
        }
        with router_config([*get_router_config()["model_list"], local]):
            resp = self._create_batch_after_making_unavailable(lambda: None)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(BatchModel.objects.exists())

    def test_batch_from_a_file_whose_model_is_still_available_starts(self):
        resp = self._create_batch_after_making_unavailable(
            lambda: UserProfile.objects.get(user_id=1).add_excluded_model("dall-e-2")
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(BatchModel.objects.exists())

    def test_another_unavailable_model_keeps_upload(self):
        UserProfile.objects.get(user_id=1).add_excluded_model("dall-e-2")

        resp = self._upload_batch_file(self.model)

        self.assertEqual(
            resp.status_code, 200, f"Expected 200, got {resp.status_code}: {resp.content}"
        )
        self.assertTrue(FileObject.objects.filter(id=resp.json()["id"], token_id=1).exists())
