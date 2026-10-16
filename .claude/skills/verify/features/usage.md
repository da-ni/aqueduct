# Usage

The usage dashboard shows request and token totals, requests over time, requests by model, and request counts per API key. Users see their own keys and their teams' service accounts. Org admins see every key in their org. Admins pick any org or all orgs.

## Sub-features

- `usage-totals` shows total and failed requests, average response times, and input, cached, output, and total tokens.
- `usage-charts` shows requests over time and requests by model.
- `usage-keys` lists request counts by API key with each key's owner.
- `usage-span` switches the time frame between 1 hour, 1 day, 1 week, 1 month, and a custom range.
- `usage-org` lets an admin choose one org or all orgs.

## How to get to it (user POV)

- Choose **Usage** in the top navigation. The path is `/aqueduct/management/usage/`.
- Choose a time frame tab: `1h`, `1d`, `1w`, `1m`, or `Custom`.
- As an admin, pick an org in the `Organization:` select, or choose `Show all orgs`.

## Driving it with aqv

Preconditions:

- A fresh launch. The seed spreads 280 requests over the last 7 days across all seeded keys.

- **Org admin view.** Run `aqv login org-admin --next /aqueduct/management/usage/?span=1w`. The totals show more than 200 requests, and `Request Counts by API Key` lists all five seeded keys, with `verify-pipeline` marked `Service Account`. Run `aqv shot usage/week-org-admin`.
- **User view.** Run `aqv login user --next /aqueduct/management/usage/?span=1w`. The per-key table lists only `user-laptop`.
- **Admin org choice.** Run `aqv login admin --next /aqueduct/management/usage/`. The `Organization:` combobox offers `All`, `Verify Org`, and `Other Org`. Run `aqv select --label "Organization:" --value All`. The browser lands on `/aqueduct/management/usage/?org=all`. The seed puts no requests in `Other Org`, so the totals match `Verify Org`.
- **Cross-check.** Run `aqv shell -c "from management.models import Request; from django.utils import timezone; from datetime import timedelta; print(Request.objects.filter(timestamp__gte=timezone.now()-timedelta(days=7)).count())"` and compare it with the admin's all-orgs weekly total.

## Gotchas

- The time frame tabs are not buttons in the accessibility tree. Set the frame with `?span=1h`, `1d`, `1w`, or `1m` in the path instead.
- Seed timestamps are relative to launch time. The `1d` frame holds about 40 requests.
- The seeded requests carry no reasoning tokens, so `Of which 0 were reasoning tokens` is expected.
- The `user_id` of every seeded request is its key owner's email.
