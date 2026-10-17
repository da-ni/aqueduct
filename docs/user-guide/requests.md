---
title: Requests
parent: User Guide
nav_order: 5
---

# Requests
{: .no_toc }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

The Requests page is the log of single API requests, newest first. Use it to find out why a request failed or how long it took. Choose **Requests** in the sidebar.

![Requests page](../assets/user_guide/requests.png)

## Whose requests you see

You see the same requests as on the [Overview](usage.md) page:

- Users see the requests of their own keys and of their teams' service accounts. They also see requests that any key in their organization sent with their e-mail address as [`user_id`](../api/index.md#sending-user_id-in-api-requests).
- Org admins see every request of their organization.
- Admins also choose an organization, or **All organizations**, in the organization select.

The log lists only requests to models that the gateway still serves.

## Read a request

Each row shows the time, the key, the model and the API path, the status code, the input tokens, the input tokens from cache, the output tokens, and the response time.

Choose a row, or the arrow at its start, to show its details. Choose it again to hide them.

- **User agent**: the client that sent the request.
- **User ID sent by the client**: the `user_id` value, if the client sent one.
- **Gateway overhead**: the time Aqueduct added to the provider's response time.
- **Output speed**: the output tokens per second.
- **What the status means**: the status code in plain words.

## Filter the log

- To show one kind of status, choose a tab: **All**, **Failed**, **2xx**, **429**, **Other 4xx**, or **5xx**. **Failed** holds every `4xx` and `5xx` request. Each tab shows its count.
- The log has the same filters as the [Overview](usage.md) page: the organization, **Everyone** or **My keys**, **All keys**, **All models**, and the time frame. The log opens on **All retained**, every request Aqueduct still keeps.
- On the Overview page, a status group, the **Requests** and **Failed** totals, and the request count of a model open the log with the same filters.

The log shows 50 requests per page. Choose **Older** and **Newer** to move between pages.

## How long requests are kept

The line under the table shows how many days Aqueduct keeps requests. The default is 30 days. Aqueduct deletes older requests once a day, and the Overview page cannot show them either. Your deployment sets the number of days with the `REQUEST_RETENTION_DAYS` environment variable.
