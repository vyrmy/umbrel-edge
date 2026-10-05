---
id: 007
title: Umbrel dashboard opens apps at their own hostnames
status: in-progress
depends_on: [002]
---

## Goal
Clicking an app on the Umbrel dashboard at `https://umbrel.DOMAIN` opens `https://<app>.DOMAIN`, at home and through the tunnel. Nothing opens at `umbrel.DOMAIN:<port>` or `umbrel.home.arpa:<port>`.

## Files
- `sync/src/umbrel_edge/launcher.py` (new)
- `sync/src/umbrel_edge/health.py` (existing): serve `GET /__edge/launcher.js` next to `/healthz`
- `sync/src/umbrel_edge/traefik_writer.py` (existing): the `umbrel` route gets the body-rewrite middleware and a `PathPrefix(/__edge/)` router to `sync:9000`
- `vyrmy-edge/docker-compose.yml` (existing): enable the body-rewrite plugin in Traefik's command flags, pinned by version
- `sync/tests/test_launcher.py` (new)

## Contract
Decision 13 in ARCHITECTURE.md. `launcher.js` holds a map `{ "<port>": "<hostname>" }` for every route with `internal` or `external` true, and nothing else. It is served with `Cache-Control: no-cache` so a new app shows up without a hard refresh.

## Steps
1. Before writing code, load the dashboard at `umbrel.DOMAIN` and record three things: the exact URL the dashboard opens for two apps (link `href` or `window.open`), the `Content-Security-Policy` header, and whether the HTML comes back compressed. Amend decision 13 if any of them rules the approach out.
2. `launcher.py` renders the script from `DesiredState`. The script wraps `window.open` and adds one capture-phase click listener on `document`. Any URL whose host is `location.hostname` and whose port is in the map is rewritten to `https://<hostname><path><query>`. Every other URL passes through untouched.
3. Add the middleware so it inserts the script tag before `</head>` on `text/html` responses from the `umbrel` route only.
4. Ask Traefik for uncompressed responses from the dashboard upstream if step 1 found compression, so the rewrite sees plain HTML.

## Acceptance
- From Main, clicking Home Assistant on `https://umbrel.DOMAIN` opens `https://home-assistant.DOMAIN` with a valid certificate.
- From a phone on mobile data, clicking Excalidraw on the dashboard opens `https://excalidraw.DOMAIN` behind Access, with no second PIN prompt.
- Installing a new app makes its dashboard icon open the new hostname within 60 seconds, without clearing the browser cache.
- Other pages proxied by Traefik have no injected script.

## Tests
Script rendering, including an app with a custom subdomain and an app on the external deny list (still internal, so still mapped). A golden-file test of the generated Traefik middleware.

## Out of scope
Changing the dashboard's look or the order of apps. Opening apps from `umbrel.home.arpa`, which keeps Umbrel's own behaviour.

## Step 1 findings

Recorded on 5 October 2026 with read-only GETs against `http://192.168.10.2/` (Traefik was not in place yet), plus a read of the public JS bundles it references.

- **Link format.** The dashboard opens an app with `window.open(url, "_blank")`, where `url` comes from one function: if the app's URL starts with `umbrel:`, it returns `` `${location.protocol}//${location.hostname}:${port}` ``. So the link is `<current protocol>//<current host>:<port>`, with no path. The app launcher, the command palette and the app icon all go through it. A second `window.open` in the settings code opens `http://<ip>/confirm-static-ip` and is not affected (no mapped port).
- **Content-Security-Policy.** `default-src 'self'` with no separate `script-src`, and `script-src-attr 'none'`. A same-origin `<script src="/__edge/launcher.js">` is allowed. An inline script would not be, which is why the tag points at a file served on the same route.
- **Compression.** The HTML comes back uncompressed even with `Accept-Encoding: gzip, deflate, br, zstd`, so step 4 is not needed. The plugin also handles gzip if that ever changes, but not brotli or zstd.
- **Other.** `/__edge/launcher.js` on the dashboard upstream returns the SPA shell (HTTP 200), so the `PathPrefix(/__edge/)` router must outrank the umbrel router. It has `priority: 1000`.
- **Verdict.** Decision 13 is not ruled out and is unchanged.

## Implementation notes

- Plugin: `github.com/packruler/rewrite-body` v1.2.0, pinned in the traefik service's `--experimental.plugins` flags in `vyrmy-edge/docker-compose.yml`. It is a fork of Traefik's own rewrite-body plugin with gzip support. The last release is from November 2022, but the last commit to the repo is from May 2024 and it is the only maintained body-rewrite plugin for Traefik that I found. Traefik downloads it from GitHub at start, so the Traefik container needs internet access then. If the download or load fails, Traefik logs "Plugins are disabled" and starts anyway (`--experimental.abortonpluginfailure=false`). Only the umbrel router is dropped, and sync's priority-1 fallback router `edge-umbrel-fallback` serves the dashboard without the launcher. Traefik v3.7.13 is already pinned in the compose file.
- The rewrite is a single regex, `</head>` to the script tag plus `</head>`, on `text/html` GET responses of the umbrel route only. `lastModified: true` keeps the upstream header.
- `sync` serves `/__edge/launcher.js` from its health server (`:9000`) with `Cache-Control: no-cache`. The script is regenerated every pass and swapped in memory, so it is also current for `--dry-run` output. Traefik reaches it at `http://vyrmy-edge_sync_1:9000`.
- Only the ports of the manifests are mapped, which is what the dashboard uses. A custom `upstream_port` in `edge.yaml` does not change the map.
- Traefik's static config, plugin included, moved from `traefik/traefik.yml` into compose command flags, because an app update does not copy the `traefik/` folder. Updating the app delivers it.
- The golden file is `sync/tests/fixtures/golden/umbrel-route.yml`. Regenerate it by hand if the middleware changes on purpose.

## Acceptance results (5 October 2026, Edge 0.2.0)
- Passed: the plugin loads ("Plugins loaded" in Traefik's log). A request to `umbrel.bebitwise.dev` with `Accept: text/html` gets the `<script src="/__edge/launcher.js">` tag, `/__edge/launcher.js` is served with `Cache-Control: no-cache`, and `jellyfin.bebitwise.dev` gets no injected script.
- Pending: clicking apps on the dashboard in a browser, at home and through the tunnel.
