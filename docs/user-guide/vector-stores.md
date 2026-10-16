---
title: Vector stores
parent: User Guide
nav_order: 9
---

# Vector stores

The Vector stores page lists the vector stores that your keys created through the [Vector Stores API](../api/vector-stores.md). A vector store holds files that are split, embedded, and indexed for file search. Choose **Vector stores** under **Resources** in the sidebar.

![Vector stores page](../assets/user_guide/vector_stores.png)

The table lists the stores of your personal keys and of your teams' service accounts. Org admins also see the stores of every team in their organization. Each row shows:

- **Vector store**: the name and ID.
- **Belongs to**: **Yours**, or the team name.
- **Status**: whether the store is ready or still processing files.
- **Files**: the number of files against the maximum per store.
- **Size**: the total size of the files.
- **Expires**: when the store expires, if it has an expiry.

The line under the table counts your stores against the limits for your keys and for each team.

## Open a vector store

Choose a store's name to open its page. The page shows the store's status, ID, owner, dates, file count, and size, and lists its **Files** and **File batches** with the status of each.

## Refresh a vector store

Aqueduct stores a copy of each vector store's status. To load the current status from the provider, choose the refresh button in the store's row, or **Refresh** on the store's page.

To create a vector store or add files to it, use the [Vector Stores API](../api/vector-stores.md).
