---
id: 014
title: Move each desktop slot onto its private network, with its own egress proxy
status: todo
depends_on: [012, 013]
---

## Goal
Each desktop container sits only on `vyrmy-edge_desk-<n>`, reaches the internet only through its own Squid, and reaches the Umbrel only through Traefik; nothing on the Umbrel except Traefik can reach its port 3000.

## Files
- `vyrmy-desktop-1/docker-compose.yml`, `vyrmy-desktop-2/docker-compose.yml` (existing)
- `vyrmy-desktop-1/umbrel-app.yml`, `vyrmy-desktop-2/umbrel-app.yml` (existing): `dependencies: [vyrmy-edge]`, description and release notes
- `scripts/new-desktop-slot` (existing): substitutes `<n>` in addresses and the network name
- `docs/architecture/ARCHITECTURE.md` (existing)

## Contract
Slot `n` compose, from ARCHITECTURE.md Contracts, "Desktop slot":

- `desktop`: `networks: {desk: {ipv4_address: 10.31.<n>.10}}` only. Environment adds `EGRESS_PROXY: http://10.31.<n>.3:3128`, `EGRESS_DIRECT_DOMAIN: bebitwise.dev`, `EGRESS_DIRECT_HOST: 10.31.<n>.2` and `SELKIES_ENABLE_DUAL_MODE: "false|locked"`.
- `egress`: the Squid service from Contracts, with `acl clients src 10.31.<n>.10/32`; `networks: {desk: {ipv4_address: 10.31.<n>.3}, egress: {}}`.
- `placeholder`: a pinned `nginx:alpine-slim` on `default` (umbrel_main_network) serving one inline page that says the desktop opens at its own address; `app_proxy` `APP_HOST: vyrmy-desktop-<n>_placeholder_1`, `APP_PORT: 8080`. Umbrel's login stays on this port.
- Top level: `networks: {desk: {external: true, name: vyrmy-edge_desk-<n>}, egress: {driver: bridge}}`.
- `edge.yaml` is unchanged: `upstream_host: vyrmy-desktop-<n>_desktop_1`, `upstream_port: 3000` still resolve, because Traefik is on `desk-<n>`.

## Steps
1. Write slot 1, then generate slot 2 with `new-desktop-slot` logic (or by hand) and diff them.
2. Locally, with Edge's compose from task 012 running: the desktop starts, its stream loads through Traefik, Chromium and the WhatsApp window browse through the proxy, and from inside the desktop `192.168.x`, `10.21.x`, the host, `desk-2` and Traefik `:8080` are all unreachable while `https://<app>.DOMAIN` loads. Docker's embedded DNS answers nothing external (`getent hosts example.com` fails).
   Also confirm Squid's `access_log ... refused` line logs refused requests and nothing else. Task 013's run on Squid 6.13 confirmed it; recheck on the pinned image. Squid also writes `WARNING: refused ACL is used in context without an HTTP response` to stderr for connections that close before a request (Chromium preconnects, about 86 in 10 minutes). It is noise in `docker logs` only; accept it.
3. Release the desktops only after the Edge release from task 012 is installed on the Umbrel; say so in the release notes.

## Acceptance
- On the Umbrel: `docker exec vyrmy-desktop-1_desktop_1 getent hosts example.com` fails; `curl` from inside to `192.168.10.2:80`, `10.21.0.1` and `1.1.1.1` fails; Chromium opens `https://jellyfin.bebitwise.dev` and a public site.
- From another app's container: `vyrmy-desktop-1_desktop_1:3000` does not resolve and `10.31.1.10:3000` is unreachable.
- `https://desk-<name>.bebitwise.dev` still asks for Authentik and shows the desktop; `192.168.10.2:18101` shows the placeholder after Umbrel's login.
- Signal and WhatsApp send and receive messages. Calls are expected not to connect (decision 18).

## Tests
The local run in step 2, recorded here.

## Out of scope
Blocking the dashboard from desktops (task 015). Calls.
