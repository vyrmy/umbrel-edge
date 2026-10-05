---
id: 001
title: Community app store and Traefik on its own LAN address
status: in-progress
depends_on: []
---

## Goal
Traefik runs as an Umbrel app at `192.168.10.4`, holds a valid `*.DOMAIN` certificate, and serves one hand-written route from the Main network.

## Files
- `umbrel-app-store.yml` (new)
- `vyrmy-edge/umbrel-app.yml` (new)
- `vyrmy-edge/docker-compose.yml` (new): the `traefik` service only for now
- `vyrmy-edge/traefik/traefik.yml` (new; later replaced by command flags in `docker-compose.yml`, see Risks in ARCHITECTURE.md)
- `vyrmy-edge/edge.example.yaml` (new)

## Contract
- Networks: `lan` is a macvlan with parent = the Umbrel's NIC, subnet `192.168.10.0/24` and gateway `192.168.10.1`. `edge` is a bridge.
- `traefik` is on `lan` with `ipv4_address: 192.168.10.4` and a fixed `mac_address`. It is also on `edge`, with `extra_hosts: ["host.docker.internal:host-gateway"]`.
- Entrypoints: `web` `:80` redirecting to `websecure` `:443`.
- The certResolver is `cloudflare`, using the DNS-01 challenge with provider `cloudflare` and `CF_DNS_API_TOKEN` from `secrets.env`. Storage is `/data/acme.json`.
- The file provider watches `/data/traefik/dynamic/`.

## Steps
1. On the Umbrel over SSH, record `umbreld --version` (or the Settings page) and the NIC name (`ip -br link`). Write both into Risks in ARCHITECTURE.md.
2. Create the repo and store files, and add the store in Umbrel (App Store → ⋯ → Community App Stores).
3. Create `secrets.env` in the app's data directory by hand, mode 0600.
4. In UniFi, add these three things:
   - A fixed IP reservation `192.168.10.4` for the pinned MAC.
   - A firewall allow from Main to `192.168.10.4` on TCP 80 and 443.
   - A firewall allow from VPN to `192.168.10.4` on TCP 80 and 443.
5. Add a static file `dynamic/umbrel.yml` routing `umbrel.DOMAIN` to `http://host.docker.internal:80`, plus a UniFi A record by hand.

## Acceptance
- `curl -vk https://192.168.10.4 -H 'Host: umbrel.DOMAIN'` from the Mac returns the Umbrel login page with a certificate issued to `*.DOMAIN` by Let's Encrypt.
- `https://umbrel.DOMAIN` opens in Chrome on the Mac with no certificate warning.
- From a device on IoT, a TCP connection to `192.168.10.4:443` times out.
- After the Dell restarts, Traefik comes back at `.4` with the same certificate. It is not reissued.

## Tests
Manual checks above. There is no code in this task.

## Out of scope
The sync service, cloudflared, any Cloudflare DNS records, and the existing Umbrel Cloudflare Tunnel app.
