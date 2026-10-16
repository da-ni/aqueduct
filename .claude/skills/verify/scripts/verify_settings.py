"""Settings for a private verification instance.

The quartz dev overlay (mock SSO, dev defaults) with this run's own SQLite database,
so a verification run never reads or writes the database of aqueduct-dev.service.
"""

import os

from aqueduct_dev_settings import *  # noqa: F403
from aqueduct_dev_settings import AUTHENTICATION_BACKENDS, DATABASES

DATABASES["default"]["NAME"] = os.environ["AQV_DB"]
ROOT_URLCONF = "verify_urls"
# Django instantiates every backend on each authenticate() call, and the OIDC backend
# raises ImproperlyConfigured without a JWKS endpoint, which turns every API-key
# request into a 500. Sign-in goes through /verify-login/ instead.
AUTHENTICATION_BACKENDS = [b for b in AUTHENTICATION_BACKENDS if b != "management.auth.OIDCBackend"]
