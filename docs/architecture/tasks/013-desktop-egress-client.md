---
id: 013
title: Desktop image sends home hostnames to Traefik and everything else through the egress proxy
status: todo
depends_on: []
---

## Goal
When a slot sets `EGRESS_PROXY`, Chromium (every window, the WhatsApp one included) and Signal reach the internet only through that proxy and reach `*.DOMAIN` only at Traefik's address, with no DNS of their own. Without `EGRESS_PROXY` the image behaves exactly as it does today.

## Files
- `desktop/root/etc/chromium.d/90-egress` (new): the Chromium flags
- `desktop/root/custom-cont-init.d/10-egress-policy` (new, executable): the managed proxy policy
- `desktop/root/usr/local/bin/wrapped-signal` (existing): proxy for Signal
- `desktop/Dockerfile` (existing): build guard on the Chromium launcher
- `.github/workflows/desktop.yml` (existing): smoke test for the new files
- `docs/architecture/ARCHITECTURE.md` (existing)

## Contract
Environment, set by the slot compose (task 014): `EGRESS_PROXY=http://10.31.<n>.3:3128`, `EGRESS_DIRECT_DOMAIN=<DOMAIN>`, `EGRESS_DIRECT_HOST=10.31.<n>.2`.

`/etc/chromium.d/90-egress` (sourced by Debian's `/usr/bin/chromium-browser`, which every launch path reaches):

```sh
if [ -n "$EGRESS_PROXY" ]; then
  set -- "$@" "--proxy-server=$EGRESS_PROXY"
  if [ -n "$EGRESS_DIRECT_DOMAIN" ] && [ -n "$EGRESS_DIRECT_HOST" ]; then
    set -- "$@" \
      "--proxy-bypass-list=$EGRESS_DIRECT_DOMAIN;*.$EGRESS_DIRECT_DOMAIN" \
      "--host-resolver-rules=MAP *.$EGRESS_DIRECT_DOMAIN $EGRESS_DIRECT_HOST, MAP $EGRESS_DIRECT_DOMAIN $EGRESS_DIRECT_HOST"
  fi
fi
```

`/custom-cont-init.d/10-egress-policy` writes `/etc/chromium/policies/managed/egress.json` with `ProxySettings` `{ProxyMode: fixed_servers, ProxyServer: $EGRESS_PROXY, ProxyBypassList: "<domain>;*.<domain>"}` when `EGRESS_PROXY` is set, and removes the file otherwise.

`wrapped-signal`: when `EGRESS_PROXY` is set, export `HTTPS_PROXY`, `https_proxy`, `HTTP_PROXY` and `http_proxy` to it and add `--proxy-server=$EGRESS_PROXY` to both exec lines. Signal's own code reads only `HTTPS_PROXY`/`https_proxy`.

Dockerfile guard: `RUN grep -q '^for file in /etc/chromium.d/' /usr/bin/chromium-browser`, so a launcher change fails the weekly build instead of silently dropping the proxy.

## Steps
1. Add the files; mark `10-egress-policy` executable in git.
2. Locally on the Webtop base: with `EGRESS_PROXY` unset, nothing changes (no flags in `ps`, no policy file). With it set, against a stand-in proxy and a stand-in Traefik: one Chromium main process carries all three flags, the WhatsApp window opened by `wrapped-whatsapp --after-browser` joins it, `*.DOMAIN` reaches the stand-in Traefik directly, other sites appear in the proxy log, and the desktop makes no DNS lookups at all (the proxy and Traefik are IP literals).
3. Extend the smoke test.

## Acceptance
- CI builds the image with the guard and the smoke test passes.
- The local checks in step 2 pass and are recorded here.

## Tests
The smoke test greps for the three files and the guard; the local run above.

## Out of scope
The compose changes that set the variables (task 014). Calls (decision 18: not supported).
