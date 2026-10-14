---
id: 008
title: Personal desktops, and Authentik reachable from outside
status: in-progress
depends_on: [005, 006]
---

## Goal
Each person in the household has their own desktop, Desktop 1 and Desktop 2 to start with, where a browser and Signal stay signed in. They open it at `https://desk-<name>.DOMAIN` from anywhere, and nobody else can. `auth.DOMAIN` works from outside, so every Authentik login does too.

## Files
- `sync/src/umbrel_edge/models.py`, `desired.py` (existing): `apps.<id>.upstream_host` (decision 15)
- `sync/tests/test_direct_upstream.py` (new)
- `desktop/` (new): the `ghcr.io/vyrmy/umbrel-desktop` image
- `vyrmy-desktop-1/`, `vyrmy-desktop-2/` (new): the Umbrel apps
- `scripts/new-desktop-slot`, `scripts/pin-desktop-image` (new)
- `.github/workflows/desktop.yml` (new): weekly build, then pin
- `vyrmy-edge/edge.example.yaml`, `vyrmy-edge/umbrel-app.yml` (existing)

## Contract
Decisions 15 and 16 in ARCHITECTURE.md. Slot `n` is app id `vyrmy-desktop-<n>`, Umbrel port `18100 + n`, container `vyrmy-desktop-<n>_desktop_1` serving Selkies on port 3000. Each slot's Umbrel login stays on.

## Steps
Steps 1 and 2 are in this branch. The rest are the owner's, in this order.

1. `upstream_host` in sync, tests first. Release as Edge 0.4.0: push, wait for the `build` workflow, then pin the new `umbrel-edge-sync` digest and bump `version` in a second push.
2. The desktop image, slots 1 and 2, the slot and pin scripts and the `desktop` workflow. The first push to `main` builds the image and the workflow commits its digest; install nothing until that commit is there, because until then the compose files hold a placeholder digest.
3. **Publish the image.** GitHub, Packages, `umbrel-desktop`, Package settings: make it public, as for `umbrel-edge-sync`. Then run the `desktop` workflow once by hand (Actions, desktop, Run workflow), so its smoke test pulls the image anonymously.
4. **Harden Authentik before it goes public.** In Authentik's admin interface:
   - Flows and Stages, Stages, `default-authentication-mfa-validation`: device classes TOTP and WebAuthn, "Not configured action" set to "Force the user to configure an authenticator", with the TOTP and WebAuthn setup stages picked under "Configuration stages". Everyone then has MFA from their next login.
   - Customization, Policies, create an Expression policy `superuser-via-tunnel`:
     ```python
     user = request.context.get("pending_user")
     return bool(user and user.is_superuser
                 and "HTTP_CF_CONNECTING_IP" in request.http_request.META)
     ```
     Then Stages, create a Deny stage `deny-superuser-via-tunnel`, and in Flows, `default-authentication-flow`, Stage Bindings, bind it at an order after the identification stage and before the user login stage, with "Evaluate when stage is run" ticked and `superuser-via-tunnel` bound to that stage binding. The Deny stage then runs only for a superuser coming through Cloudflare. Do not bind the policy to the password stage instead: a failed policy skips a stage rather than stopping the flow, so that would let a superuser in without a password.
5. **Rate-limit the login in Cloudflare.** Security, WAF (Security rules in the newer dashboard), Rate limiting rules, one rule (the free plan allows one): when hostname equals `auth.bebitwise.dev` and URI path contains `/api/v3/flows/executor/` and method is `POST`, with the same IP, more than 10 requests in 10 seconds, block for 10 seconds.
6. **Open Authentik to the outside.** In `edge.yaml`: `denny-authentik: {subdomain: auth, external: true, access: false}`. Works on Edge 0.3.2.
7. **Update Edge to 0.4.0** in the Umbrel app store. Earlier versions reject `upstream_host` as an unknown key and stop applying any change to `edge.yaml`.
8. **Install Desktop 1 and Desktop 2** from the Vyrmy store. Settings, Users: share each with its one person, and leave "Require Umbrel login" on.
9. **Route them.** In `edge.yaml`, under `apps` (pick each hostname; `forward_auth` comes on by itself):
   ```yaml
   vyrmy-desktop-1:
     subdomain: desk-<name>
     external: true
     upstream_host: vyrmy-desktop-1_desktop_1
     upstream_port: 3000
   vyrmy-desktop-2:
     subdomain: desk-<name>
     external: true
     upstream_host: vyrmy-desktop-2_desktop_1
     upstream_port: 3000
   ```
   Add each person's email to `access.allowed_emails`, or Cloudflare Access turns them away outside.
10. **One Authentik application per desktop.** Applications, Providers, create a Proxy provider: "Forward auth (single application)", external host `https://desk-<name>.bebitwise.dev`. Applications, create an application with that provider and launch URL, then under Policy / Group / User Bindings bind that one user and nothing else. Outposts, the embedded outpost: add the application.
11. **Link Signal.** Each person opens their desktop, then Signal, and scans the code from Signal on their phone (Settings, Linked devices).

`scripts/new-desktop-slot <n>` adds Desktop 3 onwards and prints steps 8 to 10 for it.

## Acceptance
- `https://auth.bebitwise.dev` loads from a phone on mobile data with no Access prompt and asks for MFA. A superuser signing in that way is refused at the Deny stage; the same account signs in at home.
- More than 10 rapid login POSTs from one IP get Cloudflare's block page.
- From mobile data, `https://desk-<a>.bebitwise.dev` asks for the Access PIN, then Authentik, and shows Desktop 1 with Chromium and Signal open. Person B's Authentik account gets "Permission denied" there.
- At home, `https://desk-<a>.bebitwise.dev` asks only for Authentik. `https://192.168.10.2:18101` asks for Umbrel's login and refuses person B's Umbrel account ("This app has not been shared with this account").
- A desktop left idle for 5 minutes is still connected (rules out Traefik's 60-second read timeout).
- Restarting the app keeps every sign-in: Gmail or Outlook, Google Calendar, WhatsApp Web and Signal.
- `chrome://sandbox` in Chromium shows the namespace sandbox in use. If Chromium does not start at all, remove `security_opt` from the compose file and report it here: the kernel refuses unprivileged user namespaces.
- The Monday after release, the `desktop` workflow commits a new pin and Umbrel offers an update for both desktops.

## Tests
`upstream_host` URL building, the `upstream_port` requirement and the container-name pattern; forward auth defaulting on, explicit off, `forward_auth_deny` winning, and the config error without a `forward_auth` block; `build` failing closed; the launcher mapping the manifest port.

## Out of scope
Moving Obsidian and LibreWolf to per-person slots. Closing app ports at the UniFi firewall (LibreWolf publishes 3001 with no login, see app-auth-audit.md). Using Authentik as Cloudflare Access's identity provider, which would bring the two logins down to one.

## Implementation notes (13 October 2026)
- umbrelOS 2.0 facts checked in `getumbrel/umbrel` master: every app's default network is `umbrel_main_network` (`legacy-compat/docker-compose.common.yml`); the app gateway passes no user identity to apps (`app-gateway/app-gateway.ts`); `requiresHttps: true` makes the dashboard open the app port over HTTPS with Umbrel's own TLS (`ui/src/hooks/use-launch-app.ts`), which Selkies needs for its secure-context browser APIs.
- Ports 18101 and 18102 are unused in the official store and in Denny's store.
- Webtop's Ubuntu XFCE image already ships Chromium (`wrapped-chromium`) and its sandbox switch; `wrapped-signal` copies it.
- Traefik's default 60-second `readTimeout` should not cut a websocket: on hijack, Go's server clears the read deadline it set while a background read was pending, which is the case for a body-less upgrade request. The idle check in Acceptance confirms it.
- GitHub turns off scheduled workflows in a public repo after 60 days without activity. The weekly pin commit should count as activity, but if the desktops stop being offered updates, check the `desktop` workflow under Actions first.
- Local checks: ruff, mypy and pytest pass, except `test_server_serves_script_uncached`, which needs to bind a local port and is blocked by the sandbox here; CI runs it. A local `docker build` of `desktop/` could not finish (Docker Desktop stopped during the base image pull), so the image is first built by the `desktop` workflow, whose smoke test checks Chromium, Signal and the wrappers.

## Release results (14 October 2026)
- Steps 1 to 3 done. The `desktop` workflow built the first image, its smoke test passed (Chromium, Signal, both wrappers, the autostart entries) and it pinned `umbrel-desktop@sha256:cb791d42…` in both slots at version `2026.10.13`. `umbrel-desktop` and `umbrel-edge-sync` both pull anonymously from GHCR, so no visibility change was needed.
- Edge 0.4.0 pins `umbrel-edge-sync@sha256:9fce05df…`, built from the commit that adds `upstream_host`.
