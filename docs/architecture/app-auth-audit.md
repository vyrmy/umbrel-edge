# Umbrel app auth audit

Recorded 5 October 2026 for task 006. For each installed app: whether it has a login of its own once Umbrel's app-proxy login is turned off, what an unauthenticated visitor could do (risk), and what SSO it supports for the Authentik work. Sources were the Umbrel app store compose files and manifests (getumbrel/umbrel-apps and dennysubke/dennys-umbrel-app-store) and upstream docs. Ten first-pass claims were corrected by a second review. Nothing was tested against the live apps, so "First visitor claims admin" means the app is only safe once its setup is finished, and published default passwords count as no protection until changed.

"Umbrel auth" is the state after 5 October: *off* where it was turned off for task 006, otherwise unchanged. How each app can sign in through Authentik is in [sso-support.md](sso-support.md). Remember that the "IoT to Umbrel" firewall rule lets IoT reach every port on `192.168.10.2`, so an app with no login of its own is open to IoT devices as well as Main and the VPN.

| App | Own login | Risk without Umbrel auth | SSO | Umbrel auth | Note |
|---|---|---|---|---|---|
| `arcane` | Yes | high | OIDC | unchanged | The high rating stands, but the claim that a login protects it does not hold until the password is changed. |
| `bazarr` | Optional, off | high | Forward auth only | unchanged | No auth in the image or compose; Bazarr auth (basic/form) is off unless set in Settings. |
| `denny-actualbudget` | First visitor claims admin | high | OIDC | unchanged | Compose already sets PROXY_AUTH_ADD false, so Umbrel auth is not in front today. |
| `denny-gitea-mirror` | First visitor claims admin | high | OIDC | unchanged | First user signup becomes admin (upstream README), so an unconfigured instance can be claimed by the first LAN or IoT visitor. |
| `denny-hoarder` | First visitor claims admin | high | OIDC | unchanged | Hoarder's login can be bypassed by anyone who can reach port 3888. |
| `denny-librewolf` | Optional, off | high | Forward auth only | unchanged | linuxserver/librewolf has no authentication unless CUSTOM_USER/PASSWORD is set, and it is not set. |
| `esphome` | Optional, off | high | Forward auth only | unchanged | ESPHome dashboard has optional USERNAME/PASSWORD that are not set in the Umbrel compose, so it has no login. |
| `firefly-iii` | First visitor claims admin | high | Header | unchanged | Personal finance data. Until the first user registers, anyone can register and become owner (single-user mode default, not re-verified). |
| `gitea-mirror` | First visitor claims admin | high | OIDC | unchanged | README states the first user to sign up becomes admin, so an unclaimed instance is taken by whoever arrives first. |
| `homebridge` | First visitor claims admin | high | None | unchanged | Important: the Umbrel compose has no app_proxy service and uses network_mode: host, so the UI on port 8581 is directly reachable on the LAN and IoT VLAN regardless of Umbrel app auth. |
| `lidarr` | Unknown | high | Forward auth only | unchanged | I could not read the getumbrel/media-app-configurator source, so I cannot confirm whether it sets Lidarr's AuthenticationMethod to External (which would mean no login of its own and Umbrel's proxy was the only gate; the PROXY_AUTH_WHITELIST |
| `lobe-chat` | Yes | high | OIDC | unchanged | The claim that per-user data stays private is wrong for uploaded files. |
| `matter-server` | None | high | None | unchanged | Compose has no app_proxy at all: host-network container exposing the Matter websocket API on port 5580 with no authentication, so Umbrel auth does not apply and the IoT VLAN firewall rule reaches it. |
| `mealie` | Yes | high | OIDC | unchanged | The login screen is real, but the admin account behind it can use credentials that are public and identical on every install. |
| `n8n` | First visitor claims admin | high | None | unchanged | n8n shows an owner-account setup form on first visit, so whoever arrives first becomes owner (not verified whether the owner already did this). |
| `nginx-proxy-manager` | First visitor claims admin | high | Unknown | unchanged | Controls the proxy and TLS certificates. |
| `obsidian` | Optional, off | high | Forward auth only | unchanged | linuxserver/obsidian has no authentication unless CUSTOM_USER/PASSWORD is set; it is not set. |
| `openthread-border-router` | None | high | None | unchanged | Host-network containers expose a setup wizard (7586), OTBR web UI (7587) and REST API (8083) with no authentication, directly on the host and not only via the app proxy. |
| `portainer` | Yes | high | OIDC | unchanged | PROXY_AUTH_ADD is already false, so Umbrel auth is not in front of Portainer today. |
| `radarr` | None | high | Forward auth only | unchanged | Compose forces RADARR__AUTH__METHOD=External, which disables Radarr's own login and trusts whatever is in front. |
| `readarr` | Unknown | high | Forward auth only | unchanged | Same situation as Lidarr: uses the closed-source media-app-configurator and PROXY_AUTH_WHITELIST /api/*, so I cannot confirm whether Readarr has its own login or is set to External auth. |
| `sonarr` | None | high | Forward auth only | unchanged | Same pattern as Radarr: Umbrel's compose sets External auth, so Sonarr has no login of its own and relies on the proxy. |
| `tailscale` | None | high | None | unchanged | The claim is wrong. `tailscale web --listen 0.0.0.0:8240` without --readonly runs in LoginServerMode, not the check-mode "manage" client that KB 1325 describes. |
| `termix` | First visitor claims admin | high | OIDC | unchanged | Termix is a web SSH, RDP and VNC manager that stores credentials for the owner's hosts. |
| `transmission` | Optional, off | high | Forward auth only | unchanged | linuxserver/transmission only enables a login if USER and PASS are set; they are not. |
| `zigbee2mqtt` | Optional, off | high | Forward auth only | unchanged | The frontend auth_token is optional and the Umbrel compose does not set one (only the frontend is enabled on 8080), so the UI has no login. |
| `adguard-home` | First visitor claims admin | medium | Forward auth only | unchanged | Fresh install has no config, so the first visitor to the install wizard sets the admin account. |
| `affine` | First visitor claims admin | medium | OIDC | unchanged | AFFiNE has its own accounts. A fresh instance lets the first visitor to /admin create the admin account, and sign-up is open by default (I did not confirm this against the self-host config, so treat it as unverified), so a LAN, VPN or IoT v |
| `booklore` | First visitor claims admin | medium | OIDC | unchanged | Booklore has its own users. The first visitor creates the admin account in the setup screen. |
| `code-server` | Yes | medium | Forward auth only | off | Own password login is on: PASSWORD=$APP_PASSWORD, deterministic per Umbrel seed, so the Umbrel proxy was a second gate and not the only one. |
| `convertx` | First visitor claims admin | medium | None | unchanged | The login exists but does not protect anything here, because the Umbrel compose sets ACCOUNT_REGISTRATION=true (upstream default is false). |
| `denny-changedetection` | Optional, off | medium | None | unchanged | No login out of the box (optional password in Settings; compose sets none). |
| `denny-kitchenowl` | First visitor claims admin | medium | OIDC | unchanged | Own login with registration; first registered user becomes admin (from memory, not re-read). |
| `denny-manyfold` | First visitor claims admin | medium | OIDC | unchanged | Without MULTIUSER=enabled, which the compose does not set, Manyfold runs in single-user mode: authenticate_user! applies to every request, so login is required. |
| `denny-olivetin` | Optional, off | medium | OIDC | unchanged | The community store's compose has no auth settings and the shipped config.yaml has no authentication section, so there is no login. |
| `downtify` | Yes | medium | None | unchanged | The login is required (DOWNTIFY_DISABLE_AUTH defaults to false and the Umbrel compose does not set it), but it is only a real barrier if admin/downtify was changed. |
| `gitea` | First visitor claims admin | medium | OIDC | unchanged | PROXY_AUTH_ADD is already false, so Umbrel auth is not in front of Gitea today. |
| `gitlab` | Yes | medium | OIDC | unchanged | Compose already sets PROXY_AUTH_ADD false; GitLab has its own login and root password comes from the deterministic APP_PASSWORD. |
| `glasshome` | Unknown | medium | None | unchanged | The protections the claim relies on come from 1.4.0-beta.7 (2026-10-03), but Umbrel ships 1.3.1. |
| `golinks-go` | Unknown | medium | Unknown | unchanged | I could not locate this app. It is not in getumbrel/umbrel-apps, not in dennysubke/dennys-umbrel-app-store, and GitHub repo search for golinks-go or an Umbrel golinks package found nothing, nor does the owner's umbrel-edge worktree mention  |
| `grafana` | Yes | medium | OIDC | unchanged | The 'forced change prompt on first login' is not forced. |
| `home-assistant` | First visitor claims admin | medium | None | unchanged | First run is the HA onboarding wizard where whoever arrives first creates the owner; after onboarding, login is always required. |
| `homehub` | Optional, off | medium | None | unchanged | README: site-wide password is blank by default (passwordless) and the admin user can be picked from a user switcher without a password unless set via CLI. |
| `jellyfin` | First visitor claims admin | medium | None | unchanged | Jellyfin has its own accounts. A first-run wizard lets the first visitor create the admin if setup was not completed. |
| `metube` | None | medium | Forward auth only | unchanged | MeTube has no authentication at all. A visitor can queue yt-dlp downloads of any URL from the Umbrel (this can fetch internal LAN URLs and then serve the file back, a limited SSRF), fill the disk, and delete finished downloads. |
| `ollama` | None | medium | None | unchanged | Ollama has no authentication at all. Umbrel auth is already off for it (PROXY_AUTH_ADD false), so the API on its port is already open to anything that can reach it. |
| `overseerr` | First visitor claims admin | medium | None | unchanged | The login is only 'required' once setup has been completed. |
| `prowlarr` | First visitor claims admin | medium | Forward auth only | unchanged | Servarr apps normally force a login set-up on first load (whoever arrives first sets credentials); whether Umbrel's media-app-configurator pre-sets authentication is not verified. |
| `torbrowser` | None | medium | None | unchanged | Web-streamed remote desktop of a browser (port 5800, jlesage-style image); no password configured in the compose, so any LAN visitor gets full control of the browser session, its downloads and config volume, and can browse as the server's T |
| `uptime-kuma` | First visitor claims admin | medium | Forward auth only | unchanged | PROXY_AUTH_ADD is already false, so Umbrel auth is not in front of it today. |
| `wikijs` | First visitor claims admin | medium | OIDC | unchanged | First-run setup wizard lets whoever arrives first create the admin. |
| `bentopdf` | None | low | Forward auth only | off | Client-side PDF toolkit (runs in the browser, static nginx on 8080). |
| `cobalt` | None | low | Forward auth only | unchanged | Umbrel auth is already off (PROXY_AUTH_ADD false) and API_AUTH_REQUIRED=0; API port 9013 is published directly. |
| `denny-cyberchef` | None | low | None | unchanged | Static client-side data tools; no accounts and no stored data. |
| `denny-myip` | None | low | None | unchanged | MyIP is a stateless IP and network toolbox with no accounts and no admin functions. |
| `excalidraw` | None | low | None | off | The excalidraw-persist image has no login. |
| `fossflow` | None | low | Forward auth only | off | Diagram editor with no accounts; server storage is enabled so a visitor can read, overwrite or delete saved diagrams. |
| `invidious` | Optional, off | low | Forward auth only | off | Browsing needs no account; accounts are optional and registration is open by default in upstream config. |
| `ittools` | None | low | Forward auth only | off | Static client-side developer utilities (nginx on port 80). |
| `myspeed` | Optional, off | low | None | off | Speed test dashboard; no login by default (optional password in settings, from memory). |
| `networkingtoolbox` | None | low | Forward auth only | off | Mostly offline-first browser tools with no accounts or stored data. |
| `nextcloud` | Yes | low | OIDC | off | Nextcloud always requires its own login. |
| `privatebin` | None | low | None | off | PrivateBin is a zero-knowledge paste service with no accounts. |
| `searxng` | None | low | None | off | SearXNG has no login. Anyone who can reach it can run searches that go out from the owner's IP. |
| `skybro` | None | low | None | off | Flight tracker dashboard, no user accounts. |
| `vert` | None | low | None | off | VERT is a static web app that converts files in the browser (WASM), with no accounts and no server-side data. |
| `wireguard` | Yes | low | None | off | wg-easy v15 has its own login. Umbrel's pre-start hook completes the setup wizard on fresh installs with user umbrel and the deterministic APP_PASSWORD, so nobody can claim admin first. |

## Already reachable without any Umbrel login

These publish a port directly or run in host network mode, so Umbrel's login never applied. They are reachable from Main, the VPN and IoT today, whatever happens to app auth:

- `denny-librewolf`: port 3001, a full browser desktop with a passwordless sudo terminal.
- `denny-hoarder`: port 3888; the login can be forged because the community compose hard-codes `NEXTAUTH_SECRET`.
- `lobe-chat`: its RustFS bucket of uploaded files is set to anonymous download and published on port 7458.
- `matter-server` (5580), `openthread-border-router` (7586, 7587, 8083) and `homebridge` (8581): host network, no login on the APIs or setup pages.
- `ollama` and `cobalt`: their APIs are published with no auth.
- `tailscale`: the web UI runs in login-server mode, which authorises every request; check whether its port is published before relying on Umbrel auth.
