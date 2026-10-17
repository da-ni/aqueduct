"""Rows for the Files, Batches, and Vector stores pages.

Each page lists what the viewer's keys and their teams' service account keys created.
The viewer acts (delete, cancel) only on what their personal keys created, as with a
personal key on the API.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Q, QuerySet
from django.template.defaultfilters import filesizeformat

from management.icons import Icon
from management.key_activity import Expiry
from management.models import (
    Batch,
    BatchStatus,
    FileObject,
    Team,
    Token,
    VectorStore,
    VectorStoreFile,
    VectorStoreFileBatch,
    VectorStoreFileStatus,
    VectorStoreStatus,
)

DAY_SECONDS = 86_400


def visible_to(user: AbstractBaseUser, teams: Iterable[Team]) -> Q:
    return Q(token__user=user) | Q(token__service_account__team__in=teams)


def own(user: AbstractBaseUser) -> Q:
    return Q(token__user=user, token__service_account__isnull=True)


def owns(user: AbstractBaseUser, token: Token | None) -> bool:
    return token is not None and token.service_account_id is None and token.user_id == user.pk


def counted(count: int, noun: str) -> str:
    plural = noun + ("es" if noun.endswith("ch") else "s")
    return f"{count} {noun if count == 1 else plural}"


def moment(timestamp: int | None) -> datetime | None:
    return None if timestamp is None else datetime.fromtimestamp(timestamp, tz=UTC)


def team_of(token: Token | None) -> str | None:
    return token.service_account.team.name if token and token.service_account else None


@dataclass(frozen=True)
class Status:
    label: str
    tone: str
    icon: Icon

    @classmethod
    def of(cls, raw: str, known: Mapping[str, "Status"]) -> "Status":
        return known.get(raw) or cls(raw.replace("_", " ").capitalize(), "st-off", Icon.OFF)


def _working(label: str) -> Status:
    return Status(label, "text-primary", Icon.PENDING)


_DONE = Status("Completed", "st-ok", Icon.OK)
_FAILED = Status("Failed", "st-err", Icon.FAILED)
_CANCELLED = Status("Cancelled", "st-off", Icon.OFF)
_EXPIRED = Status("Expired", "st-off", Icon.OFF)

BATCH_STATUSES: Mapping[str, Status] = {
    BatchStatus.VALIDATING: _working("Validating"),
    BatchStatus.IN_PROGRESS: _working("In progress"),
    BatchStatus.FINALIZING: _working("Finalizing"),
    BatchStatus.CANCELLING: _working("Cancelling"),
    BatchStatus.COMPLETED: _DONE,
    BatchStatus.FAILED: _FAILED,
    BatchStatus.EXPIRED: _EXPIRED,
    BatchStatus.CANCELLED: _CANCELLED,
}
CANCELLABLE = frozenset({BatchStatus.VALIDATING, BatchStatus.IN_PROGRESS, BatchStatus.FINALIZING})
_READY = Status("Ready", "st-ok", Icon.OK)
STORE_STATUSES: Mapping[str, Status] = {
    VectorStoreStatus.COMPLETED: _READY,
    VectorStoreStatus.IN_PROGRESS: _working("Indexing"),
    VectorStoreStatus.EXPIRED: _EXPIRED,
}
STORE_FILE_STATUSES: Mapping[str, Status] = {
    VectorStoreFileStatus.COMPLETED: _READY,
    VectorStoreFileStatus.IN_PROGRESS: _working("Indexing"),
    VectorStoreFileStatus.FAILED: _FAILED,
    VectorStoreFileStatus.CANCELLED: _CANCELLED,
}
PURPOSES: Mapping[str, str] = {
    "batch": "Batch input",
    "batch_output": "Batch output",
    "assistants": "Assistants",
    "assistants_output": "Assistants output",
    "user_data": "User data",
    "fine-tune": "Fine-tune",
    "vision": "Vision",
    "evals": "Evals",
}


@dataclass(frozen=True)
class Quota:
    owner: str
    used: int
    limit: int
    noun: str

    def __str__(self) -> str:
        return f"{self.owner}: {self.used} of {self.limit} {self.noun}"


@dataclass(frozen=True)
class Progress:
    total: int
    completed: int
    failed: int

    @classmethod
    def of(cls, counts: Mapping[str, int] | None) -> "Progress":
        counts = counts or {}
        return cls(counts.get("total", 0), counts.get("completed", 0), counts.get("failed", 0))

    def _share(self, part: int) -> float:
        return round(100 * part / self.total, 1) if self.total else 0

    @property
    def completed_share(self) -> float:
        return self._share(self.completed)

    @property
    def failed_share(self) -> float:
        return self._share(self.failed)

    @property
    def label(self) -> str:
        label = f"{self.completed + self.failed:,} of {self.total:,}"
        return f"{label} · {self.failed:,} failed" if self.failed else label


@dataclass(frozen=True)
class FileRow:
    file: FileObject
    team: str | None
    purpose: str
    uploaded: datetime | None
    expiry: Expiry
    deletable: bool


@dataclass(frozen=True)
class FileList:
    rows: tuple[FileRow, ...]

    @property
    def summary(self) -> str:
        size = filesizeformat(sum(row.file.bytes for row in self.rows))
        limit = settings.AQUEDUCT_FILES_API_MAX_FILE_SIZE_MB
        return f"{counted(len(self.rows), 'file')} · {size} · up to {limit} MB per file"


def file_list(user: AbstractBaseUser, teams: QuerySet[Team], now: datetime) -> FileList:
    files = (
        FileObject.objects.filter(visible_to(user, teams))
        .select_related("token__service_account__team")
        .order_by("-created_at")
    )
    return FileList(
        tuple(
            FileRow(
                file=file,
                team=team_of(file.token),
                purpose=PURPOSES.get(file.purpose, file.purpose),
                uploaded=moment(file.created_at),
                expiry=Expiry.of(moment(file.expires_at), now),
                deletable=owns(user, file.token),
            )
            for file in files
        )
    )


@dataclass(frozen=True)
class BatchRow:
    batch: Batch
    team: str | None
    status: Status
    progress: Progress
    created: datetime | None
    expiry: Expiry
    cancellable: bool

    @property
    def results(self) -> tuple[tuple[str, FileObject], ...]:
        named = (("Output", self.batch.output_file), ("Errors", self.batch.error_file))
        return tuple((label, file) for label, file in named if file)

    @property
    def note(self) -> str:
        if self.batch.status in CANCELLABLE:
            return "Output appears when it finishes"
        return "No output file"


@dataclass(frozen=True)
class BatchList:
    rows: tuple[BatchRow, ...]
    quotas: tuple[Quota, ...]

    @property
    def summary(self) -> str:
        return " · ".join([counted(len(self.rows), "batch"), *map(str, self.quotas)])


ACTIVE_BATCH = Q(
    status__in=[BatchStatus.VALIDATING, BatchStatus.IN_PROGRESS, BatchStatus.CANCELLING]
)


def batch_list(user: AbstractBaseUser, teams: QuerySet[Team], now: datetime) -> BatchList:
    batches = (
        Batch.objects.filter(visible_to(user, teams))
        .select_related("token__service_account__team", "input_file", "output_file", "error_file")
        .order_by("-created_at")
    )
    rows = tuple(
        BatchRow(
            batch=batch,
            team=team_of(batch.token),
            status=Status.of(batch.status, BATCH_STATUSES),
            progress=Progress.of(batch.request_counts),
            created=moment(batch.created_at),
            expiry=Expiry.of(moment(batch.expires_at), now),
            cancellable=batch.status in CANCELLABLE and owns(user, batch.token),
        )
        for batch in batches
    )
    active = Batch.objects.filter(ACTIVE_BATCH)
    quotas = (
        Quota(
            "Personal keys",
            active.filter(token__user=user).count(),
            settings.MAX_USER_BATCHES,
            "running",
        ),
        *(
            Quota(
                team.name,
                active.filter(token__service_account__team=team).count(),
                settings.MAX_TEAM_BATCHES,
                "running",
            )
            for team in teams.order_by("name")
        ),
    )
    return BatchList(rows, quotas)


def store_expiry(store: VectorStore, now: datetime) -> Expiry:
    policy = store.expires_after or {}
    days = policy.get("days")
    anchor = store.last_active_at or store.created_at
    if not isinstance(days, int):
        return Expiry.of(None, now)
    return Expiry.of(moment(anchor + days * DAY_SECONDS), now)


def store_size(store: VectorStore) -> int:
    return sum(entry.file_obj.bytes for entry in store.files.all() if entry.file_obj)


@dataclass(frozen=True)
class StoreRow:
    store: VectorStore
    team: str | None
    status: Status
    files: int
    size: int
    expiry: Expiry


@dataclass(frozen=True)
class StoreList:
    rows: tuple[StoreRow, ...]
    quotas: tuple[Quota, ...]
    files_limit: int

    @property
    def summary(self) -> str:
        return " · ".join(map(str, self.quotas))


def store_row(store: VectorStore, now: datetime) -> StoreRow:
    return StoreRow(
        store=store,
        team=team_of(store.token),
        status=Status.of(store.status, STORE_STATUSES),
        files=len(store.files.all()),
        size=store_size(store),
        expiry=store_expiry(store, now),
    )


def stores_visible_to(user: AbstractBaseUser, teams: QuerySet[Team]) -> QuerySet[VectorStore]:
    return (
        VectorStore.objects.filter(visible_to(user, teams))
        .select_related("token__service_account__team")
        .prefetch_related("files__file_obj", "file_batches")
    )


def store_list(user: AbstractBaseUser, teams: QuerySet[Team], now: datetime) -> StoreList:
    stores = stores_visible_to(user, teams).order_by("-created_at")
    quotas = (
        Quota(
            "Personal keys",
            VectorStore.objects.filter(token__user=user).count(),
            settings.MAX_USER_VECTOR_STORES,
            "vector stores",
        ),
        *(
            Quota(
                team.name,
                VectorStore.objects.filter(token__service_account__team=team).count(),
                settings.MAX_TEAM_VECTOR_STORES,
                "vector stores",
            )
            for team in teams.order_by("name")
        ),
    )
    return StoreList(
        tuple(store_row(store, now) for store in stores), quotas, settings.MAX_VECTOR_STORE_FILES
    )


@dataclass(frozen=True)
class StoreFileRow:
    entry: VectorStoreFile
    status: Status
    added: datetime | None


@dataclass(frozen=True)
class StoreBatchRow:
    batch: VectorStoreFileBatch
    status: Status
    progress: Progress
    in_progress: int
    created: datetime | None


@dataclass(frozen=True)
class StoreDetail:
    row: StoreRow
    files: tuple[StoreFileRow, ...]
    batches: tuple[StoreBatchRow, ...]
    files_limit: int
    created: datetime | None
    last_active: datetime | None


def store_detail(store: VectorStore, now: datetime) -> StoreDetail:
    return StoreDetail(
        row=store_row(store, now),
        files=tuple(
            StoreFileRow(
                entry, Status.of(entry.status, STORE_FILE_STATUSES), moment(entry.created_at)
            )
            for entry in store.files.all()
        ),
        batches=tuple(
            StoreBatchRow(
                batch,
                Status.of(batch.status, BATCH_STATUSES),
                Progress.of(batch.file_counts),
                (batch.file_counts or {}).get("in_progress", 0),
                moment(batch.created_at),
            )
            for batch in store.file_batches.all()
        ),
        files_limit=settings.MAX_VECTOR_STORE_FILES,
        created=moment(store.created_at),
        last_active=moment(store.last_active_at),
    )
