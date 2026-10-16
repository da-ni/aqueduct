---
title: Batches
parent: User Guide
nav_order: 8
---

# Batches

The Batches page lists the batch jobs that your keys created through the [Batches API](../api/batches.md). A batch runs the requests of an uploaded JSON Lines (`.jsonl`) file. Choose **Batches** under **Resources** in the sidebar.

![Batches page](../assets/user_guide/batches.png)

The table lists the batches of your personal keys and of your teams' service accounts. Each row shows:

- **Batch**: the batch ID and the API endpoint its requests call.
- **Status**: the state of the batch, such as **Validating**, **In progress**, **Completed**, **Failed**, or **Cancelled**.
- **Progress**: a bar with the processed and total requests and the number of failed requests.
- **Files**: the input file, and links to the output and error files when they exist. A file name opens its row on the [Files](files.md) page.
- **Created** and **Expires**: when the batch was created and when Aqueduct deletes it.

The line under the table counts your running batches against the limits for your keys and for each team. Only validating and in-progress batches count toward these limits.

## Cancel a batch

You can cancel a validating, in-progress, or finalizing batch that your personal keys created. Choose **Cancel** in the batch's row, then confirm. Requests that the batch has not run yet are dropped.

To cancel a service account's batch, use the [Batches API](../api/batches.md) with the service account key.
