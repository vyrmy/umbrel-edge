---
id: 007
title: Umbrel dashboard opens apps at their own hostnames
status: todo
depends_on: [002]
---

## Goal
Clicking an app on the Umbrel dashboard at `https://umbrel.DOMAIN` opens `https://<app>.DOMAIN`, at home and through the tunnel. Nothing opens at `umbrel.DOMAIN:<port>` or `umbrel.home.arpa:<port>`.

## Files
- `sync/src/umbrel_edge/launcher.py` (new)
- `sync/src/umbrel_edge/health.py` (existing): serve `GET /__edge/launcher.js` next to `/healthz`
- `sync/src/umbrel_edge/traefik_writer.py` (existing): the `umbrel` route gets the body-rewrite middleware and a `PathPrefix(/__edge/)` router to `sync:9000`
- `vyrmy-edge/traefik/traefik.yml` (existing): enable the body-rewrite plugin, pinned by version
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
