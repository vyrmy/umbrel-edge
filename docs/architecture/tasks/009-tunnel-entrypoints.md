---
id: 009
title: Separate Traefik entrypoints for the tunnel, with routes placed by their flags
status: todo
depends_on: []
---

## Goal
Traefik serves tunnel traffic on its own entrypoint, so a route's `internal` and `external` flags decide where Traefik answers it, not only where DNS points.

## Files
- `vyrmy-edge/docker-compose.yml` (existing): the `tunnel` entrypoint, the hardening flags repeated on it, a fixed `edge` subnet and Traefik's address on it
- `sync/src/umbrel_edge/traefik_writer.py` (existing): `entryPoints` per route; companion routers copy the app router's list
- `sync/src/umbrel_edge/cloudflare.py` (existing): ingress service `https://traefik:8443`
- `sync/tests/test_traefik_writer.py`, `sync/tests/test_forward_auth.py`, `sync/tests/test_launcher.py`, `sync/tests/test_cloudflare_tunnel_dns.py` and the golden files (existing)
- `docs/architecture/ARCHITECTURE.md` (existing): mark decision 19 as built

## Contract
From ARCHITECTURE.md, Contracts, "Traefik static flags" and "Traefik dynamic file":

- Static: `--entrypoints.tunnel.address=10.30.0.2:8443`, plus `aliasHeadersStrategy=delete` and the three `encodedCharacters.allowEncoded*=false` flags on `tunnel`, exactly as on `websecure`. No `forwardedHeaders` on `tunnel`.
- Compose: `networks.edge.ipam.config: [{subnet: 10.30.0.0/24}]`; Traefik's `edge` entry gets `ipv4_address: 10.30.0.2`.
- `entry_points(route) -> list[str]`: `websecure` if `internal`, then `tunnel` if `external` (decision 20 later adds `tunnel-realip`). Never empty, because routes with neither flag are not written.
- `outpost-<id>`, `edge-assets` and `edge-umbrel-fallback` use the same list as the route they belong to.
- Ingress entries: `{"hostname": h, "service": "https://traefik:8443", "originRequest": {"originServerName": h}}`.

## Steps
1. Tests first: golden files gain `entryPoints` per the table, a route with `internal: false` lists only `tunnel`, an outpost router copies its app router, and ingress entries use `:8443`.
2. Add `entry_points` in `traefik_writer.py` and use it everywhere `["websecure"]` is written today, except the `dashboard` router.
3. Change `ORIGIN_SERVICE` in `cloudflare.py`. `_normalise` compares the service, so the first pass after the update rewrites every entry, which is intended.
4. Add the compose flags, subnet and address. Check `docker compose config` locally with the macvlan swapped for a bridge.
5. Release as an Edge minor version: push, pin the sync image `main` builds, bump `version`, with release notes saying that `internal: false` routes now answer only through the tunnel.

## Acceptance
- From the LAN, `https://<app>.DOMAIN` for a route with `internal: false, external: true` returns 404; through the tunnel it loads.
- A route with `external: false` returns 404 through the tunnel even if its hostname is added to the ingress by hand.
- `curl -k https://192.168.10.4:8443` from the LAN is refused (the entrypoint listens only on the `edge` address).
- `%5C` in a path through the tunnel returns 400, and `X_Authentik_Username` sent through the tunnel never reaches an app.
- Forward-auth logins complete through the tunnel (the outpost router answers on `tunnel`).

## Tests
Golden files for an internal-only, an external-only and a both route; companion routers; ingress service and drift detection on the old `:443` service.

## Out of scope
`real_ip` and `tunnel-realip` (task 010). The Access token check (task 011). Uptime Kuma monitors: list in the release notes which monitored hostnames are external-only and need `internal: true`.
