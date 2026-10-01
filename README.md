# umbrel-edge

An Umbrel community app store with one app, Edge (`vyrmy-edge`). Every app on the Umbrel gets `https://<app>.bebitwise.dev`: at home through Traefik on `192.168.10.4`, away through a Cloudflare Tunnel behind Cloudflare Access.

The design, decisions and task list are in [docs/architecture](docs/architecture/ARCHITECTURE.md).

## Layout

| Path | What it is |
|---|---|
| `umbrel-app-store.yml` | Store id `vyrmy` |
| `vyrmy-edge/` | The Umbrel app: compose file, Traefik static config, example `edge.yaml` |
| `sync/` | The Python reconciler, built into `ghcr.io/vyrmy/umbrel-edge-sync` |
| `.github/workflows/build.yml` | Lint, type-check, test, then push the image on `main` |

## Development

```sh
cd sync
uv venv -p 3.12 && uv pip install -e '.[dev]'
ruff check . && ruff format --check . && mypy src tests && pytest -q
EDGE_CONFIG=../vyrmy-edge/edge.example.yaml EDGE_APP_DATA_ROOT=tests/fixtures/app-data \
  python -m umbrel_edge --dry-run
```

## Installing on the Umbrel

1. Push to `main` and wait for the build. Make the `umbrel-edge-sync` package public in GitHub (Packages → Package settings → Change visibility).
2. In Umbrel, App Store → ⋯ → Community App Stores, add `https://github.com/vyrmy/umbrel-edge`. A private repo needs a URL with a read-only token.
3. Install Edge, then create `data/secrets.env` (mode 0600) and `data/edge.yaml` in its data folder. The keys are listed under Secrets in the architecture doc.
