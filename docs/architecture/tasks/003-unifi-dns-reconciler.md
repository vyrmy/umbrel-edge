---
id: 003
title: Internal DNS records in UniFi
status: in-progress
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

## Implementation notes
- Endpoints and fields confirmed against the official OpenAPI document for Network 10.1.84, https://developer.ui.com/network/v10.1.84/openapi.json (the page https://developer.ui.com/network/v10.1.84/creatednspolicy renders from it). I could not fetch the 10.6 docs, but the contract in ARCHITECTURE.md matches, so it is unchanged. Paths: `GET`/`POST sites/{siteId}/dns/policies`, `GET`/`PUT`/`DELETE .../dns/policies/{dnsPolicyId}`. The list is paged (`offset`, `limit` up to 200, `totalCount`). Create and update both take `type: A_RECORD`, `enabled`, `domain`, `ipv4Address` and `ttlSeconds` (all required); sync sends a TTL of 300. Update is a full `PUT`.
- The stage is skipped, with one info log per pass, unless `UNIFI_HOST`, `UNIFI_API_KEY` and `UNIFI_SITE_ID` are all set.
- TLS verification is off for the UniFi client only.
- A corrupt `ownership.json` fails the stage rather than resetting, so records are never orphaned. An owned id that has vanished from UniFi is forgotten, and recreated if still wanted.
- Deleting an already-missing record (404) counts as success.
- Live acceptance is still pending, so status stays "in-progress".


## Acceptance results (5 October 2026, Edge 0.2.0)
- First run: every pass failed with `GET /dns/policies: HTTP 400` because `UNIFI_SITE_ID` was `default`. The Integration API wants the site's UUID from `GET /proxy/network/integration/v1/sites` (`id`; `internalReference` is `default`). The other stages carried on, as designed.
- Passed after setting the UUID: sync created one A record per route, and all 69 hostnames for installed apps plus `umbrel` resolve to `192.168.10.4` through `192.168.1.1`.
- Passed: the hand-made `umbrel.bebitwise.dev` record is logged as a conflict on every pass and left alone.
- Found: `app-data/lobe-chat/umbrel-app.yml` is left over from an app that is no longer installed, so it got a route and a record pointing at a dead port. Discovery cannot tell installed apps from leftovers.
- Pending: install and uninstall an app to time the record appearing and going.
