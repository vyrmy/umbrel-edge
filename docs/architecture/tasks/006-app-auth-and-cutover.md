---
id: 006
title: Per-app checks, Home Assistant proxy settings and cut-over
status: todo
depends_on: [003, 005, 007]
---

## Goal
Every installed app works at its new hostname, both inside and outside. The old access paths are retired, and Uptime Kuma watches the lot.

## Files
- `vyrmy-edge/edge.example.yaml` (existing): record the per-app overrides you settle on
- `docs/architecture/ARCHITECTURE.md` (existing): update Risks with the findings
- Home Assistant `configuration.yaml` on the Umbrel (existing, outside the repo)

## Steps
1. For each app, load it internally and externally. Note any Umbrel app-auth redirect failure (umbrel#2242), WebSocket failure or broken asset path.
2. Where Umbrel app auth breaks on the custom hostname, decide per app. Either turn it off with `umbreld client apps.setSettings.mutate --appId <id> --appProxyAuthEnabled false`, if Access covers the external path, or keep the app internal only.
3. For Home Assistant, add `http: use_x_forwarded_for: true` and `trusted_proxies` covering the `edge` bridge subnet. Restart it and check that `https://home-assistant.DOMAIN` (or your chosen subdomain) works on the LAN, including the companion app.
4. Set the companion app's server URL to `https://home-assistant.DOMAIN`. On the phone, set the UniFi WireGuard profile to connect on demand whenever the phone is off the home Wi-Fi, with the UCG as its DNS server. Check from mobile data that the app connects and that a notification arrives.
5. Set up Uptime Kuma monitors for `https://edge.DOMAIN/healthz` (internal route to `sync:9000`) and each app hostname.
6. Stop the old Umbrel "Cloudflare Tunnel" app, and delete its public hostnames once nothing depends on them.

## Acceptance
- A table in ARCHITECTURE.md lists every app with its internal result, external result and the auth decision.
- The Home Assistant logs show no "untrusted proxy" warnings, and the companion app connects on Wi-Fi at home.
- An Uptime Kuma alert fires within 2 minutes of stopping the `sync` container.
- The old tunnel shows zero connectors, or is deleted.

## Tests
None new. This is an operational task.

## Out of scope
Changing any app's own configuration beyond Home Assistant's `http:` block.
