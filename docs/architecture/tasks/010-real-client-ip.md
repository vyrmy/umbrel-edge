---
id: 010
title: Real visitor addresses for Home Assistant through a trusting tunnel entrypoint
status: todo
depends_on: [009]
---

## Goal
With `apps.home-assistant.real_ip: true`, Home Assistant sees each internet visitor's own address, so its local-only webhooks and users stop working from the internet, and no other app can be given a spoofable address.

## Files
- `vyrmy-edge/docker-compose.yml` (existing): the `tunnel-realip` entrypoint
- `sync/src/umbrel_edge/models.py` (existing): `AppPolicy.real_ip`, `Route.real_ip`, validation
- `sync/src/umbrel_edge/desired.py` (existing): carry `real_ip` into the route
- `sync/src/umbrel_edge/traefik_writer.py` (existing): `tunnel-realip` in `entry_points`, the `realip-<id>` middleware
- `sync/src/umbrel_edge/cloudflare.py` (existing): ingress service `:8444` for `real_ip` routes
- `sync/tests/test_real_ip.py` (new), golden file `real-ip-route.yml` (new)
- `vyrmy-edge/edge.example.yaml`, `docs/architecture/ARCHITECTURE.md` (existing)

## Contract
- Static: `--entrypoints.tunnel-realip.address=10.30.0.2:8444`, the same hardening flags as `tunnel`, `forwardedHeaders.trustedIPs=10.30.0.0/24` and `forwardedHeaders.notAppendXForwardedFor=true`.
- `AppPolicy.real_ip: bool = False`. A config with `real_ip: true` and forward auth on for that app (explicitly or by default) is rejected, naming the app. `real_ip: true` on a route that is not external is rejected.
- `entry_points`: external with `real_ip` gives `tunnel-realip` in place of `tunnel`.
- Middleware `realip-<id>`: `headers.customRequestHeaders` with `X-Forwarded-Host: <hostname>`, `X-Forwarded-Proto: https`, and empty values for `X-Forwarded-Uri`, `X-Forwarded-Method`, `X-Forwarded-Port` and `X-Real-Ip`. Listed on the route's router on every entrypoint.
- Ingress: `real_ip` routes use `https://traefik:8444`.

## Steps
1. Tests first, including the two rejections and the ingress port.
2. Model, desired state, writer, ingress.
3. Compose flags. Release with Edge.
4. Owner, in Home Assistant's `configuration.yaml`: `http: {use_x_forwarded_for: true, trusted_proxies: [10.21.0.0/16]}`, then restart Home Assistant. Then set `apps.home-assistant.real_ip: true` in `edge.yaml`.

## Acceptance
- A request through the tunnel with a forged `X-Forwarded-For: 192.168.1.1` shows Home Assistant's log (or `/api/` error log line) with the visitor's public address, not `192.168.1.1` and not a 10.x or 172.x address.
- A `local_only` webhook called from mobile data returns 405 (Home Assistant's refusal), and the same call from the LAN works.
- Authentik's event log for a login through the tunnel shows `10.30.0.x` or the visitor's address, never a value the client chose (Authentik stays on `tunnel`).

## Tests
Validation errors; `entry_points`; middleware contents; ingress port; that a `real_ip` route never gets `authentik` in its middlewares.

## Out of scope
Real addresses for any other app. Authentik's own client-IP handling.
