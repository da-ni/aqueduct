# Terms of service

A signed-in user who has not accepted the current terms sees them before any management page. Accepting returns the user to the page they asked for. Declining signs them out.

## Sub-features

- `tos-prompt` redirects a user without an agreement to the terms page.
- `tos-accept` stores the agreement and continues to the requested page.
- `tos-decline` signs the user out.

## How to get to it (user POV)

- Sign in as a user who has not accepted the terms and open any management page. The browser lands on `/terms-of-service/confirm/?next=<path>`.
- Choose **Terms of service** under **Help** in the footer to read the terms without the prompt.

## Driving it with aqv

Preconditions:

- A fresh launch. `new-user` has no agreement. Every other seeded account has one.

- **Prompt.** Run `aqv login new-user`. The JSON line shows `url` `/terms-of-service/confirm/?next=/aqueduct/management/usage/`. The page has the logo and footer but no sidebar. It shows the heading `Accept the terms to continue`, `Version of <d Mon YYYY>`, the text `Verification terms of service.`, and the buttons `Decline and sign out` and `Accept`. Run `aqv shot terms/prompt-new-user`.
- **Accept.** Run `aqv click --role button --name "Accept"`. The browser lands on `/aqueduct/management/usage/`.
- **Decline.** On a fresh launch, run `aqv login new-user`, then `aqv click --role button --name "Decline and sign out"`. The browser lands on `/login/`.
- **Stored value.** Run `aqv shell -c "from tos.models import UserAgreement; print(UserAgreement.objects.filter(user__username='new-user').count())"`. It prints `1`.

## Gotchas

- The prompt guards page loads only. Form submissions and other non-GET requests skip it.
- Members of the `admin` group skip the prompt through a cache entry that the server writes at startup and on user saves. The seeded `admin` also has an agreement, so it never sees the prompt either way.
- Accepting is permanent for the run. Launch again to see the prompt a second time.
