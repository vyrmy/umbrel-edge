---
id: 012
title: One private network per desktop, owned by Edge and joined by Traefik
status: todo
depends_on: []
---

## Goal
Edge creates `vyrmy-edge_desk-1` and `vyrmy-edge_desk-2`, private networks with no host address, and Traefik has a fixed address on each, so desktop slots can move onto them in task 014.

## Files
- `vyrmy-edge/docker-compose.yml` (existing): networks `desk-1`, `desk-2`; Traefik attached to both
- `scripts/new-desktop-slot` (existing): adds `desk-<n>` to Edge's compose and prints that Edge needs a release first
- `docs/architecture/ARCHITECTURE.md` (existing)

## Contract
Per slot `n`, in `vyrmy-edge/docker-compose.yml`:

```yaml
networks:
  desk-1:
    internal: true
    driver: bridge
    driver_opts:
      com.docker.network.bridge.inhibit_ipv4: "true"
    ipam:
      config:
        - subnet: 10.31.1.0/24
```

Traefik's `networks:` gains `desk-1: {ipv4_address: 10.31.1.2}`. Nothing else joins `desk-<n>` from Edge. Once shipped, a `desk-<n>` definition never changes: Compose cannot recreate a network that a running desktop is attached to.

## Steps
1. Add the networks and Traefik's addresses. `docker compose config` locally.
2. Locally (Docker 28 or later, as on the Umbrel), bring Edge's compose up with a bridge in place of the macvlan and confirm: the host has no address on the `desk-*` bridges; a container on `desk-1` reaches Traefik by name and address; it cannot reach the host, `umbrel_main_network`, `desk-2` or the internet.
3. Update `new-desktop-slot` so slot `n` adds `desk-<n>` with `10.31.<n>.0/24` and Traefik's `10.31.<n>.2`.
4. Release as an Edge minor version.

## Acceptance
- On the Umbrel after the update: `docker network inspect vyrmy-edge_desk-1` shows `Internal: true`, subnet `10.31.1.0/24`, Traefik at `10.31.1.2`, and `ip addr` on the host shows no `10.31.` address.
- Existing routes, the tunnel and the Traefik dashboard tile are unaffected.

## Tests
The local checks in step 2, recorded in this file's results section. `new-desktop-slot 3` output for Edge's compose.

## Out of scope
Moving the desktops (task 014).
