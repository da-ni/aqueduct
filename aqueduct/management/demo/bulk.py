import time
from collections.abc import Callable, Iterator
from typing import Any

from django.db import connection

from management.demo.traffic import LogRow
from management.models import Request

INSERT_BATCH = 10_000
PROGRESS_REPORTS = 20


def insert_log(rows: Iterator[LogRow], expected: int, report: Callable[[str], Any]) -> int:
    table = Request._meta.db_table
    columns = ", ".join(LogRow._fields)
    written = 0
    started = time.monotonic()
    step = max(expected // PROGRESS_REPORTS, INSERT_BATCH)

    def progress() -> None:
        rate = written / max(time.monotonic() - started, 1e-6)
        report(f"  {written:,} / ~{expected:,} requests, {rate:,.0f}/s")

    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            with cursor.cursor.copy(f"COPY {table} ({columns}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
                    written += 1
                    if written % step == 0:
                        progress()
            cursor.execute(f"ANALYZE {table}")
            return written
        ops = connection.ops
        values = ", ".join(["%s"] * len(LogRow._fields))
        sql = f"INSERT INTO {table} ({columns}) VALUES ({values})"  # noqa: S608
        batch: list[tuple[Any, ...]] = []
        for row in rows:
            batch.append((*row[:2], ops.adapt_datetimefield_value(row[2]), *row[3:]))
            written += 1
            if len(batch) == INSERT_BATCH:
                cursor.executemany(sql, batch)
                batch.clear()
            if written % step == 0:
                progress()
        cursor.executemany(sql, batch)
        cursor.execute(f"ANALYZE {table}")
        return written
