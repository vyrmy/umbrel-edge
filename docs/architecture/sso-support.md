# Authentik SSO support per app

Recorded 7 October 2026. How each installed Umbrel app can work with Authentik, checked against the version Umbrel ships, upstream docs and Authentik's integration guides; twelve first-pass entries were corrected by a second review. Read with [app-auth-audit.md](app-auth-audit.md), which covers what each app exposes without a login.

Common to all OIDC apps: the app's public URL setting must be its `https://<app>.bebitwise.dev` hostname, or the callback URL will not match. Several Umbrel compose files set it to `http://umbrel.local:<port>`, so it needs a custom environment variable. Keep each app's local admin as a fallback.

## Native SSO (OIDC) (21)

The app signs you in from Authentik itself. Configure an OAuth2/OpenID provider in Authentik and the client in the app. Forward auth is not needed for these, though it can still gate the hostname.

| App | Other methods | In Umbrel's version | Licence or plugin | Set up via | Authentik | Notes |
|---|---|---|---|---|---|---|
| `affine` | - | yes | none | AFFiNE: Admin Panel > Settings > OAuth > 'OIDC OAuth provider config' JSON {"args":{}, "issuer":"https://<authentik>/application/o/<slug>", "clientId":"...", "c | [guide](https://integrations.goauthentik.io/chat-communication-collaboration/affine/) | A dedicated Authentik guide exists at chat-communication-collaboration/affine (the first pass's guessed URL was wrong). |
| `arcane` | - | yes | none | Arcane admin UI (Settings > Security > OIDC Authentication: client ID/secret, issuer https://<authentik>/application/o/<slug>, enable) or OIDC_ENABLED / OIDC_CL | [guide](https://integrations.goauthentik.io/hypervisors-orchestrators/arcane/) | Umbrel ships v2.15.0; the Authentik guide documents OIDC for current Arcane, and I did not confirm the exact release that introduced it, so check the UI shows the OIDC section. |
| `booklore` | - | yes | none | App admin UI: Settings > Authentication > OpenID Connect (issuer URL, client ID/secret, optional group mapping). |  | Umbrel ships 2.4.0, which includes OIDC (UI and backend code present upstream). |
| `denny-actualbudget` | - | yes | none | Env vars on the server container: ACTUAL_OPENID_DISCOVERY_URL, ACTUAL_OPENID_CLIENT_ID, ACTUAL_OPENID_CLIENT_SECRET, ACTUAL_OPENID_SERVER_HOSTNAME (optionally A | [guide](https://integrations.goauthentik.io/miscellaneous/actual-budget/) | Shipped image is actual:26.7.0, which has OIDC (the docs do not state the introducing version, so version presence is inferred from the recent tag). |
| `denny-gitea-mirror` | Trusted header | yes | none | App UI: Configuration > Authentication > SSO Providers > add provider (issuer URL, domain, provider ID, client ID and secret; use Discover). |  | The only field corrected is authentik_guide. |
| `denny-hoarder` | - | yes | none | Env vars on the web service (Umbrel custom env vars): OAUTH_WELLKNOWN_URL, OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, optionally OAUTH_PROVIDER_NAME, OAUTH_SCOPE, OA | [guide](https://integrations.goauthentik.io/documentation/karakeep/) | Store ships ghcr.io/hoarder-app/hoarder:0.18.0; the v0.18.0 configuration doc lists the OAUTH_* variables (OIDC only) but no auto-redirect, so users click 'Sign in with <provider>' on the Hoarder login page (no password, instant w |
| `denny-kitchenowl` | - | yes | none | Env vars added to the 'web' service in the community docker-compose.yml (the all-in-one tombursch/kitchenowl image): FRONT_URL=https://<public host>, OIDC_ISSUE | [guide](https://integrations.goauthentik.io/documentation/kitchenowl/) | An Authentik guide does exist (community support level), at documentation/kitchenowl, contrary to the first pass. |
| `denny-manyfold` | - | yes | none | Env vars on the app container in docker-compose.yml: OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET (all three must be present or the feature is off); multiuse |  | Hint 'native-oidc' confirmed from source: config/application.rb gates the oidc feature on those three env vars and devise.rb configures the openid_connect omniauth strategy with scopes openid, email, profile. |
| `denny-olivetin` | Trusted header | yes | none | OliveTin config.yaml in the mounted ${APP_DATA_DIR}/config directory. |  | The shipped version is OliveTin 2025.6.22 (2k series). |
| `gitea` | LDAP, Trusted header | yes | none | Gitea Site Administration > Identity & Access > Authentication Sources > Add: OAuth2, provider OpenID Connect, name 'authentik' (the name sets the callback /use | [guide](https://integrations.goauthentik.io/development/gitea/) | Umbrel ships gitea/gitea:28.1.0-rootless (release v28.1.0 exists upstream). |
| `gitea-mirror` | Trusted header | yes | none | OIDC: web UI, Settings > Authentication & SSO (Discover button, issuer must match exactly, set Domain to email domain). |  | Upstream docs name Authentik explicitly (provider fixes in v3.8.10, SSRF hardening v3.21.0); Umbrel ships 3.40.3 so both are present. |
| `gitlab` | SAML, LDAP | yes | none for SSO login (CE supports OmniAuth OIDC and SAML); SCIM provisioning needs Premium/Ultimate | Edit gitlab.rb in the bind-mounted config dir (${APP_DATA_DIR}/data/config/gitlab.rb = /etc/gitlab/gitlab.rb), add gitlab_rails['omniauth_providers'] (openid_co | [guide](https://integrations.goauthentik.io/development/gitlab/) | Umbrel ships gitlab-ce 19.4.1. |
| `grafana` | LDAP, Trusted header | yes | none | Env vars GF_AUTH_GENERIC_OAUTH_* (enabled, client id/secret, auth/token/api URLs, role_attribute_path) added to the Umbrel app, or [auth.generic_oauth] in grafa | [guide](https://integrations.goauthentik.io/monitoring/grafana/) | Works in OSS Grafana. |
| `home-assistant` | Trusted header | yes | Community HACS integration required: christiaangoossens/hass-oidc-auth (auth_oidc) or cavefire/hass-openid. | Install HACS into /config (Umbrel app data dir data/), then install 'OpenID Connect/SSO Authentication' (hass-oidc-auth) and add it under Settings > Devices & S | [guide](https://integrations.goauthentik.io/miscellaneous/home-assistant/) | The first pass said the Authentik guide uses a Proxy Provider plus hass-auth-header; it does not. |
| `lobe-chat` | - | yes | none | Env vars on the lobehub app container: AUTH_SECRET, AUTH_SSO_PROVIDERS=authentik, AUTH_AUTHENTIK_ID, AUTH_AUTHENTIK_SECRET, AUTH_AUTHENTIK_ISSUER (https://<auth | [guide](https://github.com/lobehub/lobe-chat/blob/v2.2.17/docs/self-hosting/auth/providers/authentik.mdx) | The previously given guide URL docs.lobehub.com/docs/self-hosting/auth/providers/authentik returns 404; the guide exists in the repo at the shipped v2.2.17 tag (confirmed, so the feature is in the Umbrel version). |
| `mealie` | LDAP | yes | none | Env vars on the mealie container: OIDC_AUTH_ENABLED=true, OIDC_PROVIDER_NAME, OIDC_CONFIGURATION_URL, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, optionally OIDC_GROUPS | [guide](https://integrations.goauthentik.io/documentation/mealie/) | Shipped v3.28.0, well past OIDC introduction (v1.x). |
| `n8n` | SAML, LDAP | yes | Paid licence: OIDC needs the Enterprise plan; SAML and LDAP need Business or Enterprise (self-hosted). | n8n Settings > SSO (instance owner/admin) after activating a licence key; choose OIDC and paste the Authentik discovery URL, client ID and secret. | [guide](https://integrations.goauthentik.io/development/n8n/) | First pass said Enterprise is needed for OIDC, SAML and LDAP; n8n docs say SAML is on Business and Enterprise and LDAP is self-hosted Business and Enterprise, only OIDC is Enterprise-only. |
| `nextcloud` | SAML, LDAP | yes | none (free apps: user_oidc, or Social Login; user_saml for SAML; user_ldap bundled) | Install the OpenID Connect user backend app (user_oidc) from the Nextcloud app store, then add the provider in Admin settings > OpenID Connect; or occ user_oidc | [guide](https://integrations.goauthentik.io/chat-communication-collaboration/nextcloud/) | Umbrel ships Nextcloud 35.0.1; Authentik's guide covers OIDC, SAML and LDAP. |
| `portainer` | - | yes | none for basic OAuth login; automatic team membership from groups is Business Edition only | Portainer UI: Settings > Authentication > OAuth (custom provider). | [guide](https://integrations.goauthentik.io/hypervisors-orchestrators/portainer/) | First-pass 'native-oidc' confirmed by the Authentik guide. |
| `termix` | Trusted header, LDAP | yes | none (SSO, LDAP and trusted-proxy auth are bundled with the 2.9 image) | In Termix 2.9 OIDC moved into the bundled 'Single sign-on' plugin: Admin settings > Single sign-on > Providers > add an OpenID Connect provider (issuer https:// | [guide](https://integrations.goauthentik.io/infrastructure/termix/) | Umbrel ships release-2.9.2. |
| `wikijs` | SAML, LDAP | yes | none | Wiki.js Administration > Authentication: add a Generic OpenID Connect / OAuth2 strategy with Authentik client ID/secret and endpoints; copy its Callback URL int | [guide](https://integrations.goauthentik.io/documentation/wiki-js/) | The hint 'native-oidc' is correct for shipped 2.5.315 (Generic OIDC strategy is in Wiki.js 2.x; the Authentik guide uses it). |

## Trusted header (1)

Authentik forward auth signs you in, and the app trusts the username header Traefik passes on. Only safe while the app's port is unreachable except through Traefik.

| App | Other methods | In Umbrel's version | Licence or plugin | Set up via | Authentik | Notes |
|---|---|---|---|---|---|---|
| `firefly-iii` | - | yes | none | Env vars on the Firefly III container: AUTHENTICATION_GUARD=remote_user_guard, AUTHENTICATION_GUARD_HEADER=<header, PHP form e.g. |  | The hint 'header-auth' is correct: Firefly docs describe only local auth and remote_user_guard and mention no native OIDC (no Authentik integration guide exists). |

## LDAP (1)

Accounts live in Authentik (LDAP outpost) but the app still shows its own password prompt.

| App | Other methods | In Umbrel's version | Licence or plugin | Set up via | Authentik | Notes |
|---|---|---|---|---|---|---|
| `jellyfin` | - | yes | Free official plugin: Jellyfin LDAP Authentication (install from the plugin catalogue). | Authentik: LDAP provider plus LDAP outpost and a service account. | [guide](https://integrations.goauthentik.io/media/jellyfin/) | Umbrel ships Jellyfin 12.2 (linuxserver/jellyfin:version-12.2). |

## Forward auth, single login (14)

The app has no login of its own (or can trust the proxy), so Authentik forward auth is the only prompt. Set `forward_auth: true` in `edge.yaml`.

| App | Other methods | In Umbrel's version | Licence or plugin | Set up via | Authentik | Notes |
|---|---|---|---|---|---|---|
| `cobalt` | - | yes | none | Traefik forward auth in front of the hostname; the app has no accounts or login. |  | Stateless media downloader with no user concept, so no SSO method exists. |
| `denny-librewolf` | - | yes | none | Traefik forward auth in front of the hostname. |  | Remote browser streamed through a web UI with no accounts. |
| `denny-myip` | - | yes | none | Traefik forward auth in front of the hostname; no accounts in the app. |  | IP toolbox with no login. |
| `excalidraw` | - | yes | none | Traefik forward auth in front of the hostname; the app has no login. |  | Self-hosted Excalidraw is a static client-side whiteboard with no accounts. |
| `fossflow` | - | unknown | none | Authentik forward auth on the hostname. |  | No authentication of its own, so the gate is the only protection and the user logs in once at Authentik. |
| `homebridge` | - | yes | none | Traefik forward auth in front of the hostname; the Umbrel manifest ships empty default credentials (Homebridge UI auth effectively off). |  | No native OIDC/SAML/LDAP in Homebridge UI as far as I verified; I did not find an upstream doc or Authentik guide either way (marked as a lack of evidence, not proof). |
| `metube` | - | yes | none | Authentik Proxy Provider (forward auth single application) on the hostname via Traefik |  | MeTube has no login of its own, so the gate is the only auth and the user logs in once at Authentik (no second prompt). |
| `obsidian` | - | yes | none | Traefik forward auth in front of the hostname. |  | Based on the linuxserver docker-obsidian desktop-in-browser image; the manifest specifies no auth, so the stream is open without the gate. |
| `privatebin` | - | yes | none | Traefik forward auth in front of the hostname; PrivateBin has no user accounts. |  | Anonymous pastebin by design, so there is no identity to map. |
| `radarr` | - | yes | none | Authentik forward auth on the hostname, and set Radarr's authentication method to External (not selectable in the UI; set via config.xml AuthenticationMethod or | [guide](https://integrations.goauthentik.io/media/sonarr/) | Radarr has no OIDC, SAML or LDAP. |
| `skybro` | - | unknown | none | Authentik forward auth on the hostname via Traefik |  | Manifest describes a monitoring app configured entirely through a built-in interface, with no mention of authentication. |
| `sonarr` | - | yes | none | Sonarr AuthenticationMethod set to External (config.xml, or Settings -> General -> Authentication in v4) so it trusts the proxy; Authentik gates the hostname. | [guide](https://integrations.goauthentik.io/media/sonarr/) | No native OIDC/SAML/LDAP in Sonarr. |
| `uptime-kuma` | Trusted header | yes | none | Authentik Proxy Provider (forward auth); in Uptime Kuma set Settings > Advanced > Disable Auth, and Settings > Reverse Proxy > Trust Proxy = Yes | [guide](https://integrations.goauthentik.io/monitoring/uptime-kuma/) | No native OIDC/SSO; single user only. |
| `vert` | - | yes | none | Traefik forward auth in front of the hostname; the app is a client-side converter with no accounts. |  | Conversions run in the browser, so no identity is needed. |

## Forward auth, app keeps its own login (28)

Authentik gates the hostname, then the app asks for its own login as well.

| App | Other methods | In Umbrel's version | Licence or plugin | Set up via | Authentik | Notes |
|---|---|---|---|---|---|---|
| `adguard-home` | - | yes | none | Authentik Proxy Provider (forward auth single application) on the app hostname via Traefik; app's own users stay in AdGuardHome.yaml |  | Only built-in bcrypt username/password; no OIDC, LDAP or header auth. |
| `bazarr` | - | yes | none | Authentik proxy/forward-auth in front of the hostname; in Bazarr Settings > General > Security the only options are none, Basic or Forms. |  | Hint 'forward-auth-only' confirmed: config.py allows auth.type only None, basic or form, with no OIDC, SAML, LDAP or trusted-header support. |
| `bentopdf` | - | unknown | none | Traefik forward auth on the app hostname; no app-side config |  | Client-side PDF toolkit with no accounts or login of its own, so a gate is the only option. |
| `code-server` | - | no | none | Traefik forward auth in front of the hostname. |  | Upstream code-server has no OIDC, SAML, LDAP or trusted-header support (linuxserver docs list only password options). |
| `convertx` | - | no | none | Authentik forward auth on the hostname. |  | No native OIDC, SAML, LDAP or header auth in 0.19.0. |
| `denny-changedetection` | - | unknown | none | Authentik forward auth on the hostname; changedetection.io's own optional password is set in its Settings |  | No OIDC/SSO/header-auth found in docs or search; only an optional single password. |
| `denny-cyberchef` | - | unknown | none | Traefik forward auth on the app hostname; no app-side config |  | CyberChef is a static client-side web tool with no users. |
| `downtify` | - | yes | none | Traefik forward auth on the hostname. |  | Only a single built-in admin login (default admin/downtify per the earlier audit); no OIDC, SAML, LDAP or header auth documented. |
| `esphome` | - | unknown | none | Authentik forward auth on the hostname; ESPHome dashboard optional username/password via env (USERNAME/PASSWORD) |  | No native SSO found; I could not confirm the dashboard auth mechanism from the pages fetched. |
| `glasshome` | - | unknown | none | Traefik forward auth in front of the hostname; no app-side auth settings identified. |  | A Home Assistant dashboard that talks to Home Assistant; I found no SSO, OIDC or header support documented, and the Authentik guide listing has none. |
| `golinks-go` | - | unknown | unknown | Unknown. |  | Not present in getumbrel/umbrel-apps (manifest 404) or dennysubke/dennys-umbrel-app-store, and not in the owner's worktree except the earlier audit saying it could not be located. |
| `homehub` | - | no | none | Traefik forward auth in front of the hostname; app has only an optional site-wide password in config.yml and an admin password via flask set-admin-password. |  | README states no SSO, OIDC or proxy-header auth and a deliberately no-login design. |
| `invidious` | - | yes | none | Authentik forward auth on the hostname. |  | No OIDC, SAML, LDAP or header auth in the example config or in 2.20260207.0; no Authentik guide. |
| `ittools` | - | unknown | none | Traefik forward auth on the app hostname; no app-side config |  | IT-Tools is a static client-side toolbox with no accounts. |
| `lidarr` | - | yes | none | Traefik forward auth in front of the hostname. | [guide](https://integrations.goauthentik.io/media/sonarr/) | Servarr apps have no native SSO, OIDC, SAML, LDAP or trusted-header login (Authentik's Sonarr guide says so for Sonarr; Lidarr shares the codebase, but there is no Lidarr-specific guide, so this is by analogy). |
| `myspeed` | - | unknown | none | Traefik forward auth in front of the hostname; the app's own optional password is set in its settings. |  | README documents no SSO, OIDC, LDAP or header auth; I could not confirm the in-app password feature from upstream docs (docs.myspeed.dev not read), so treat it as unverified. |
| `networkingtoolbox` | - | unknown | none | Traefik forward auth on the app hostname; no app-side config |  | Web toolbox with no user accounts. |
| `nginx-proxy-manager` | - | yes | none | Authentik forward auth on the admin hostname via Traefik; NPM keeps its own admin login |  | No native OIDC/SAML/LDAP found for the NPM admin UI (search surfaced only NPM as a reverse proxy for Authentik). |
| `openthread-border-router` | - | unknown | none | Traefik forward auth on the app hostname; no app-side config |  | OTBR web UI and REST API have no authentication or SSO. |
| `overseerr` | - | no | none | Traefik forward auth on the hostname only. | [guide](https://integrations.goauthentik.io/media/seerr/) | Overseerr has no OIDC/SAML/LDAP. |
| `prowlarr` | - | yes | none | Traefik forward auth in front of the hostname; Authentik's Servarr guide (Sonarr) uses a proxy provider/outpost. | [guide](https://integrations.goauthentik.io/media/sonarr/) | No native SSO, OIDC, SAML, LDAP or trusted-header login in Servarr apps (inferred from Authentik's Sonarr guide; no Prowlarr-specific guide, and the Servarr wiki FAQ page did not load usable content). |
| `readarr` | Trusted header | yes | none | Traefik forward auth on the hostname; in Readarr Settings > General > Authentication, set 'External' (or None) so it does not show its own login if the build of |  | No native OIDC or SAML. |
| `searxng` | - | yes | none | Traefik forward auth on the hostname. |  | SearXNG has no authentication at all (manifest has empty credentials), so there is no OIDC or header integration to configure and no Authentik guide. |
| `tailscale` | OIDC | yes | Custom OIDC for the tailnet is a Tailscale account feature; I did not verify which plan tier includes it | Web UI: Traefik forward auth in front of the hostname. | [guide](https://integrations.goauthentik.io/networking/tailscale/) | The Authentik guide is for signing into a Tailscale tailnet (login.tailscale.com) with Authentik as the identity provider. |
| `torbrowser` | - | unknown | none | Traefik forward auth on the app hostname; app-side basic auth (if the image supports it, e.g. |  | Remote-desktop style browser session streamed to the web. |
| `transmission` | - | yes | none | Traefik forward auth on the hostname. |  | Transmission has no OIDC, SAML or LDAP, and no trusted-header support. |
| `wireguard` | OIDC | no | none | Umbrel's WireGuard is wg-easy. | [guide](https://integrations.goauthentik.io/networking/wg-easy/) | Umbrel ships wg-easy 15.3.0, but Authentik's guide states OIDC needs wg-easy 15.4 or later; v15.4.0 was released 2026-08-14, so SSO becomes possible once Umbrel updates the app (or the image tag is overridden). |
| `zigbee2mqtt` | - | unknown | none | Traefik forward auth on the frontend hostname; leave the frontend auth_token unset to avoid a second prompt, or keep it as a second factor |  | Frontend supports only a single shared auth_token, no OIDC, SAML, LDAP or header auth, and no Authentik guide exists. |

## Not applicable (2)

No web login to integrate.

| App | Other methods | In Umbrel's version | Licence or plugin | Set up via | Authentik | Notes |
|---|---|---|---|---|---|---|
| `matter-server` | - | unknown | none | n/a |  | Matter controller server exposing a WebSocket API for Home Assistant and similar clients, no user-facing web UI or login. |
| `ollama` | - | yes | none | Not applicable: Ollama is an HTTP API server on port 11434 with no web UI and no authentication or user model. |  | API-only, so SSO does not apply. |
