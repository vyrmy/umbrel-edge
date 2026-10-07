# Authentication plan per app

Agreed 7 October 2026 for task 006, from [app-auth-audit.md](app-auth-audit.md) (what each app exposes without a login) and [sso-support.md](sso-support.md) (how each can use Authentik). Authentik itself is `denny-authentik`, served as `auth.bebitwise.dev`, internal only, never behind Access or forward auth.

Rules that apply to every app below:

- Umbrel's own app login is turned off for every app that keeps a hostname, because it breaks on custom hostnames (umbrel#2242). It stays on only for the apps with no hostname.
- "Internal only" means the app id is in `external_deny`.
- Forward auth and trusted header protect only the hostname path. Until the UniFi firewall blocks direct access to app ports on `192.168.10.2`, an app with Umbrel's login off can still be opened at its port without a login.
- Forward-auth apps each get a "forward auth (single application)" Proxy provider in Authentik, on the embedded outpost, gated to Household or Household Admins as below. Paths that an app guards with its own key are exempted in Authentik with `skip_path_regex`, so phone apps and tools keep working: `^/api(/.*)?$` on Radarr, Sonarr, Lidarr, Readarr, Prowlarr, Bazarr and changedetection, and `^/api/push/.*$` on Uptime Kuma. Nothing on Transmission, whose RPC has no auth of its own. Edge needs no bypass setting of its own: the outpost answers 200 for those paths.
- Hostname renames planned with the Authentik setup: `denny-authentik` to `auth`, `denny-actualbudget` to `actual`, `denny-kitchenowl` to `kitchenowl`, `denny-hoarder` to `karakeep`, `denny-manyfold` to `manyfold`, `denny-olivetin` to `olivetin`, `denny-changedetection` to `changedetection`, `denny-librewolf` to `librewolf`. OIDC redirect URIs in Authentik use these names.

| App | Method | External | Notes |
|---|---|---|---|
| **Sign in with Authentik (OIDC)** | | | Keep each app's local admin as a fallback |
| Actual Budget (`actual`) | OIDC | Yes | Env vars; server hostname set to `actual.bebitwise.dev` |
| AFFiNE | OIDC | Yes | Turn sign-up off |
| Arcane | OIDC, Household Admins | Internal only | Controls Docker; set `APP_URL` |
| Booklore | OIDC (public client) | Yes | Settings, Authentication |
| Gitea | OIDC | Yes | No forward auth (breaks `git` over HTTPS); set `ROOT_URL` |
| GitLab | OIDC | Yes | Deferred: the app is stopped; set `external_url` when started |
| Gitea Mirror | OIDC | Yes | Deferred until one of the two installed copies is chosen; fix `BETTER_AUTH_URL` |
| Grafana | OIDC | Yes | `GF_AUTH_GENERIC_OAUTH_*` env vars |
| Karakeep (`karakeep`, was Hoarder) | OIDC | Yes | Also needs port 3888 closed: the login can be forged |
| KitchenOwl | OIDC | Yes | The mobile app supports OIDC |
| Manyfold | OIDC | Yes | Usernames of at least three characters |
| Mealie | OIDC | Yes | Turn sign-up off |
| Nextcloud | OIDC (`user_oidc` app) | Yes | No forward auth (breaks sync clients and WebDAV) |
| OliveTin (`olivetin`) | Deferred | Internal only | CVE-2026-30223 affects the shipped 2025.6.22; keep Umbrel login and no hostname until updated |
| Portainer | OIDC, Household Admins | Internal only | |
| Termix | OIDC, Household Admins | Internal only | Set up in the 2.9 Single sign-on plugin |
| Wiki.js | OIDC | Yes | |
| Lobe Chat | n/a | n/a | Not installed; only a leftover app-data folder |
| **Authentik signs you in, the app trusts the header** | | | |
| Firefly III | Trusted header (`X-authentik-email`) | Yes | Only after the firewall blocks its port; until then its own login plus forward auth |
| **Forward auth, app's own login off (one prompt)** | | | `forward_auth: true` |
| Radarr, Sonarr | Forward auth | Yes | Umbrel already sets them to trust the proxy; `/api` skips Authentik (API key protects it) |
| Lidarr, Readarr, Prowlarr, Bazarr | Forward auth | Yes | Set their auth to External; `/api` skips Authentik (API key protects it) |
| Transmission | Forward auth | Yes | |
| MeTube, changedetection, HomeHub | Forward auth | Yes | changedetection's `/api` skips Authentik (API key protects it) |
| Uptime Kuma | Forward auth | Yes | Turn its own auth off; `/api/push/` skips Authentik (push tokens protect it) |
| Downtify | Forward auth | Yes | Turn its login off only after the firewall change |
| go (golinks) | Forward auth | Owner's call | Login method not confirmed |
| Homebridge | Forward auth | Internal only | Port 8581 open until the firewall change; app currently stopped |
| ESPHome | Forward auth | Internal only | |
| Obsidian, LibreWolf, Tor Browser | Forward auth | Internal only | Remote desktops with root terminals |
| Zigbee2MQTT | Uninstall | n/a | Stopped, replaced by Thread |
| **Forward auth in front of the app's own login (two prompts)** | | | Admin tools |
| AdGuard Home | Forward auth, Household Admins | Internal only | |
| Nginx Proxy Manager | Forward auth, Household Admins | Internal only | Edge replaces it; consider uninstalling |
| code-server | Forward auth, Household Admins | Internal only | A shell on the server, so keep both |
| WireGuard | Forward auth, Household Admins | Internal only | Move to OIDC when Umbrel ships wg-easy 15.4 |
| ConvertX | Forward auth | Yes | Turn registration off |
| GlassHome | Forward auth | Owner's call | Login method not confirmed |
| **App's own login only, no Authentik** | | | |
| Home Assistant | Own login with MFA | Owner's call | Companion app cannot do forward auth; OIDC through HACS later if wanted |
| Jellyfin | Authentik LDAP (official LDAP plugin) | Yes | Central accounts that still work in TV and phone apps; users type their Authentik password in Jellyfin. The 9p4 SSO plugin is archived. Chosen 7 October 2026 |
| Overseerr | Plex or Jellyfin sign-in | Yes | OIDC only after moving to Seerr |
| n8n | Own login | Yes | Forward auth would block webhooks; OIDC needs Enterprise |
| **No login needed (Cloudflare Access covers outside)** | | | |
| IT-Tools, BentoPDF, VERT, CyberChef, MyIP, Networking Toolbox, SkyBro, MySpeed | None | Yes | Nothing stored, nothing to misuse |
| Excalidraw, FossFLOW, PrivateBin | Forward auth optional | Yes | Store data anyone on the LAN can edit |
| SearXNG, Invidious, Cobalt | Forward auth optional | Yes | Others on the LAN could use them as a proxy |
| **No hostname** | | | |
| Ollama | None | No | No login on its API; reached internally |
| Matter Server, OpenThread Border Router | None | No | Not user-facing; add to `exclude` |
| Tailscale | Umbrel login | No | |
