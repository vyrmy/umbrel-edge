---
id: 004
title: cloudflared in the app, tunnel ingress and public DNS
status: todo
depends_on: [002]
---

## Goal
Every route with `external: true` is reachable from outside at `https://<app>.DOMAIN` through the tunnel.

## Files
- `vyrmy-edge/docker-compose.yml` (existing): add `cloudflared` on the `edge` bridge only
- `sync/src/umbrel_edge/cloudflare.py` (new): tunnel and DNS parts
- `sync/src/umbrel_edge/loop.py` (existing)
- `sync/tests/test_cloudflare_tunnel_dns.py` (new)

## Contract
Tunnel ingress and DNS exactly as in ARCHITECTURE.md. Errors raise `StageError("cloudflare_tunnel" | "cloudflare_dns", ...)`.

## Steps
1. In the Cloudflare dashboard, create a new remotely managed tunnel, `umbrel-edge`. Put its token and id in `secrets.env`. Create the API token with the three scopes listed in Architecture.
2. Run `cloudflared tunnel run` with `TUNNEL_TOKEN` and no ingress config. Ingress comes from the API.
3. Build the full ingress list from the desired state, compare it with `GET` on the same path, and `PUT` only on difference.
4. Reconcile the DNS CNAMEs. Only records whose comment is `managed-by=umbrel-edge` are ever modified or deleted. An existing unmanaged record with the same name is a logged conflict.
5. Order within a pass: ingress first, then DNS. This stops a public name pointing at the tunnel before the tunnel knows it.
6. Until task 005 lands, publish only routes with `access: false`. Skip every route with `access: true` and log it. This stops anything going public without Access in the gap between the two tasks.

## Acceptance
- With task 005 not yet done, set `access: false` on one throwaway app (Excalidraw) only. It loads from a phone on mobile data, and `curl -I https://portainer.DOMAIN` from outside returns an NXDOMAIN error.
- Tunnel ingress never contains a deny-listed app, even if the dashboard was edited by hand. The next pass overwrites it.
- Deleting the app removes its CNAME and its ingress entry within 60 seconds.
- An unmanaged record `www.DOMAIN` is never touched.

## Tests
Ingress list building, including the trailing 404 rule and stable ordering. No `PUT` when nothing changed. DNS create, update and delete, conflict handling, and the retry policy.

## Out of scope
Cloudflare Access (task 005). Removing the old Umbrel Cloudflare Tunnel app (task 006).
