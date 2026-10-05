---
id: 005
title: Cloudflare Access in front of external routes
status: todo
depends_on: [004]
---

## Goal
Every external route with `access: true` requires a one-time PIN sent to an allowed email before Cloudflare forwards a single byte to the Umbrel.

## Files
- `sync/src/umbrel_edge/cloudflare.py` (existing): the Access part
- `sync/src/umbrel_edge/loop.py` (existing)
- `sync/tests/test_cloudflare_access.py` (new)

## Contract
The Access API exactly as in ARCHITECTURE.md. Errors raise `StageError("cloudflare_access", ...)`.

## Steps
1. In Zero Trust, enable the One-time PIN login method and set the team domain. This is manual, once.
2. Reconcile the Access apps by the `umbrel-edge:` name prefix. Create, update the session duration and policy emails, and delete the ones no longer wanted.
3. Order within a pass: Access apps are created before the DNS record for a new external route and deleted after it. A route is never public without its Access app when `access: true`.
4. Remove the task 004 gate that skipped `access: true` routes, now that the ordering in step 3 covers them.

## Acceptance
- From a phone on mobile data, `https://excalidraw.DOMAIN` shows the Cloudflare Access PIN page. An address outside `allowed_emails` gets no PIN.
- At home on Main, the same URL opens directly with no Access page, because it resolves to `192.168.10.4`.
- Removing an email from `edge.yaml` removes it from every managed policy within 60 seconds.
- Setting `apps.excalidraw.access: false` deletes only that app's Access app.

## Tests
Access app diffing, the ordering guarantee (assert call order with respx), and prefix-based ownership.

## Out of scope
Service tokens, SSO providers, device posture checks.

## Implementation notes
- The contract changed: policies are one reusable policy (`umbrel-edge:allowed-emails`) referenced by id from each app, not an inline policy per app. This is Cloudflare's current recommendation. I confirmed it from the developer docs (policy management page and the create-application API reference), but did not see a live response, so check the field names during live acceptance. ARCHITECTURE.md is amended.
- Ownership: apps and the policy are owned by the `umbrel-edge:` name prefix. An unprefixed app already serving the same hostname is logged as a conflict and skipped.
- Removing an address edits the single policy, so every app picks it up in one PUT.
- The shared policy is deleted only when no external route needs Access, after the last app is gone.
- Ordering lives in `_cloudflare` in `loop.py`: ingress, Access create and update, DNS, Access delete. If the Access stage fails, access-true hostnames without an app are withheld from DNS create and update; other routes publish as normal.
- The task 004 gate (`published()`) is replaced by `external_routes()`, which returns every external route.
- The API token already needs Access Apps and Policies Edit. Reusable policies sit under the same permission, but if the policy calls return 403, check that scope first.
- Live acceptance is still pending, so status stays "in-progress".
