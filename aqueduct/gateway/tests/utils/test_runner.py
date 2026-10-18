import atexit
import logging
import os
import shutil
import signal
import subprocess
import unittest

from django.conf import settings
from django.core.cache import caches
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.test.runner import DiscoverRunner
from django.utils import translation

from gateway import config
from mock_api.mock_server import MockAPIServer

logger = logging.getLogger("aqueduct")


class MockServerState:
    _mock_server: MockAPIServer | None = None
    _mcp_server_process: subprocess.Popen | None = None
    _mcp_server_port: int | None = None

    @classmethod
    def get_mock_server(cls) -> MockAPIServer | None:
        if cls._mock_server is None:
            raise RuntimeError("Mock server not initialized. Use MockServerTestRunner.")
        return cls._mock_server

    @classmethod
    def get_mcp_server_process(cls) -> subprocess.Popen | None:
        return cls._mcp_server_process

    @classmethod
    def get_mcp_server_port(cls) -> int | None:
        return cls._mcp_server_port

    @classmethod
    def set_mcp_server(cls, process: subprocess.Popen, port: int) -> None:
        cls._mcp_server_process = process
        cls._mcp_server_port = port

    @classmethod
    def cleanup_mock_server(cls):
        if cls._mock_server is not None and cls._mock_server.process:
            cls._mock_server.stop()
            cls._mock_server = None

    @classmethod
    def cleanup_mcp_server(cls):
        if cls._mcp_server_process is not None and cls._mcp_server_process.poll() is None:
            logger.info("\nStopping MCP everything server...")

            try:
                os.killpg(os.getpgid(cls._mcp_server_process.pid), signal.SIGTERM)
                cls._mcp_server_process.wait(timeout=5)
                logger.info("MCP server stopped successfully")
            except subprocess.TimeoutExpired:
                logger.warning("MCP server did not stop gracefully, forcing kill")
                try:
                    os.killpg(os.getpgid(cls._mcp_server_process.pid), signal.SIGKILL)
                    cls._mcp_server_process.wait()
                    logger.info("MCP server stopped successfully (forced)")
                except ProcessLookupError:
                    logger.info("Process group already terminated")
            except ProcessLookupError:
                logger.info("Process already terminated")

            cls._mcp_server_process = None
            cls._mcp_server_port = None

        from gateway.tests.utils.mcp import MCP_CONFIG_PATH

        shutil.rmtree(MCP_CONFIG_PATH.parent, ignore_errors=True)


def get_shared_mock_server() -> MockAPIServer | None:
    return MockServerState.get_mock_server()


def get_shared_mcp_server_process() -> subprocess.Popen | None:
    return MockServerState.get_mcp_server_process()


def get_mcp_server_port() -> int | None:
    return MockServerState.get_mcp_server_port()


def set_shared_mcp_server(process: subprocess.Popen, port: int) -> None:
    MockServerState.set_mcp_server(process, port)


ROUTER_CACHES = (
    config.get_router_config,
    config.get_router,
    config.get_openai_client,
    config.get_all_model_request_limit_multipliers,
)
CACHES_BUILT_FROM_SETTING = {
    "LITELLM_ROUTER_CONFIG_FILE_PATH": ROUTER_CACHES,
    "MCP_CONFIG_FILE_PATH": (config.get_mcp_config,),
    "AQUEDUCT_FILES_API_URL": (config.get_files_api_client,),
    "AQUEDUCT_FILES_API_KEY": (config.get_files_api_client,),
}


@receiver(setting_changed)
def clear_caches_built_from_the_setting(*, setting: str, **kwargs) -> None:
    for cached in CACHES_BUILT_FROM_SETTING.get(setting, ()):
        cached.cache_clear()


class _ResetSharedStateBeforeEachTest(unittest.TestResult):
    def startTest(self, test):  # noqa: N802
        for cache in caches.all(initialized_only=True):
            cache.clear()
        # LocaleMiddleware activates a request's language on this thread and never deactivates it.
        translation.activate(settings.LANGUAGE_CODE)
        if MockServerState._mock_server is not None:
            MockServerState._mock_server.forget_requests()
        super().startTest(test)


class MockServerTestRunner(DiscoverRunner):
    """
    Custom Django test runner that starts a single mock server instance
    for the entire test suite.

    It also stops the test MCP server if it has been started by any tests that needed it.
    """

    def get_resultclass(self):
        result = super().get_resultclass() or unittest.TextTestResult
        return type("IsolatedTestResult", (_ResetSharedStateBeforeEachTest, result), {})

    def setup_test_environment(self, **kwargs):
        """Set up the test environment, starting the mock API server."""
        super().setup_test_environment(**kwargs)

        if settings.TESTS_USE_MOCK_API:
            MockServerState._mock_server = MockAPIServer(host="localhost", delays=False)
            try:
                MockServerState._mock_server.start()
                logger.info("✓ Mock server started on %s.", MockServerState._mock_server.base_url)
            except RuntimeError as err:
                logger.exception("✗ Failed to start mock server: %s", err)
                raise

            atexit.register(MockServerState.cleanup_mock_server)
        else:
            logger.warning("Skipping the initialisation of the mock server.")

        atexit.register(MockServerState.cleanup_mcp_server)

    def teardown_test_environment(self, **kwargs):
        """Tear down the test environment, stopping the mock and MCP server."""
        MockServerState.cleanup_mock_server()
        MockServerState.cleanup_mcp_server()
        super().teardown_test_environment(**kwargs)
