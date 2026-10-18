"""Rows for the Files, Batches, and Vector stores pages.

Each page lists what the keys of the viewer's `Scope` created, newest first, 50 rows per page.
Seeing a row grants no action on it. The viewer deletes and cancels only what their personal
keys created, as with a personal key on the API, and syncs what `syncable` allows.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.core.paginator import Page, Paginator
from django.db.models import Count, Q, QuerySet, Sum
from django.db.models.functions import Coalesce
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import Promise
from django.utils.http import urlencode
from django.utils.translation import gettext, ngettext
from django.utils.translation import gettext_lazy as _

from management.icons import Icon
from management.key_activity import Expiry, grouped
from management.models import (
    Batch,
    BatchStatus,
    FileObject,
    Org,
    Team,
    Token,
    UserProfile,
    VectorStore,
    VectorStoreFile,
    VectorStoreFileBatch,
    VectorStoreFileStatus,
    VectorStoreStatus,
)
from management.request_log import visible_tokens

DAY_SECONDS = 86_400
PAGE_SIZE = 50
NEWEST_FIRST = ("-created_at", "-id")


API_DOCS = "https://tu-wien-datalab.github.io/aqueduct/api/"


class Whose(Enum):
    EVERY_ORG = auto()
    ORG = auto()
    YOURS = auto()


@dataclass(frozen=True)
class Scope:
    """The keys whose files, batches, and vector stores the viewer sees, the same keys whose
    requests the request log shows. `whose` says which keys they are, so an empty page can name
    them, and `org` names the org for `Whose.ORG`."""

    keys: QuerySet[Token]
    whose: Whose
    org: str = ""

    def empty(self, every_org: str, org: str, yours: str) -> str:
        """The sentence of an empty page for this scope, one per `Whose`; `org` holds `%(org)s`."""
        match self.whose:
            case Whose.EVERY_ORG:
                return every_org
            case Whose.ORG:
                return org % {"org": self.org}
            case Whose.YOURS:
                return yours


def scope(profile: UserProfile, org: Org | None) -> Scope:
    """Every key of `org` for its admins, every key for an admin viewing every org (`org=None`),
    otherwise the viewer's personal keys and the service account keys of their teams."""
    keys = visible_tokens(profile, org, profile.teams.all())
    if org is None:
        return Scope(keys, Whose.EVERY_ORG)
    if profile.is_org_admin(org):
        return Scope(keys, Whose.ORG, org.name)
    return Scope(keys, Whose.YOURS)


def syncable(user: AbstractBaseUser, teams: Iterable[Team], seen: Scope) -> Q:
    """The vector stores the viewer may sync from upstream: those of the keys of `seen` that they
    created or that belong to `teams`, every team of the org for an org admin. A service account
    key the viewer created for a team they have left is out of `seen`."""
    return Q(token__in=seen.keys) & (
        Q(token__user=user) | Q(token__service_account__team__in=teams)
    )


def own(user: AbstractBaseUser) -> Q:
    return Q(token__user=user, token__service_account__isnull=True)


def owns(user: AbstractBaseUser, token: Token | None) -> bool:
    return token is not None and token.service_account_id is None and token.user_id == user.pk


def moment(timestamp: int | None) -> datetime | None:
    return None if timestamp is None else datetime.fromtimestamp(timestamp, tz=UTC)


def owner_of(token: Token, viewer: AbstractBaseUser) -> str:
    """The team of a service account's key, "You" for the viewer's personal key, or the email
    of another user's personal key, which only an org admin sees."""
    if token.service_account:
        return token.service_account.team.name
    return gettext("You") if token.user_id == viewer.pk else token.user.email


@dataclass(frozen=True)
class Status:
    label: str | Promise
    tone: str
    icon: Icon

    @classmethod
    def of(cls, raw: str, known: Mapping[str, "Status"]) -> "Status":
        return known.get(raw) or cls(raw.replace("_", " ").capitalize(), "st-off", Icon.OFF)


def _working(label: Promise) -> Status:
    return Status(label, "text-primary", Icon.PENDING)


_DONE = Status(_("Completed"), "st-ok", Icon.OK)
_FAILED = Status(_("Failed"), "st-err", Icon.FAILED)
_CANCELLED = Status(_("Cancelled"), "st-off", Icon.OFF)
_EXPIRED = Status(_("Expired"), "st-off", Icon.OFF)

BATCH_STATUSES: Mapping[str, Status] = {
    BatchStatus.VALIDATING: _working(_("Validating")),
    BatchStatus.IN_PROGRESS: _working(_("In progress")),
    BatchStatus.FINALIZING: _working(_("Finalizing")),
    BatchStatus.CANCELLING: _working(_("Cancelling")),
    BatchStatus.COMPLETED: _DONE,
    BatchStatus.FAILED: _FAILED,
    BatchStatus.EXPIRED: _EXPIRED,
    BatchStatus.CANCELLED: _CANCELLED,
}
CANCELLABLE = frozenset({BatchStatus.VALIDATING, BatchStatus.IN_PROGRESS, BatchStatus.FINALIZING})
_READY = Status(_("Ready"), "st-ok", Icon.OK)
STORE_STATUSES: Mapping[str, Status] = {
    VectorStoreStatus.COMPLETED: _READY,
    VectorStoreStatus.IN_PROGRESS: _working(_("Indexing")),
    VectorStoreStatus.EXPIRED: _EXPIRED,
}
STORE_FILE_STATUSES: Mapping[str, Status] = {
    VectorStoreFileStatus.COMPLETED: _READY,
    VectorStoreFileStatus.IN_PROGRESS: _working(_("Indexing")),
    VectorStoreFileStatus.FAILED: _FAILED,
    VectorStoreFileStatus.CANCELLED: _CANCELLED,
}
PURPOSES: Mapping[str, str | Promise] = {
    "batch": _("Batch input"),
    "batch_output": _("Batch output"),
    "assistants": _("Assistants"),
    "assistants_output": _("Assistants output"),
    "user_data": _("User data"),
    "fine-tune": _("Fine-tune"),
    "vision": _("Vision"),
    "evals": _("Evals"),
}


def paged(rows: QuerySet, page: str | int | None) -> tuple[Page, QuerySet]:
    """The page of `rows` in `NEWEST_FIRST` order, and a query by id that loads only its rows,
    so joins and annotations run on 50 rows rather than on all of them."""
    pages = Paginator(
        rows.order_by(*NEWEST_FIRST).values_list("id", flat=True), PAGE_SIZE
    ).get_page(page)
    return pages, rows.model._default_manager.filter(id__in=list(pages)).order_by(*NEWEST_FIRST)


def list_url(name: str, page: str | None) -> str:
    """The list page `name` at `page`, so delete and cancel return the viewer to their page."""
    return f"{reverse(name)}?{urlencode({'page': page})}" if page else reverse(name)


def page_holding(rows: QuerySet, row_id: str) -> int | None:
    """The number of the page that holds row `row_id` of `rows`, `None` when `rows` lacks it."""
    row = rows.filter(id=row_id).values("created_at").first()
    if row is None:
        return None
    newer = rows.filter(
        Q(created_at__gt=row["created_at"]) | Q(created_at=row["created_at"], id__gt=row_id)
    ).count()
    return newer // PAGE_SIZE + 1


def quotas(
    line: str,
    user: AbstractBaseUser,
    rows: QuerySet,
    teams: QuerySet[Team],
    limits: tuple[int, int],
) -> str:
    """What `rows` holds against the viewer's limit and against the limit of each team they
    belong to, the limits the API enforces when it creates more, as "you 1/10 · <team> 0/25"
    put into `line` at `%(quotas)s`."""
    user_limit, team_limit = limits
    per_team = dict(
        rows.filter(token__service_account__team__in=teams)
        .order_by()
        .values("token__service_account__team")
        .annotate(n=Count("id"))
        .values_list("token__service_account__team", "n")
    )
    used = (
        gettext("you %(used)s/%(limit)s")
        % {"used": rows.filter(own(user)).count(), "limit": user_limit},
        *(
            f"{team.name} {per_team.get(team.id, 0)}/{team_limit}"
            for team in teams.order_by("name")
        ),
    )
    return line % {"quotas": " · ".join(used)}


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
    def parts(self) -> tuple[str, ...]:
        """The label's phrases; a narrow column breaks the label between them, never inside one."""
        done = gettext("%(done)s of %(total)s") % {
            "done": grouped(self.completed + self.failed),
            "total": grouped(self.total),
        }
        if not self.failed:
            return (done,)
        failed = ngettext("%(count)s failed", "%(count)s failed", self.failed)
        return done, failed % {"count": grouped(self.failed)}

    @property
    def label(self) -> str:
        return " · ".join(self.parts)


@dataclass(frozen=True)
class FileRow:
    file: FileObject
    owner: str
    purpose: str | Promise
    created: datetime | None
    expiry: Expiry
    mine: bool
    """Whether the viewer's personal key created it, which lets them delete it."""


@dataclass(frozen=True)
class FileList:
    rows: tuple[FileRow, ...]
    pages: Page
    size: int
    scope: Scope
    docs = f"{API_DOCS}files/"

    @property
    def summary(self) -> str:
        count = self.pages.paginator.count
        limit = settings.AQUEDUCT_FILES_API_MAX_FILE_SIZE_MB
        return " · ".join(
            (
                ngettext("%(count)s file", "%(count)s files", count) % {"count": count},
                filesizeformat(self.size),
                gettext("max %(limit)s MB each") % {"limit": limit},
            )
        )

    @property
    def empty(self) -> str:
        return self.scope.empty(
            gettext("No files yet."),
            gettext("No files in %(org)s yet."),
            gettext("No files from your keys or your teams' keys yet."),
        )


def file_list(
    user: AbstractBaseUser,
    keys: Scope,
    now: datetime,
    page: str | int | None = None,
    file_id: str | None = None,
) -> FileList:
    """`file_id` opens the page that holds that file, so a link to it lands on its row."""
    visible = FileObject.objects.filter(token__in=keys.keys)
    size = visible.aggregate(size=Coalesce(Sum("bytes"), 0))["size"]
    pages, files = paged(visible, (file_id and page_holding(visible, file_id)) or page)
    files = files.select_related("token__user", "token__service_account__team")
    return FileList(
        tuple(
            FileRow(
                file=file,
                owner=owner_of(file.token, user),
                purpose=PURPOSES.get(file.purpose, file.purpose),
                created=moment(file.created_at),
                expiry=Expiry.of(moment(file.expires_at), now),
                mine=owns(user, file.token),
            )
            for file in files
        ),
        pages,
        size,
        keys,
    )


@dataclass(frozen=True)
class BatchRow:
    batch: Batch
    owner: str
    status: Status
    progress: Progress
    created: datetime | None
    deadline: datetime | None
    """When a running batch must finish, `None` once it stopped running."""
    deadline_today: bool
    mine: bool
    """Whether the viewer's personal key created it."""
    cancellable: bool

    @property
    def results(self) -> tuple[tuple[str, FileObject], ...]:
        named = (
            (gettext("Output"), self.batch.output_file),
            (gettext("Errors"), self.batch.error_file),
        )
        return tuple((label, file) for label, file in named if file)

    @property
    def note(self) -> str:
        if self.batch.status in CANCELLABLE:
            return gettext("Output pending")
        return gettext("No output file")


@dataclass(frozen=True)
class BatchList:
    rows: tuple[BatchRow, ...]
    pages: Page
    summary: str
    scope: Scope
    docs = f"{API_DOCS}batches/"

    @property
    def empty(self) -> str:
        return self.scope.empty(
            gettext("No batches yet."),
            gettext("No batches in %(org)s yet."),
            gettext("No batches from your keys or your teams' keys yet."),
        )


ACTIVE_BATCH = Q(
    status__in=[BatchStatus.VALIDATING, BatchStatus.IN_PROGRESS, BatchStatus.CANCELLING]
)


def batch_row(batch: Batch, user: AbstractBaseUser, now: datetime) -> BatchRow:
    running = batch.status in CANCELLABLE
    mine = owns(user, batch.token)
    deadline = moment(batch.expires_at) if running else None
    return BatchRow(
        batch=batch,
        owner=owner_of(batch.token, user),
        status=Status.of(batch.status, BATCH_STATUSES),
        progress=Progress.of(batch.request_counts),
        created=moment(batch.created_at),
        deadline=deadline,
        deadline_today=deadline is not None
        and timezone.localdate(deadline) == timezone.localdate(now),
        mine=mine,
        cancellable=running and mine,
    )


def batch_list(
    user: AbstractBaseUser,
    keys: Scope,
    teams: QuerySet[Team],
    now: datetime,
    page: str | None = None,
) -> BatchList:
    """Lists the batches of `keys`; the summary counts running batches of the viewer and of
    the `teams` they belong to."""
    pages, batches = paged(Batch.objects.filter(token__in=keys.keys), page)
    batches = batches.select_related(
        "token__user", "token__service_account__team", "input_file", "output_file", "error_file"
    )
    limits = (settings.MAX_USER_BATCHES, settings.MAX_TEAM_BATCHES)
    return BatchList(
        tuple(batch_row(batch, user, now) for batch in batches),
        pages,
        quotas(
            gettext("Running: %(quotas)s"), user, Batch.objects.filter(ACTIVE_BATCH), teams, limits
        ),
        keys,
    )


def store_expiry(store: VectorStore, now: datetime) -> Expiry:
    policy = store.expires_after or {}
    days = policy.get("days")
    anchor = store.last_active_at or store.created_at
    if not isinstance(days, int):
        return Expiry.of(None, now)
    return Expiry.of(moment(anchor + days * DAY_SECONDS), now)


@dataclass(frozen=True)
class StoreRow:
    store: VectorStore
    owner: str
    status: Status
    files: int
    size: int
    expiry: Expiry
    syncable: bool


@dataclass(frozen=True)
class StoreList:
    rows: tuple[StoreRow, ...]
    pages: Page
    summary: str
    scope: Scope
    docs = f"{API_DOCS}vector-stores/"

    @property
    def empty(self) -> str:
        return self.scope.empty(
            gettext("No vector stores yet."),
            gettext("No vector stores in %(org)s yet."),
            gettext("No vector stores from your keys or your teams' keys yet."),
        )


def store_rows(
    stores: QuerySet[VectorStore], user: AbstractBaseUser, sync: Q, now: datetime
) -> tuple[StoreRow, ...]:
    """A row for each of `stores`, counting its files and their size. `sync` is `syncable`."""
    stores = stores.select_related("token__user", "token__service_account__team").annotate(
        file_count=Count("files"), size=Coalesce(Sum("files__file_obj__bytes"), 0)
    )
    synced = set(stores.filter(sync).values_list("id", flat=True))
    return tuple(
        StoreRow(
            store=store,
            owner=owner_of(store.token, user),
            status=Status.of(store.status, STORE_STATUSES),
            files=store.file_count,
            size=store.size,
            expiry=store_expiry(store, now),
            syncable=store.id in synced,
        )
        for store in stores
    )


def store_list(
    user: AbstractBaseUser,
    keys: Scope,
    sync: Q,
    teams: QuerySet[Team],
    now: datetime,
    page: str | None = None,
) -> StoreList:
    """Lists the stores of `keys`; the summary covers the viewer and the `teams` they belong
    to, which they create stores for."""
    pages, stores = paged(VectorStore.objects.filter(token__in=keys.keys), page)
    limits = (settings.MAX_USER_VECTOR_STORES, settings.MAX_TEAM_VECTOR_STORES)
    return StoreList(
        store_rows(stores, user, sync, now),
        pages,
        quotas(
            gettext("Vector stores: %(quotas)s"), user, VectorStore.objects.all(), teams, limits
        ),
        keys,
    )


@dataclass(frozen=True)
class StoreFileRow:
    entry: VectorStoreFile
    status: Status
    created: datetime | None
    listed: bool


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
    pages: Page
    batches: tuple[StoreBatchRow, ...]
    created: datetime | None
    last_active: datetime | None


def store_detail(
    store_id: str,
    user: AbstractBaseUser,
    keys: Scope,
    sync: Q,
    now: datetime,
    page: str | None = None,
) -> StoreDetail | None:
    """The store `store_id` of `keys`, `None` when `keys` did not create it. A file links to
    the Files page, which lists the files of the same `keys`, only when its row is there."""
    found = store_rows(
        VectorStore.objects.filter(token__in=keys.keys, id=store_id), user, sync, now
    )
    if not found:
        return None
    row = found[0]
    store = row.store
    pages, entries_on_page = paged(store.files.all(), page)
    entries = tuple(entries_on_page.select_related("file_obj"))
    listed = set(
        FileObject.objects.filter(
            token__in=keys.keys, id__in=[entry.file_obj_id for entry in entries]
        ).values_list("id", flat=True)
    )
    return StoreDetail(
        row=row,
        files=tuple(
            StoreFileRow(
                entry,
                Status.of(entry.status, STORE_FILE_STATUSES),
                moment(entry.created_at),
                entry.file_obj_id in listed,
            )
            for entry in entries
        ),
        pages=pages,
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
        created=moment(store.created_at),
        last_active=moment(store.last_active_at),
    )
