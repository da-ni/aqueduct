---
title: Files
parent: User Guide
nav_order: 7
---

# Files

The Files page lists the files that your keys uploaded through the [Files API](../api/files.md). Choose **Files** under **Resources** in the sidebar.

![Files page](../assets/user_guide/files.png)

The table lists the files of your personal keys and of your teams' service accounts. Org admins see the files of every key in their organization, and admins see those of the organization chosen in the sidebar, or of every organization. The newest files come first, 50 per page. Choose **Next** and **Previous** under the table to move between pages. Deleting a file keeps you on the same page. A file that is not yours shows its team, or the email of the user who uploaded it, after its ID. Each row shows:

- **File**: the file name and ID.
- **Purpose**: what the file is for, such as `batch`.
- **Size**: the file size.
- **Created**: when the file was uploaded.
- **Expires**: when Aqueduct deletes the file.

The line under the table shows the number of files on all pages, their total size, and the maximum size per file, as in `3 files · 17.6 MB · max 8 MB each`.

## Preview a file

Choose **Preview** to show the first lines of a file. Only files with a stored preview, such as batch input files, have this button. Choose **Close** to hide the preview.

## Delete a file

You can delete files that your personal keys uploaded. Other files, including the files an org admin sees of other users, are view-only.

1. Choose the delete button in the file's row.
2. Choose **Delete**.

Aqueduct deletes the file at the provider first. If that fails, Aqueduct keeps the file. To delete a service account's file, use the Files API with the service account key.

To upload a file, use the [Files API](../api/files.md).
