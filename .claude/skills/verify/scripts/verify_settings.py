"""Settings for a private verification instance.

The quartz dev overlay (mock SSO, dev defaults) with this run's own SQLite database,
so a verification run never reads or writes the database of aqueduct-dev.service.
"""

import os

from aqueduct_dev_settings import *  # noqa: F403
from aqueduct_dev_settings import DATABASES

DATABASES["default"]["NAME"] = os.environ["AQV_DB"]
ROOT_URLCONF = "verify_urls"
