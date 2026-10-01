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
