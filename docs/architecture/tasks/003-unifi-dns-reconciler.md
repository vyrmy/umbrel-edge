---
id: 003
title: Internal DNS records in UniFi
status: todo
depends_on: [002]
---

## Goal
Every route with `internal: true` has a UniFi A record pointing at `proxy_ip`. Records this service created, and no longer wants, are deleted.

## Files
- `sync/src/umbrel_edge/unifi.py` (new)
- `sync/src/umbrel_edge/ownership.py` (new)
- `sync/src/umbrel_edge/loop.py` (existing)
- `sync/tests/test_unifi.py` (new)

## Contract
The UniFi Integration API and `ownership.json` exactly as in ARCHITECTURE.md. Errors raise `StageError("unifi", ...)`.

## Steps
1. Check the DNS policy create, list and delete request and response fields against developer.ui.com for Network 10.6. Amend the Contracts section if they differ.
2. Create an API key in UniFi (Control Plane → Integrations) with the least role that can manage DNS. Add it to `secrets.env`.
3. Reconcile. List the existing policies. Create the missing ones. Where a record's address differs, update it if the id is owned, otherwise log a conflict and skip it. Delete owned ids no longer desired.
4. Write `ownership.json` atomically after each successful create or delete.

## Acceptance
- The `umbrel.DOMAIN` record created by hand in task 001 stays untouched, because it isn't owned.
- Installing an app creates its record within 60 seconds, and `nslookup <app>.DOMAIN 192.168.1.1` from the Mac returns `192.168.10.4`.
- Uninstalling it deletes the record. A record you created by hand with the same name is logged as a conflict and never deleted.
- With the UniFi API unreachable, the Traefik stage still runs and `/healthz` reports stage `unifi`.

## Tests
respx fakes covering create, update, delete, a conflict with an unowned record, a 429 retried then succeeding, and a 401 not retried.

## Out of scope
Cloudflare. Any UniFi firewall or network changes.
