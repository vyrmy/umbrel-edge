---
id: 011
title: cloudflared refuses tunnel requests without a valid Access token
status: done
depends_on: [009]
---

## Goal
For every external route with `access: true`, cloudflared itself checks the Cloudflare Access token, so a deleted or misconfigured Access app no longer leaves the hostname open.

## Files
- `sync/src/umbrel_edge/models.py` (existing): `AccessSettings.team_name`
- `sync/src/umbrel_edge/cloudflare.py` (existing): ingress `originRequest.access`, the Access app `aud` in the plan
- `sync/src/umbrel_edge/loop.py` (existing): stage order
- `sync/tests/test_cloudflare_access.py`, `sync/tests/test_cloudflare_tunnel_dns.py`, `sync/tests/test_loop.py` (existing)
- `vyrmy-edge/edge.example.yaml`, `docs/architecture/ARCHITECTURE.md` (existing)

## Contract
- `AccessSettings.team_name: str | None = None`: the `<team>` in `<team>.cloudflareaccess.com`, pattern `^[a-z0-9-]+$`. Unset keeps today's ingress.
- With it set, each ingress entry for a route with `access: true` gets `"access": {"required": true, "teamName": "<team>", "audTag": ["<aud of umbrel-edge:<hostname>>"]}` inside `originRequest`. Routes with `access: false` get none.
- Pass order: Access create and update, tunnel ingress, DNS, Access delete. An `access: true` hostname whose Access app is missing or has no `aud` after the create phase gets no ingress entry and no DNS record in that pass.
- `_normalise` includes the access block, so a changed `aud` is drift.

## Steps
1. Tests first: the ingress body with and without `team_name`; a failed Access create withholds that hostname from ingress and DNS; reordering keeps every existing ordering test meaningful.
2. Keep each managed app's `aud` from the list call (and from the create response) in `AccessPlan`.
3. Reorder `_cloudflare` in `loop.py` and build ingress from the plan.
4. Owner: set `access.team_name` in `edge.yaml`.

## Acceptance
- `curl https://<access app>.DOMAIN` with a forged `Cf-Access-Jwt-Assertion` reaching the tunnel returns 502 or 403 from cloudflared; without any token, 403. With a real Access login the app loads.
- Deleting an Access app by hand in Zero Trust makes its hostname return cloudflared's 403 until the next pass recreates the app, instead of loading.
- `auth.DOMAIN` and Home Assistant (both `access: false`) are unaffected.

## Tests
Ingress builder, ordering, withholding, drift on `aud`.

## Out of scope
Using Authentik as Access's identity provider.

## Results (29 October 2026, local)
- Unit tests:
  - `team_name` accepts only a bare team.
  - Without it the ingress is unchanged. With it, access-true entries carry `{required, teamName, audTag}`, access-false entries carry nothing, and an access-true route with no known `aud` gets no entry.
  - A changed `aud`, or a dropped check, is drift.
  - The plan keeps the `aud` of managed apps and of an owner's own app on the same domain, and a create records the `aud` from its response.
- Pass tests:
  - Access apps are created before the ingress PUT, which comes before DNS, and every entry carries its new app's `aud`.
  - An app deleted by hand whose recreation fails: the hostname leaves the ingress and its DNS record is deleted.
  - When the Access apps cannot be read, the ingress is not touched and DNS creates nothing.
  - A second pass with everything in place writes nothing.
  - Without `team_name`, an Access listing failure still updates the ingress, as before (existing test).

Released in Edge 0.5.1, which pins `umbrel-edge-sync:sha-c7ec3d3`. Left: the owner's step 4 and the Acceptance checks.

