---
id: 002
title: Sync service core, discovery and Traefik routes
status: in-progress
depends_on: [001]
---

## Goal
Installing an app on the Umbrel gives it a working `https://<app>.DOMAIN` route in Traefik within 60 seconds. You add its DNS record by hand for now.

## Files
- `sync/pyproject.toml` (new)
- `sync/src/umbrel_edge/{__main__,config,models,discovery,desired,traefik_writer,health,loop}.py` (new)
- `sync/tests/test_discovery.py`, `test_desired.py`, `test_traefik_writer.py` (new)
- `.github/workflows/build.yml` (new)
- `vyrmy-edge/docker-compose.yml` (existing): add the `sync` service
- `vyrmy-edge/edge.example.yaml` (existing)

## Contract
Copy `models.py`, the CLI table, the `/healthz` shapes and the Traefik dynamic file rules exactly from ARCHITECTURE.md.

## Steps
1. On the Umbrel, confirm that `~/umbrel/app-data/<id>/umbrel-app.yml` exists and carries `port`. If it does not, implement the `docker-compose.yml` `PORT` fallback and amend ARCHITECTURE.md first.
2. `discovery.py` reads the manifests read-only from `/umbrel/app-data`, skips unreadable ones with a warning, and never raises for a single bad app.
3. `desired.py` is a pure function `(list[AppManifest], EdgeConfig) -> DesiredState` applying the rules in Data model.
4. `traefik_writer.py` renders YAML, writes it to a temp file in the same directory, `fsync`s it and `os.replace`s it into place. It skips the write when the content is unchanged.
5. `loop.py` runs discovery, then desired state, then the Traefik write, every 60 seconds and when `edge.yaml` changes (poll mtime, no inotify dependency).
6. The CI builds the image, runs ruff, `mypy --strict` and pytest, and pushes to GHCR. The compose file pins the image by digest.

## Acceptance
- With `edge.yaml` empty apart from `domain`, `proxy_ip` and `access`, `--dry-run` lists every installed app except `mosquitto`, each with hostname `<id>.DOMAIN`.
- `apps.jellyfin.subdomain: tv` produces `tv.DOMAIN`. Two apps mapped to the same subdomain make the pass fail with a validation error that names both ids.
- `home-assistant` shows `external: false` unless `apps.home-assistant.external: true` is set.
- Installing Excalidraw from the Umbrel store makes `https://excalidraw.DOMAIN` load within 60 seconds, once you add the A record by hand. Uninstalling it removes the router within 60 seconds.
- `/healthz` returns 503 with stage `traefik` when the dynamic directory is read-only.

## Tests
Discovery on a fixture tree with one valid, one missing-port and one malformed manifest. Desired-state rules, including the deny list, overrides and collision. Writer atomicity: no partial file visible, and no write when unchanged.

## Out of scope
The UniFi and Cloudflare API calls. The `ownership.json` file.

## Acceptance results (5 October 2026, Edge 0.2.0)
- Passed: sync writes 70 routes (every installed app except `mosquitto`, plus `umbrel`). `umbrel`, `jellyfin`, `excalidraw`, `ittools`, `home-assistant` and `vyrmy-edge` all answer on `192.168.10.4` with the wildcard certificate.
- Passed by unit tests: subdomain override, collision naming both ids, the Home Assistant deny-list default, and `/healthz` 503 with stage `traefik` on a read-only directory.
- Not yet passing, task 006: apps with Umbrel app auth on (Excalidraw, IT-Tools and others) redirect to `https://<app>.bebitwise.dev:2000/app-auth`, which nothing serves (umbrel#2242). Home Assistant returns 400 until its `trusted_proxies` is set.
- Pending: install and uninstall Excalidraw to time the route appearing and going.
- Root cause of the first deployment's missing routes: Docker created `data/` as root, so sync (uid 1000) could not create `data/traefik`. The `init` service now fixes ownership on every start.
