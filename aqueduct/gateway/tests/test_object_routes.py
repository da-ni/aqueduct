import json

from django.conf import settings
from django.urls import reverse

from gateway.tests.utils.base import GatewayFilesTestCase
from management.models import (
    Batch,
    FileObject,
    Token,
    VectorStore,
    VectorStoreFile,
    VectorStoreFileBatch,
)

VECTOR_STORE = ("Vector store not found.", "vector_store_id")
VECTOR_STORE_FILE = ("Vector store file not found.", "file_id")
FILE_BATCH = ("File batch not found.", "batch_id")
FILE = ("File not found.", "file_id")
BATCH = ("Batch not found.", "batch_id")
FILES = ("File not found: file-other", "file_ids")
INPUT_FILE = ("Input file not found.", "input_file_id")
BATCH_PARAMS = {"endpoint": "/v1/chat/completions", "completion_window": "24h"}


class TestObjectRoutes(GatewayFilesTestCase):
    def setUp(self):
        super().setUp()
        other = Token.objects.get(user_id=self.create_new_user()[1])
        mine = Token.objects.get(pk=1)
        stores = {}
        for token, name in ((mine, "vs-mine"), (other, "vs-other")):
            stores[name] = VectorStore.objects.create(
                id=name,
                token=token,
                name=name,
                created_at=1,
                upstream_url=settings.AQUEDUCT_FILES_API_URL,
            )
        file = FileObject.objects.create(
            id="file-other",
            bytes=1,
            created_at=1,
            filename="other.jsonl",
            purpose="batch",
            token=other,
            upstream_url=settings.AQUEDUCT_FILES_API_URL,
        )
        VectorStoreFile.objects.create(
            id="vsf-other", vector_store=stores["vs-other"], file_obj=file, created_at=1
        )
        VectorStoreFileBatch.objects.create(
            id="vsfb-other", vector_store=stores["vs-other"], created_at=1
        )
        Batch.objects.create(
            id="batch-other",
            completion_window="24h",
            created_at=1,
            endpoint="/v1/chat/completions",
            input_file=file,
            token=other,
        )

    def _routes(self):
        theirs = {"vector_store_id": "vs-other"}
        mine = {"vector_store_id": "vs-mine"}
        their_file = {"file_id": "file-other"}
        new_batch = {"input_file_id": "file-other", **BATCH_PARAMS}
        vs_file_routes = [
            ("get", "vector_store_file"),
            ("delete", "vector_store_file"),
            ("get", "vector_store_file_content"),
        ]
        file_batch_routes = [
            ("get", "vector_store_file_batch"),
            ("post", "vector_store_file_batch_cancel"),
            ("get", "vector_store_file_batch_files"),
        ]
        return [
            ("get", "vector_store", theirs, None, VECTOR_STORE),
            ("post", "vector_store", theirs, {"name": "x"}, VECTOR_STORE),
            ("delete", "vector_store", theirs, None, VECTOR_STORE),
            ("post", "vector_store_search", theirs, {"query": "x"}, VECTOR_STORE),
            ("get", "vector_store_files", theirs, None, VECTOR_STORE),
            ("post", "vector_store_files", theirs, their_file, VECTOR_STORE),
            ("post", "vector_store_file_batches", theirs, {"file_ids": ["x"]}, VECTOR_STORE),
            *(
                (method, name, {**ids, "file_id": "vsf-other"}, None, expected)
                for ids, expected in ((theirs, VECTOR_STORE), (mine, VECTOR_STORE_FILE))
                for method, name in vs_file_routes
            ),
            *(
                (method, name, {**ids, "batch_id": "vsfb-other"}, None, expected)
                for ids, expected in ((theirs, VECTOR_STORE), (mine, FILE_BATCH))
                for method, name in file_batch_routes
            ),
            ("post", "vector_store_files", mine, their_file, FILE),
            ("post", "vector_store_file_batches", mine, {"file_ids": ["file-other"]}, FILES),
            ("get", "file", their_file, None, FILE),
            ("delete", "file", their_file, None, FILE),
            ("get", "file_content", their_file, None, FILE),
            ("get", "batch", {"batch_id": "batch-other"}, None, BATCH),
            ("post", "batch_cancel", {"batch_id": "batch-other"}, None, BATCH),
            ("post", "batches", {}, new_batch, INPUT_FILE),
        ]

    def _send(self, method, name, kwargs, body):
        return getattr(self.client, method)(
            reverse(f"gateway:{name}", kwargs=kwargs),
            data=json.dumps(body) if body is not None else None,
            content_type="application/json",
            headers=self.headers,
        )

    def test_an_object_outside_the_key_s_reach_answers_like_a_missing_one(self):
        for method, name, kwargs, body, (message, param) in self._routes():
            with self.subTest(method=method, name=name, kwargs=kwargs):
                resp = self._send(method, name, kwargs, body)

                self.assertEqual(resp.status_code, 404, resp.content)
                self.assertEqual(
                    resp.json(),
                    {"error": {"message": message, "type": "not_found_error", "param": param}},
                )
        self.assertEqual(VectorStore.objects.count(), 2)
        self.assertEqual(VectorStoreFile.objects.count(), 1)
        self.assertEqual(VectorStoreFileBatch.objects.count(), 1)
        self.assertEqual(FileObject.objects.count(), 1)
        self.assertEqual(Batch.objects.count(), 1)

    def test_without_a_files_api_every_object_route_answers_503(self):
        with self.settings(AQUEDUCT_FILES_API_URL=None):
            for method, name, kwargs, body, _ in self._routes():
                api = "Vector Store API" if name.startswith("vector_store") else "Files API"
                with self.subTest(method=method, name=name, kwargs=kwargs):
                    resp = self._send(method, name, kwargs, body)

                    self.assertEqual(resp.status_code, 503, resp.content)
                    self.assertEqual(
                        resp.json(),
                        {"error": {"message": f"{api} not configured", "type": "server_error"}},
                    )
