# Files, batches, and vector stores

The Files, Batches, and Vector stores pages each list what the viewer's own keys created and what the service account keys of their teams created, in one table with a summary line under it. Files shows each file's purpose, size, upload time, and expiry, with a preview of its first lines. Batches shows each batch's status, a progress bar, its input file, and links to its output and error files. Vector stores shows each store's owner, status, file count, size, and expiry, and its detail page lists its files and file batches. The viewer deletes files and cancels batches that their personal keys created. Uploads and new batches and stores come from the API, not from these pages.

## Sub-features

- `files-list` shows one row per file, newest first. A team's file shows the team name after its ID. The summary reads `<n> files · <size> · up to <limit> MB per file`.
- `files-preview` opens the first lines of a file in a popover. Only files with a stored preview, such as batch input, have the button.
- `files-delete` deletes a file after a confirmation page. It deletes the file upstream first and keeps it when that fails.
- `batches-list` shows one row per batch, with `<processed> of <total> · <n> failed` under the bar. The summary counts running batches against the per-user and per-team limits.
- `batches-cancel` cancels a validating, in-progress, or finalizing batch.
- `vector-stores-list` shows one row per store, with `Yours` or the team name under Belongs to. The summary counts stores against the per-user and per-team limits.
- `vector-stores-detail` shows one store's status, ID, owner, dates, file count, size, files, and file batches.
- `vector-stores-refresh` reloads a store from upstream, from the list or the detail page.
- `resources-scope` shows Delete and Cancel only on rows the viewer's personal keys created. Other rows, service account rows included, refuse the action with 404.

## How to get to it (user POV)

- Choose **Files**, **Batches**, or **Vector stores** under Resources in the sidebar. The paths are `/aqueduct/management/files/`, `/aqueduct/management/batches/`, and `/aqueduct/management/vector-stores/`.
- Choose a store's name to open `/aqueduct/management/vector-stores/<id>/`.
- On Batches, a file name links to its row on the Files page.

## Driving it with aqv

Preconditions:

- A fresh launch. Delete, cancel, and refresh call the Files API, which `aqv launch` points at its mock upstream.
- The seed gives `user` the files `eval-prompts.jsonl`, `eval-results.jsonl`, and `thesis-corpus.pdf`, the batches `batch-user-running` and `batch-user-done`, and the store `thesis-corpus`. The `verify-pipeline` service account of `Verify Team`, owned by `team-admin`, has the file `nightly.jsonl`, the batch `batch-team-running`, and the store `group-papers`.

- **Files.** Run `aqv login user --next /aqueduct/management/files/`. The summary reads `3 files · 17.6 MB · up to 8 MB per file`. Run `aqv click --role button --name "Preview eval-prompts.jsonl"` and `aqv shot files/preview-user --viewport`.
- **Delete a file.** Run `aqv click --role link --name "Delete eval-results.jsonl"`. The page shows `Confirm Deletion`. Run `aqv click --role button --name "Confirm Delete"`. The page reads `File 'eval-results.jsonl' deleted.` and `2 files`. Read the stored state with `aqv shell -c "from management.models import FileObject; print(sorted(FileObject.objects.values_list('id', flat=True)))"`.
- **Batches.** Open `/aqueduct/management/batches/`. `batch-user-running` reads `In progress` and `1,240 of 4,000 · 12 failed`. Run `aqv click --role button --name "Cancel batch-user-running"`. The page reads `Batch batch-user-running is being cancelled.` and the row reads `Cancelled`.
- **Team batch.** Run `aqv login team-admin --next /aqueduct/management/batches/`. `batch-team-running` has no Cancel button, though `team-admin` created the service account key. Service account batches are cancelled with that key over the API.
- **Vector stores.** Open `/aqueduct/management/vector-stores/` as `user`, and run `aqv click --role link --name thesis-corpus`. The detail page lists `thesis-corpus.pdf` as `Ready`. Run `aqv click --role button --name Refresh`. The page reads `Synced "thesis-corpus" from upstream.`
- **Scope.** As `team-member`, open `/aqueduct/management/files/file-user-prompts/delete/`. It returns 404. `/aqueduct/management/vector-stores/vs_user_thesis/` reads `Vector store not found or you do not have access.` Signed out, every path redirects to `/login/`.

## Gotchas

- Files and Batches show the teams the viewer is a member of. Vector stores also shows every team of the org to an org admin, so `org-admin` sees `group-papers` there but not the team's files or batches.
- The personal limits count everything the viewer's keys created, including service account keys they own. `team-admin` therefore counts `batch-team-running` under `running yours` and under `running in Verify Team`.
- A deleted output file leaves its batch with `No output file`.
- The browser accepts the `confirm()` dialog of Cancel.
