import os
import runpy
from pathlib import Path
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

SETTINGS = Path(__file__).resolve().parents[2] / "aqueduct" / "settings.py"


def load_settings(proxy_ssl_header: str | None) -> dict:
    with mock.patch.dict(os.environ):
        os.environ.pop("SECURE_PROXY_SSL_HEADER", None)
        if proxy_ssl_header is not None:
            os.environ["SECURE_PROXY_SSL_HEADER"] = proxy_ssl_header
        return runpy.run_path(str(SETTINGS))


class ProxySSLHeaderSettingTest(SimpleTestCase):
    def test_off_unless_set(self):
        self.assertNotIn("SECURE_PROXY_SSL_HEADER", load_settings(None))

    def test_reads_header_and_value_from_the_environment(self):
        settings = load_settings("HTTP_X_FORWARDED_PROTO,https")
        self.assertEqual(settings["SECURE_PROXY_SSL_HEADER"], ("HTTP_X_FORWARDED_PROTO", "https"))

    def test_malformed_values_stop_startup_with_the_expected_format(self):
        for raw in ("HTTP_X_FORWARDED_PROTO", "HTTP_X_FORWARDED_PROTO,", ",https", "A,b,c"):
            with (
                self.subTest(raw=raw),
                self.assertRaisesMessage(ImproperlyConfigured, "HTTP_X_FORWARDED_PROTO,https"),
            ):
                load_settings(raw)
