---
title: Developer Setup
nav_order: 5
---

# Developer Setup
{: .no_toc }

## Table of contents
{: .no_toc .text-delta }

1. TOC
{:toc}

---

Welcome! This guide will help you get up and running with the Aqueduct AI Gateway project.

## Quick Start (with Docker Compose)

The recommended way to get Aqueduct running locally is with Docker Compose. This will start the Django app, a PostgreSQL
database, Celery with Redis as the broker, Tika for text extraction from files, and a local mock OIDC provider (Dex)
for authentication.

1. **Clone the repository**
   ```bash
   git clone https://github.com/tu-wien-datalab/aqueduct.git
   cd aqueduct
   ```

2. **Set the necessary environment variables**
Most of the necessary environment variables are provided in the `.example.env` file.
You only need to set the `OPENAI_API_KEY`:
   ```bash
   export OPENAI_API_KEY="your-openai-api-key"
   ```
   This variable is used by the sample router configuration, provided in the
`example_router_config.yaml` file. Adjust it if you want to use other models.

3. **Start the services**
   ```bash
   docker compose up --build
   ```
   This will build and start all required services using the provided `.example.env` and
`example_router_config.yaml` files for environment variables and the router configuration.
For example, you could use vLLM to run a model locally.

4. **Access the application**

    - The web UI will be available at [http://localhost:8000](http://localhost:8000)
    - The local OIDC provider (Dex) will be running at [http://localhost:5556/dex](http://localhost:5556/dex)
    - Default login credentials for Dex are:
        - **Username:** `you@example.com`
        - **Password:** `1234`

You can now access the admin UI and start exploring the gateway features.

> **NOTE:**
> This starts Django in debug mode and is not suitable for production deployments. Change the
> [necessary settings](https://docs.djangoproject.com/en/5.2/topics/settings/#the-basics) for a production deployment.

---

## Local Setup (with `uv`)

If you prefer to run Aqueduct directly on your machine (for development), you can use [
`uv`](https://github.com/astral-sh/uv):

1. **Install `uv`**  
   If you don't have `uv` installed, you can install it via pip:
   ```bash
   pip install uv
   ```

2. **Clone the repository**
   ```bash
   git clone https://github.com/tu-wien-datalab/aqueduct.git
   cd aqueduct
   ```

3. **Create a virtual environment and install dependencies**
   ```bash
   uv venv
   source .venv/bin/activate
   uv sync
   ```

4. **Run the Django development server**
   ```bash
   uv run aqueduct/manage.py runserver
   ```

_Note: You will need to have Dex running locally, or adjust your environment variables accordingly to use an existing OIDC provider._

---

---

## Demo Data at Production Scale

`seed_demo` fills a development database with two orgs and a month of request log, so the
management pages can be checked against realistic volume. It runs only with `DEBUG` on.

```bash
cd aqueduct
uv run python manage.py seed_demo                 # 300 + 30 teams, 100,000 requests a day for 30 days
uv run python manage.py seed_demo --reset         # replace earlier demo data
uv run python manage.py seed_demo --delete        # remove it
uv run python manage.py seed_demo --teams 20 --small-teams 5 --requests-per-day 5000 --days 7
```

The large org `seed-demo-campus` and the small org `seed-demo-institute` get teams of three to
five people, a few team and org admins, one to three personal keys per user, and service accounts
with keys. Every seeded user has an `@seed.invalid` address. `--reset` and `--delete` remove only
those users, the `seed-demo-` orgs, and the keys, requests, files, batches, and vector stores
attached to them. The same `--seed` and end time give the same data.

Requests use only the models of the router config in `LITELLM_ROUTER_CONFIG_FILE_PATH`. To set
the model mix, pass `--models-file` with a YAML or JSON file in the format of
`aqueduct/management/management/commands/seed_demo_models.example.yaml`. When the file names
models that the router config does not serve, the command stops. Run it once with
`--write-router-config <path>` to write a router config that serves them, then point
`LITELLM_ROUTER_CONFIG_FILE_PATH` at that file.

On Postgres, the default 3 million requests take about two minutes, including the usage rollup
rebuild. SQLite is slower, so use fewer requests there.
