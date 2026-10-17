# Files, batches, and vector stores

The Files, Batches, and Vector stores pages each list what the request log's keys created: a user's personal keys and their teams' service account keys, every key of the org for an org admin, and the org chosen in the switch for an admin (`?org=all` for every org). Each is one table, newest first and 50 rows per page, with **Previous** and **Next** at the foot of the table and a summary line under it. Files shows each file's purpose, size, creation time, and expiry, with a preview of its first lines. Batches shows each batch's status with `Finish by <time>` while it runs, a progress bar, its input file, and links to its output and error files. Vector stores shows each store's owner, status, file count, size, and expiry, and its detail page lists its files and file batches. A row that is not the viewer's names its team or its user's email. The viewer deletes files and cancels batches that their personal keys created, and syncs the stores of keys they created and of `get_teams_for_user()` teams. Everything else is view-only. An empty table reads `No <items><whose> yet.` with a link to the API docs, where `<whose>` is ` from your keys or your teams' keys`, ` in <org>`, or empty for every org. Uploads and new batches and stores come from the API, not from these pages.

## Sub-features

- `resources-paging` shows 50 rows per page on Files, Batches, Vector stores, and a store's file list, through the shared `_pager.html` (`<first>–<last> of <n>`, `Previous`, `Next`) when there is more than one page. The summaries count every page. Delete and Cancel return to the page they were chosen on (`?page=`).
- `files-list` shows one row per file, newest first. A team's file shows the team name after its ID. The summary reads `<n> files · <size> · max <limit> MB each`.
- `files-preview` opens the first lines of a file in a popover. Only files with a stored preview, such as batch input, have the button.
- `files-delete` deletes a file after the confirm dialog (`Delete`), or the confirm page without JavaScript. It deletes the file upstream first and keeps it when that fails.
- `batches-list` shows one row per batch, with `<processed> of <total> · <n> failed` under the bar and `Finish by HH:MM` (or `Finish by D Mon HH:MM` on a later day) under a running status. A running batch without output reads `Output pending`. The summary reads `Running: you <n>/<limit> · <team> <n>/<limit>`.
- `batches-cancel` cancels a validating, in-progress, or finalizing batch after the confirm dialog (`Cancel batch`).
- `vector-stores-list` shows one row per store, with `You`, the team name, or the owner's email under Belongs to. The summary reads `Vector stores: you <n>/<limit> · <team> <n>/<limit>`.
- `vector-stores-detail` shows one store's status, ID, owner, `Created <date>`, `Never expires` or `Expires <date>`, `<n> files`, size, files, and file batches when there are any.
- `vector-stores-refresh` reloads a store from upstream with `Sync status <name>` in the list or `Sync status` on the detail page.
- `resources-scope` shows Delete and Cancel only on rows the viewer's personal keys created. Other rows, service account rows included, refuse the action with 404. Sync shows only where the sync views allow it; other stores answer `Vector store not found.`

## How to get to it (user POV)

- Choose **Files**, **Batches**, or **Vector stores** under Resources in the sidebar. The paths are `/aqueduct/management/files/`, `/aqueduct/management/batches/`, and `/aqueduct/management/vector-stores/`.
- Choose a store's name to open `/aqueduct/management/vector-stores/<id>/`.
- On Batches and on a vector store's detail page, a file name links to `/aqueduct/management/files/?file=<id>#<id>`. The Files page opens the page that holds the row and highlights it.

## Driving it with aqv

Preconditions:

- A fresh launch. Delete, cancel, and refresh call the Files API, which `aqv launch` points at its mock upstream.
- The seed gives `user` the files `eval-prompts.jsonl`, `eval-results.jsonl`, and `thesis-corpus.pdf`, the batches `batch-user-running` and `batch-user-done`, and the store `thesis-corpus`. The `verify-pipeline` service account of `Verify Team`, owned by `team-admin`, has the file `nightly.jsonl`, the batch `batch-team-running`, and the store `group-papers`.

- **Files.** Run `aqv login user --next /aqueduct/management/files/`. The summary reads `3 files · 17.6 MB · max 8 MB each`. Run `aqv click --role button --name "Preview eval-prompts.jsonl"` and `aqv shot files/preview-user --viewport`.
- **Delete a file.** Run `aqv click --role link --name "Delete eval-results.jsonl"`. The dialog asks `Delete eval-results.jsonl?`. Run `aqv click --role button --name Delete --exact`. The page reads `File eval-results.jsonl deleted.` and `2 files`. Read the stored state with `aqv shell -c "from management.models import FileObject; print(sorted(FileObject.objects.values_list('id', flat=True)))"`.
- **Batches.** Open `/aqueduct/management/batches/`. `batch-user-running` reads `In progress`, `Finish by <time>`, and `1,240 of 4,000 · 12 failed`. Run `aqv click --role button --name "Cancel batch-user-running"`, then `aqv click --role button --name "Cancel batch" --exact`. The page reads `Batch batch-user-running is being cancelled.` and the row reads `Cancelled`.
- **Team batch.** Run `aqv login team-admin --next /aqueduct/management/batches/`. `batch-team-running` has no Cancel button, though `team-admin` created the service account key. Service account batches are cancelled with that key over the API.
- **Vector stores.** Open `/aqueduct/management/vector-stores/` as `user`, and run `aqv click --role link --name thesis-corpus`. The detail page lists `thesis-corpus.pdf` as `Ready`. Run `aqv click --role button --name "Sync status"`. The page reads `Vector store thesis-corpus synced.`
- **Org admin.** Run `aqv login org-admin --next /aqueduct/management/files/`. It lists `user`'s files with ` · user@verify.invalid` and `nightly.jsonl` with ` · Verify Team`, with no delete buttons. Vector stores shows `group-papers` with a sync button and `thesis-corpus` without one. `other-org-admin` reads `No files in Other Org yet.`
- **Scope.** As `team-member`, open `/aqueduct/management/files/file-user-prompts/delete/`. It returns 404. `/aqueduct/management/vector-stores/vs_user_thesis/` reads `Vector store not found` with a `Back to vector stores` link. Signed out, every path redirects to `/login/`.

## Gotchas

- The quota line names only the viewer's own teams, so `org-admin` sees `you 0/10` alone though the table lists the whole org.
- The personal limits count everything the viewer's keys created, including service account keys they own. `team-admin` therefore counts `batch-team-running` under `you` and under `Verify Team`.
- A deleted output file leaves its batch with `No output file`.
