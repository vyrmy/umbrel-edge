---
id: 013
title: Desktop image sends home hostnames to Traefik and everything else through the egress proxy
status: done
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

`/etc/chromium.d/90-egress` (sourced by the Chromium package's launcher, `/usr/bin/chromium-browser`, which every launch path reaches):

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

`/custom-cont-init.d/10-egress-policy` writes `/etc/chromium/policies/managed/egress.json` with `ProxySettings` `{ProxyMode: fixed_servers, ProxyServer: $EGRESS_PROXY, ProxyBypassList: "<domain>;*.<domain>"}` when `EGRESS_PROXY` is set, and removes the file otherwise. It removes any earlier file first, so a refused value leaves no policy.

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

## Results
CI on PR #7 built the image with the guard and passed the smoke test, Signal included.

Local run on `linuxserver/webtop:ubuntu-xfce` (Ubuntu 26.04, xtradeb Chromium 156), with Signal replaced by a stub because its apt repository was unreachable from the test sandbox. Squid 6.13 used the Contracts config with `acl clients src 10.31.1.10/32`; an internal `inhibit_ipv4` network held the desktop at .10, a stand-in Traefik at .2 and Squid at .3.

- Without `EGRESS_PROXY`: the Chromium main process carries none of the three flags, no policy directory exists, and the baked `10-egress-policy` ran and exited 0.
- With it: one main process carries all three flags, and the WhatsApp window shares its PID. `egress.json` is written and chrome://policy shows ProxySettings as mandatory, status OK.
- `https://app.bebitwise.dev/` reached the stand-in Traefik from 10.31.1.10, with no Squid log line and no DNS query. Public sites reached their server from Squid's address, by IP and by name.
- `http://192.168.1.1/`, `http://10.21.0.1/` and a name resolving to 192.168.1.1 got Squid's Access Denied page. Port 81, `CONNECT 10.0.0.1`, decimal IP and `[::1]` were refused too.
- A logging resolver given to the desktop saw zero queries across a full session and six URLs.
- Squid's stdout held only `TCP_DENIED/403` lines; 200s and a 503 were not logged.
- `wrapped-signal` adds `--proxy-server` in all three exec variants and exports the four proxy variables; without `EGRESS_PROXY` it sets none of them.
- A value with `"}`, a quote in the domain or a newline is refused with exit 1 and no file.
