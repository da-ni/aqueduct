---
title: Home
nav_order: 1
---

# Aqueduct AI Gateway

**Aqueduct AI Gateway** aims to provide a **simple yet fully-featured** AI gateway you can self-host with:

- no [SSO tax](https://konghq.com/pricing)
- no [observability tax](https://www.litellm.ai/enterprise)
- no [self-hosting tax](https://portkey.ai/pricing)
- no [org management tax](https://www.litellm.ai/enterprise)
- etc.

![Aqueduct Gateway Screenshot](assets/screenshot.png)

## Key Features

- **Role-Based Access Control:** Manage users, teams, and organizations with flexible permissions.
- **Usage Tracking:** Monitor and limit API usage by organization, team, or user.
- **OpenAI-Compatible Relay:** Seamlessly proxy requests to LLM providers.
- **Admin UI:** Manage everything through a clean Django admin interface.

## Quick Start

To get started with the Aqueduct AI Gateway, open the **API keys** page of your Aqueduct instance and create a new key. The URL of the web UI depends on your specific Aqueduct deployment. See [API keys](user-guide/tokens.md) for details.

Once you have your key, use your Aqueduct instance's base URL (e.g., `https://your-instance.com/v1`) with OpenAI-compatible SDKs, and pass the key as your API key. The footer of every page in the web UI shows this URL.

---

Explore the navigation to learn more about configuration, usage, and advanced features.
