import random
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection, transaction
from django.utils import timezone

from gateway.config import model_names
from management.demo.builder import Builder, delete_seeded, seed_exists
from management.demo.bulk import insert_log
from management.demo.catalog import (
    DATALAB_PROFILE,
    Kind,
    KindDefaults,
    ModelSpec,
    kind_defaults,
    read_model_mix,
    router_config_for,
)
from management.demo.traffic import Traffic, TrafficProfile

DEFAULTS: dict[str, Any] = {
    "teams": 300,
    "small_teams": 30,
    "service_accounts": 25,
    "requests_per_day": 60_000,
    "days": 30,
    "local_time_zone": "Europe/Vienna",
    "models_file": DATALAB_PROFILE,
}
SQLITE_WARN_ROWS = 500_000
SMALL_ORG_SERVICE_ACCOUNTS = 3
KEY_AGE = timedelta(days=7)


class Command(BaseCommand):
    help = (
        "Seed a production-sized demo: a large and a small org with teams, users, keys, service "
        "accounts, and a request log. Development only."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.set_defaults(**DEFAULTS)
        parser.add_argument("--teams", type=int, help="Teams in the large org.")
        parser.add_argument("--small-teams", type=int, help="Teams in the small org.")
        parser.add_argument(
            "--service-accounts", type=int, help="Service accounts in the large org."
        )
        parser.add_argument("--requests-per-day", type=int)
        parser.add_argument("--days", type=int, help="Days of log, ending now.")
        parser.add_argument(
            "--local-time-zone",
            help="The zone whose working hours shape the traffic; TIME_ZONE is left alone.",
        )
        parser.add_argument("--seed", type=int, default=0, help="Seed of every random choice.")
        parser.add_argument(
            "--models-file",
            type=Path,
            help="YAML or JSON model mix. Defaults to the models of management/demo/datalab.yaml.",
        )
        parser.add_argument(
            "--write-router-config",
            type=Path,
            metavar="PATH",
            help="Write a router config serving the models of --models-file to PATH and stop.",
        )
        parser.add_argument(
            "--reset", action="store_true", help="Delete earlier seeded data, then seed."
        )
        parser.add_argument(
            "--delete", action="store_true", help="Delete earlier seeded data and stop."
        )
        parser.add_argument(
            "--force", action="store_true", help="Run even when DEBUG is off. Use with care."
        )

    def handle(self, *args: object, **options: Any) -> None:
        profile = yaml.safe_load(DATALAB_PROFILE.read_text())
        kinds = kind_defaults(profile["kinds"])
        if options["write_router_config"]:
            self.write_router_config(
                self.model_mix(options["models_file"], kinds), options["write_router_config"]
            )
            return
        self.check_target(force=options["force"])
        if options["delete"]:
            with transaction.atomic():
                self.delete()
        else:
            models = self.checked(self.model_mix(options["models_file"], kinds))
            traffic = TrafficProfile.parse(profile["traffic"])
            zone = self.zone(options["local_time_zone"])
            with transaction.atomic():
                if options["reset"]:
                    self.delete()
                if seed_exists():
                    msg = "Seeded data already exists. Pass --reset to replace it or --delete."
                    raise CommandError(msg)
                self.seed(traffic, models, zone, options)
        call_command("rebuild_usage_rollups", stdout=self.stdout)

    def check_target(self, *, force: bool) -> None:
        if settings.DEBUG:
            return
        if not force:
            msg = (
                "seed_demo writes demo data and runs only with DEBUG on. Pass --force to override."
            )
            raise CommandError(msg)
        name = connection.settings_dict["NAME"]
        self.stderr.write(f"DEBUG is off. Writing to the {connection.vendor} database {name}.")

    def delete(self) -> None:
        started = time.monotonic()
        counts = delete_seeded()
        self.stdout.write(f"Deleted {self.summary(counts)} in {time.monotonic() - started:.1f} s")

    @staticmethod
    def model_mix(path: Path, kinds: dict[Kind, KindDefaults]) -> list[ModelSpec]:
        try:
            models = read_model_mix(path, kinds)
        except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            msg = (
                f"Cannot read {path}: {exc!r}. Expected `models:` as a list like "
                f"{DEFAULTS['models_file']}, kind one of {', '.join(Kind)}."
            )
            raise CommandError(msg) from exc
        if not models:
            msg = f"{path} lists no models."
            raise CommandError(msg)
        return models

    @staticmethod
    def zone(name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"Unknown --local-time-zone {name!r}."
            raise CommandError(msg) from exc

    def write_router_config(self, models: list[ModelSpec], path: Path) -> None:
        path.write_text(yaml.safe_dump(router_config_for(models), sort_keys=False))
        self.stdout.write(
            f"Wrote {path}. Point LITELLM_ROUTER_CONFIG_FILE_PATH at it, then run seed_demo again."
        )

    def checked(self, models: list[ModelSpec]) -> list[ModelSpec]:
        missing = sorted({m.name for m in models} - set(model_names()))
        if missing:
            msg = (
                f"Not in the router config ({settings.LITELLM_ROUTER_CONFIG_FILE_PATH}): "
                f"{', '.join(missing)}. Requests may only name served models. Pass "
                "--write-router-config <path> to write a router config that serves them."
            )
            raise CommandError(msg)
        return models

    def seed(
        self,
        traffic: TrafficProfile,
        models: list[ModelSpec],
        zone: ZoneInfo,
        options: dict[str, Any],
    ) -> None:
        rng = random.Random(options["seed"])  # noqa: S311
        now = timezone.now()
        start = now - timedelta(days=options["days"])
        total = traffic.log_size(options["requests_per_day"], options["days"])
        if connection.vendor == "sqlite" and total > SQLITE_WARN_ROWS:
            self.stderr.write(
                f"{total:,} requests on SQLite will take a while. Postgres is faster."
            )
        started = time.monotonic()
        builder = Builder(traffic, rng, models, start - KEY_AGE)
        cap = settings.MAX_SERVICE_ACCOUNTS_PER_TEAM
        builder.org(
            "campus", options["teams"], min(options["service_accounts"], cap * options["teams"])
        )
        small_accounts = min(SMALL_ORG_SERVICE_ACCOUNTS, cap * options["small_teams"])
        builder.org("institute", options["small_teams"], small_accounts)
        keys = builder.keys
        self.stdout.write(
            f"Created 2 orgs, {builder.serial:,} users, {len(keys.personal):,} personal "
            f"keys, {len(keys.service)} service account keys, "
            f"{self.summary(builder.storage(now))} in {time.monotonic() - started:.1f} s"
        )
        rows = Traffic(traffic, rng, models, now, zone).log(keys, start, total)
        written = insert_log(rows, total, self.stdout.write)
        self.stdout.write(f"Wrote {written:,} requests in {time.monotonic() - started:.1f} s")

    @staticmethod
    def summary(counts: dict[str, int]) -> str:
        return ", ".join(f"{n:,} {name}" for name, n in counts.items())
