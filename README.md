# umbrel-edge

An Umbrel community app store with Edge (`vyrmy-edge`) and per-person desktops (`vyrmy-desktop-<n>`). With Edge, every app on the Umbrel gets `https://<app>.bebitwise.dev`: at home through Traefik on `192.168.10.4`, away through a Cloudflare Tunnel behind Cloudflare Access.

The design, decisions and task list are in [docs/architecture](docs/architecture/ARCHITECTURE.md).

## Layout

| Path | What it is |
|---|---|
| `umbrel-app-store.yml` | Store id `vyrmy` |
| `vyrmy-edge/` | The Umbrel app: compose file (Traefik static config is its command flags), example `edge.yaml` |
| `sync/` | The Python reconciler, built into `ghcr.io/vyrmy/umbrel-edge-sync` |
| `vyrmy-desktop-<n>/` | Desktop 1, Desktop 2: one person's Chromium, Signal and WhatsApp each, kept signed in |
| `desktop/` | The desktop image, `ghcr.io/vyrmy/umbrel-desktop`: linuxserver Webtop plus Signal Desktop and a WhatsApp Web window |
| `scripts/` | `new-desktop-slot <n>` adds a desktop; `pin-desktop-image` is run by CI |
| `.github/workflows/build.yml` | Lint, type-check, test, then push the image on `main` |
| `.github/workflows/desktop.yml` | Build the desktop image every Monday and pin it in every slot |

## Development

```sh
cd sync
uv venv -p 3.12 && uv pip install -e '.[dev]'
ruff check . && ruff format --check . && mypy src tests && pytest -q
EDGE_CONFIG=../vyrmy-edge/edge.example.yaml EDGE_APP_DATA_ROOT=tests/fixtures/app-data \
  python -m umbrel_edge --dry-run
```

## Installing on the Umbrel

1. Push to `main` and wait for the build. Check the `umbrel-edge-sync` package is public in GitHub (Packages → Package settings → Change visibility).
2. In Umbrel, App Store → ⋯ → Community App Stores, add `https://github.com/vyrmy/umbrel-edge`.
3. Install Edge, then create `data/secrets.env` (mode 0600) and `data/edge.yaml` in its data folder. The keys are listed under Secrets in the architecture doc.
