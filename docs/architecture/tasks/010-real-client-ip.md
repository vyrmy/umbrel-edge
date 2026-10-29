---
id: 010
title: Real visitor addresses for Home Assistant through a trusting tunnel entrypoint
status: done
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

## Results (29 October 2026, local)
- Unit tests:
  - `real_ip` is refused on a route that is not external (Home Assistant is in `external_deny`, so it needs `external: true` as well). It is also refused with forward auth, both when set and when `upstream_host` turns it on by default. The error names the app.
  - `build` fails closed if the config is changed after loading.
  - The route sits on `websecure` and `tunnel-realip`, never gets `authentik`, and other routes get no `realip-*` middleware.
  - Ingress uses `:8444` for it and `:8443` for the rest.
  - `tunnel-realip` carries the hardening flags and is the only entrypoint that trusts forwarded headers.
  - A golden file covers the route and its middleware.
- Traefik 3.7.13 with the compose flags as written, sending `X-Forwarded-For: 192.168.1.1,203.0.113.9` as cloudflared passes it on, plus forged `X-Forwarded-Uri`, `-Method`, `-Port`, `-Host`, `-Proto` and `X-Real-Ip`:
  - Through `:8444`, the app got `X-Forwarded-For: 192.168.1.1,203.0.113.9`, whose rightmost entry is the visitor. Host and Proto were pinned, and the other four were gone.
  - Through `:8443`, another app got only Traefik's own values.
  - Home Assistant is not served on `:8443`, and other apps are not served on `:8444`.
  - From the LAN, Home Assistant saw the LAN client's address, and `:8444` could not be reached.

Released in Edge 0.5.1, which pins `umbrel-edge-sync:sha-c7ec3d3`. Left: the owner's step 4 and the Acceptance checks on the Umbrel.

