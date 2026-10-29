---
id: 015
title: Desktops cannot open the Umbrel dashboard or Edge's own page
status: done
depends_on: [012]
---

## Goal
Routes for ids in `desktop_deny` (by default `umbrel` and `vyrmy-edge`) refuse requests that come from a desktop network, while the LAN, the VPN and the tunnel see no change.

## Files
- `sync/src/umbrel_edge/models.py` (existing): `EdgeConfig.desktop_deny`, `AppPolicy.desktops`, `Route.desktops`
- `sync/src/umbrel_edge/desired.py` (existing)
- `sync/src/umbrel_edge/traefik_writer.py` (existing): the `no-desktops` middleware
- `sync/tests/test_desktop_deny.py` (new), golden file update
- `vyrmy-edge/edge.example.yaml`, `docs/architecture/ARCHITECTURE.md` (existing)

## Contract
- `DESKTOP_NETWORKS = ip_network("10.31.0.0/16")` in `models.py`, matching task 012's subnets.
- `EdgeConfig.desktop_deny: list[str] = ["umbrel", "vyrmy-edge"]`; `AppPolicy.desktops: bool | None = None`; `Route.desktops: bool = True`, false for ids in `desktop_deny` unless `apps.<id>.desktops` is set.
- Middleware `no-desktops`: `ipAllowList.sourceRange` = `ip_network("0.0.0.0/0").address_exclude(DESKTOP_NETWORKS)` as sorted CIDR strings, plus `::/0`. Written once when any route has `desktops: false`, and listed first on that route's routers and on `edge-assets` and `edge-umbrel-fallback` for the dashboard.

## Steps
1. Tests first: the sourceRange never contains a `10.31.` address and covers `192.168.10.0/24`, `10.21.0.0/16`, `10.30.0.0/24` and a public address; the middleware is absent when no route needs it.
2. Model, desired state, writer.

## Acceptance
- From a desktop, `https://umbrel.bebitwise.dev` returns 403; from the LAN it loads.

## Tests
As in step 1, plus a golden file for the dashboard route.

## Out of scope
Restricting other apps for desktops. Per-person rules (Authentik's job).

## Results (29 October 2026, local)
- Unit tests:
  - `no-desktops` never contains a `10.31.` address.
  - Its IPv4 ranges add up to every address but `10.31.0.0/16`.
  - Every `desk-*` subnet in Edge's compose is inside `DESKTOP_NETWORKS`.
  - The middleware is first on `app-umbrel`, `edge-assets`, `edge-umbrel-fallback` and `outpost-umbrel`, ahead of `authentik` and `launcher-inject`.
  - It is absent when no route needs it.
  - `apps.<id>.desktops` overrides the deny list both ways.
  - The dashboard golden file carries the new middleware.
- Traefik 3.7.13 with a rendered file: from `10.31.1.10` on an internal `inhibit_ipv4` network, the dashboard hostname returned 403 and another app 200. From a LAN address both returned 200.

Left for the Umbrel, after the Edge release that pins this sync image: the Acceptance check.

