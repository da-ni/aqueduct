import time

from django.core.management.base import BaseCommand

from management import usage_rollup


class Command(BaseCommand):
    help = "Recount the usage rollups and key windows from every retained request."

    def handle(self, *args: object, **options: object) -> None:
        for label, recount in (
            ("Org hours", usage_rollup.rebuild),
            ("Key windows", usage_rollup.refresh_key_windows),
        ):
            started = time.monotonic()
            recount()
            self.stdout.write(f"{label} rebuilt in {time.monotonic() - started:.1f} s")
