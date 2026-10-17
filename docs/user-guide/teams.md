---
title: Teams
parent: User Guide
nav_order: 6
---

# Teams
{: .no_toc }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

A team groups users of an organization and owns service accounts. A service account has one API key that belongs to the team instead of to a person, for example for a pipeline or a shared application.

## Read the team list

Choose **Teams** in the sidebar.

![Teams page](../assets/user_guide/teams.png)

Users see the teams they are a member of, with their role in each. Org admins see every team of their organization. Admins see the organization they chose in the sidebar. See [Choose an organization](index.md#choose-an-organization).

Each row shows the number of members and service accounts, the request limit per minute of the team, and the tokens of the team's service accounts over the last seven days. The search box filters by team name. The list shows 50 teams per page. Choose **Next** and **Previous** below the table to see the others.

The line above the table shows the default request limit of the organization. This limit applies when no team or user limit is set. Org admins and admins get an **Edit** link that opens the limits form of the [Organization](organization.md#change-the-limits) page. Admins also get **Edit in admin**, which opens the organization in the Admin panel.

If a request goes over a limit, the API answers with HTTP status `429 Too Many Requests`.

## Create or delete a team

Org admins and admins manage teams:

1. Choose **Create team** on the Teams page.
2. Enter a **Name** and, optionally, a **Description**.
3. Choose **Create team**.

To delete a team, open it and choose **Delete**, then confirm. Its service accounts are deleted with their keys and everything the keys created. Members keep their personal keys.

## Open a team

Choose a team's name to open its page. The page shows:

- The number of members, the number of service accounts against the maximum of 10, and the request limit.
- Totals of the team's service accounts over the last seven days.
- **Service accounts**, with each owner, tokens, last use, and expiry.
- **Members**, with each member's role. Team admins come first, then the other members by email, 50 per page. The search box next to **Members** filters by email.
- **Limits**, with the rate limits of each service account key. Org admins also see the models and MCP servers that are unavailable to the team.

![Team page](../assets/user_guide/team_detail.png)

The team figures count only service account keys. Members' personal keys follow their own limits and do not count toward the team.

Team admins and org admins choose **Edit** to change the team's name and description.

## Manage service accounts

Team admins and org admins manage service accounts on the team page.

To add a service account:

1. Choose **Add service account**.
2. Enter a **Name** and, optionally, a **Description** and a **Key expiry**.
3. Choose **Create service account**.

The **Your key is ready** panel shows the new key once. Copy it now. You become the owner of the service account.

The buttons at the end of each service account row are:

- **Rotate** replaces the key and shows the new one in the **Your key is ready** panel. The old key stops working immediately.
- **Edit** changes the name, the description, or the key expiry.
- **Transfer** gives the service account to another team member. Enter the email of the **New owner**, then choose **Transfer ownership**. The new owner must be a member of the team.
- **Delete** deletes the service account and its key after you confirm.

## Manage members

Team admins and org admins manage members:

- To add a member, enter the person's email and choose **Add member**. The person must belong to the team's organization. Upper and lower case do not matter.
- To remove a member, choose the delete button in the member's row, then **Remove**. You cannot remove a member who owns a service account of the team. Transfer the service account first.

Org admins and admins also appoint team admins. Choose **Make team admin** in a member's row. **Remove team admin** takes the role away. Team admins cannot appoint other team admins.

## Limits and model availability

Team limits are set in the Admin panel. Admins get an **Edit** link next to **Limits** on the team page. See [Admin](admin.md).

Org admins and admins choose the models and MCP servers that the team's service accounts can use. Choose **Edit** under **Unavailable to service accounts** on the team page. Every box starts checked. Clear a box to make the model or server unavailable to the team's service account keys. Members' personal keys are not affected. **Also apply the organization's unavailable models** adds the organization's list. Clear it to use only the team's boxes.
