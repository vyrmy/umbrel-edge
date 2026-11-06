---
id: 016
title: Build the umbrel-claude image and pin it weekly
status: todo
depends_on: []
---

## Goal
`ghcr.io/vyrmy/umbrel-claude` builds every Monday with the newest Claude Code, runs `claude-serve` as uid 1000, and `scripts/pin-image` pins it, or the desktop image, into the apps that use it.

## Files
- `claude/Dockerfile` (new)
- `claude/root/usr/local/bin/claude-serve` (new, executable)
- `claude/root/etc/claude-code/managed-settings.json` (new)
- `scripts/pin-image` (new, replaces `scripts/pin-desktop-image`)
- `scripts/pin-desktop-image` (existing, removed)
- `.github/workflows/claude.yml` (new)
- `.github/workflows/desktop.yml` (existing): call `scripts/pin-image`
- `vyrmy-desktop-*/umbrel-app.yml` (existing): the comment naming `pin-desktop-image`
- `docs/architecture/ARCHITECTURE.md` (existing): mark the task's results

## Contract
From ARCHITECTURE.md, decisions 24, 25 and 27, and Contracts, "`claude-serve`" and "`scripts/pin-image`".

- Base `node:22-bookworm-slim`, pinned by digest. `apt-get install --no-install-recommends` for `git gh tmux ripgrep jq curl ca-certificates python3 python3-venv build-essential`; `uv` from its pinned release binary; `npm install -g @anthropic-ai/claude-code@<version>`, where the workflow passes the version `npm view @anthropic-ai/claude-code version` reports at build time as a build argument, and the image records it in the label `org.opencontainers.image.version`.
- User `claude`, uid and gid 1000, home `/home/claude` (empty in the image; the app mounts its volume there). `ENV DISABLE_AUTOUPDATER=1 USE_BUILTIN_RIPGREP=0`. `ENTRYPOINT ["/usr/local/bin/claude-serve"]`, `USER 1000:1000`.
- The workflow mirrors `desktop.yml`: build on pushes to `claude/**` or the workflow, on pull requests (load, no push), every Monday at 05:30 UTC and on demand; push `:main` and `:sha-<commit>`; a smoke test; on `main`, `scripts/pin-image --image ghcr.io/vyrmy/umbrel-claude --apps 'vyrmy-claude' <digest>` and commit the pin. Pinning skips when no `vyrmy-claude/` folder exists yet, so this task can land before task 017.

## Steps
1. Write `scripts/pin-image` from `pin-desktop-image`, with `--image` and `--apps`. Switch `desktop.yml` to it and delete the old script. Check on a copy of the repo that `scripts/pin-image --image ghcr.io/vyrmy/umbrel-desktop --apps 'vyrmy-desktop-*' sha256:<new>` gives the same diff the old script did.
2. Write the Dockerfile, `claude-serve` and the managed settings.
3. Build locally. Run the image with an empty volume on `/home/claude` and no credentials: tmux session `claude` exists, Remote Control waits at its sign-in prompt, and the container stays up.
4. Find out what `claude remote-control --continue` does with nothing to continue: in a fresh home, after a first sign-in if you can do one here, otherwise from the CLI's `--help` and docs. If it refuses to start, change `claude-serve` to add `--continue` only after a run that lasted more than a minute, and amend ARCHITECTURE.md's contract to match.
5. Add the workflow with its smoke test.

## Acceptance
- CI builds the image, and the smoke test passes:
  - `claude --version` matches the label;
  - `id -u` is `1000`;
  - `git`, `gh`, `tmux`, `rg`, `jq`, `python3` and `uv` run;
  - `/etc/claude-code/managed-settings.json` sets `disableBypassPermissionsMode` to `"disable"`;
  - `claude-serve` is executable.
- Locally, `docker run` with an empty home shows the sign-in prompt in `tmux capture-pane -pt claude`. Killing the `claude` process starts it again within about 10 seconds, and the container does not exit.
- `claude --dangerously-skip-permissions --version` or `claude --permission-mode bypassPermissions` inside the container is refused because of the managed setting (record the exact message).
- The desktop workflow, run on demand on this branch's PR, still builds. On a copy, `pin-image` changes the same lines in `vyrmy-desktop-*` that `pin-desktop-image` did.

## Tests
The smoke test in the workflow; the local runs above, recorded in this file.

## Out of scope
The app itself (task 017). Signing in to a real account in CI. Any web UI.
