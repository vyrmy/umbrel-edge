# Umbrel edge: reverse proxy with automatic internal and external DNS

## Problem

Every app on the Umbrel (Dell 3080, `192.168.10.2`, Servers network) is reached today by IP and port, or by `umbrel.local`, which does not resolve across VLANs. There is no TLS on the LAN and no way in from outside apart from the WireGuard VPN.

This project gives every Umbrel app one HTTPS hostname, `<app>.<DOMAIN>`, that works both at home and away. At home the name resolves straight to a reverse proxy on the LAN. Away, it resolves to a Cloudflare Tunnel with Cloudflare Access in front. Installing or removing an app on the Umbrel creates or removes its route, its UniFi DNS record, its tunnel hostname and its Access application within a minute, with no manual step.

## Non-goals

- Exposing anything that is not an Umbrel app. UniFi, Protect, the PicoKVM and the Sonoff dongle stay LAN or VPN only.
- Replacing Umbrel's own login across the board. Apps keep Umbrel app auth where it works (see Risks); Authentik forward auth (decision 14) replaces it only on the apps you mark.
- Wildcard DNS or wildcard tunnel ingress. Every hostname is created explicitly, so nothing is reachable by accident.
- Hostnames more than one level deep (`app.home.DOMAIN`). Cloudflare's free Universal SSL only covers `*.DOMAIN`.
- IPv6. None of the networks have IPv6 today.
- High availability. One proxy, one tunnel connector. If the Dell is down, the apps are down anyway.
- A web UI for the sync service. Configuration is one YAML file.
- Managing Tailscale, WireGuard or the UniFi firewall beyond the rules listed in task 001.
- Voice and video calls from the desktops. Messaging works through the egress proxy; calls stay on phones (decision 18).
- Reaching anything from a desktop that is not an Umbrel app with a hostname: other LAN devices, app ports by IP, other containers.

## Decisions

1. **One hostname per app, same name inside and outside (split horizon).** `jellyfin.DOMAIN` works on the sofa and on the train. Rejected: separate internal names (`jellyfin.home.DOMAIN`), which need two bookmarks per app and a paid certificate for the two-level name on Cloudflare.
2. **Traefik v3 as the reverse proxy.** Built-in Let's Encrypt with Cloudflare DNS-01, and a file provider that hot-reloads generated YAML. Rejected: Nginx Proxy Manager, which is already installed but UI-driven, with an awkward API, and it is stuck on ports 40080/40443. Also rejected: Caddy, which needs a custom build for the Cloudflare DNS module.
3. **The proxy gets its own LAN address, `192.168.10.4`, via a Docker macvlan network.** umbreld holds port 80 on the host now, and umbrelOS 2.0 also binds 443 and 2000 unconditionally ([umbrel#2239](https://github.com/getumbrel/umbrel/issues/2239)). A separate address avoids both. Rejected: patching umbreld's ports with a custom hook, which breaks on the next OS update.
4. **Traefik is dual-homed.** Macvlan receives LAN traffic and carries Traefik's default route (`gw_priority: 1`), so replies to Main and VPN clients leave from `192.168.10.4`. `umbrel_main_network` reaches the Umbrel host's app ports through `host.docker.internal`, mapped to that network's gateway `10.21.0.1`, because a macvlan child cannot talk to its own parent host. `host-gateway` is not used: it resolves to `docker0`, which is not directly attached, so it would follow the default route out to the UniFi gateway.
5. **One wildcard certificate, `*.DOMAIN`, from Let's Encrypt via DNS-01.** One certificate means one renewal and one rate-limit budget. Certificate Transparency logs show only the wildcard, not app names.
6. **Internal DNS is per-app A records in UniFi, written through the official UniFi Network Integration API.** UniFi's resolver does not support wildcards ([external-dns-unifi-webhook](https://github.com/home-operations/external-dns-unifi-webhook)), and Network 10.6.106 is above the 10.3.58 minimum for the DNS policy endpoints. Rejected: a UniFi "Forward Domain" to AdGuard Home with a wildcard rewrite, which adds a second DNS server that isn't set up yet and would capture the whole zone.
7. **External access uses our own `cloudflared` container inside this app, on a remotely managed tunnel.** It sits on the same bridge as Traefik and reaches it by container name. Rejected: the existing Umbrel "Cloudflare Tunnel" app, which runs in a separate Docker network and cannot reach the macvlan address from the host.
8. **Every external hostname gets a Cloudflare Access application by default** (one-time email PIN, allow-list of your addresses). Access is free for up to 50 users. One login covers every app: Access keeps a session on the team domain (`<team>.cloudflareaccess.com`), so after the first PIN each further app costs one automatic redirect and no prompt, until the global session expires. Set the global session and each app's session to the same duration (`access.session_duration`). Policies are written against email addresses, so swapping the one-time PIN for an identity provider later (any OIDC provider, including a self-hosted one) is a login-method change in Zero Trust, with no change to `sync`. Rejected: relying on each app's own login, since several apps (Transmission, IT-Tools, CyberChef, Excalidraw) have none.
9. **Exposure defaults: internal on for every app, external on behind Access for every app, with a short built-in deny list for external.** The deny list is Home Assistant, Portainer, Arcane, code-server, Termix, OliveTin, Tor Browser, LibreWolf, WireGuard, Tailscale, the edge app itself and the Umbrel dashboard (`umbrel`), which installs apps and opens a terminal on the host. Every default can be overridden per app in `edge.yaml`. An `external_deny` list in `edge.yaml` replaces the built-in one rather than adding to it, so it has to repeat these ids. Home Assistant is on the deny list because its companion app cannot complete an Access login. Away from home, the companion app reaches it over the UniFi WireGuard VPN with on-demand rules: the VPN hands out the UCG as DNS server, so `home-assistant.DOMAIN` resolves to `192.168.10.4` exactly as it does at home, with the same certificate and one URL in the app. The firewall rule VPN → `192.168.10.4` TCP 443 is already in task 001. Rejected: mTLS client certificates at Cloudflare with Access bypassed for Home Assistant. It works on iOS, but the Android app has an open bug where it fails to connect through Cloudflare mTLS ([android#5899](https://github.com/home-assistant/android/issues/5899)), and the published workaround exempts the API and WebSocket paths, which leaves Home Assistant's own login as the only gate.
10. **The sync service is a small Python reconciler, running in a loop every 60 seconds plus on config change.** It is idempotent and only deletes resources it created. It tags them `managed-by=umbrel-edge` in Cloudflare DNS comments, uses an `umbrel-edge:` name prefix for Access apps, and keeps an ownership file for UniFi records. Rejected: Docker-label discovery, because Umbrel app compose files are store-managed and label edits are lost on update.
11. **App discovery reads `${UMBREL_ROOT}/app-data/*/umbrel-app.yml` read-only, for the apps umbreld lists as installed.** Each manifest carries `id`, `name` and `port`, the app_proxy port on the host. umbreld keeps the installed app ids under `apps` in `${UMBREL_ROOT}/umbrel.yaml` (`StoreSchema.apps` and the `FileStore` in `packages/umbreld/source/index.ts`, umbrelOS 2.0.0). It adds an id once an install has finished and removes it on uninstall. A folder in app-data whose id is not in that list belongs to no installed app, so it gets no route and an info log line the first time it is skipped. A leftover `lobe-chat` folder once produced a route and a UniFi record for a port nothing listened on; dropping it from the desired state makes `sync` delete the records it owns on the next pass. An app being installed gets its route once umbreld lists it. If `umbrel.yaml` is missing, unreadable or has no list under `apps`, discovery logs a warning on each pass and counts every folder as installed, so a bad read never withdraws every route. The compose file mounts the whole of `${UMBREL_ROOT}` read-only at `/umbrel` (see Risks). Rejected: umbreld's tRPC API, which needs a user JWT (and your account has 2FA). Also rejected: a bind mount of `umbrel.yaml` alone. umbreld writes the file to a temporary name and renames it over the old one on every store change, and a single-file bind mount keeps showing the copy from container start.
12. **The app is shipped as a public Umbrel community app store in a GitHub repo (`vyrmy/umbrel-edge`), with images built by GitHub Actions to a public GHCR package and pinned by digest.** Community app stores are Umbrel's supported extension point and survive OS updates. Public means the Umbrel needs no GitHub token to pull the store or the image. Nothing secret is in the repo: tokens live only in `secrets.env` on the Umbrel, and the only home details it reveals are the `192.168.10.0/24` layout and the domain.
13. **The Umbrel dashboard opens apps at their own hostnames through an injected launcher script.** The dashboard builds an app's link from whatever host you loaded it on plus the app's port, so from `umbrel.DOMAIN` it would open `umbrel.DOMAIN:8123`. Traefik's body-rewrite plugin adds one `<script src="/__edge/launcher.js">` tag to the dashboard's HTML on the `umbrel.DOMAIN` route only. `sync` generates `launcher.js` from the same port-to-hostname map it already builds, and serves it on that route. The script rewrites any link or `window.open` call aimed at `<current host>:<port>` to `https://<app>.DOMAIN<path>`. It works at home, and through the tunnel once `apps.umbrel.external` is set, since the dashboard is on the deny list (decision 9). Rejected: Traefik listening on every app port and redirecting, which only works at home, because Cloudflare proxies a fixed short list of ports; it also needs a Traefik restart whenever an app is installed. Rejected: patching the dashboard's code, which is lost on every umbrelOS update.
14. **Apps with no login of their own can sit behind Authentik forward auth, per app.** `apps.<id>.forward_auth: true` adds Traefik's `forwardAuth` middleware, pointed at Authentik's embedded outpost, to that app's router, so the same login applies at home and through the tunnel. Each protected hostname also gets a router for `/outpost.goauthentik.io/` to the outpost, above the app router, which is how Authentik's [Traefik integration](https://docs.goauthentik.io/add-secure-apps/providers/proxy/server_traefik/) finishes the login in single application mode; in domain level mode the same router is harmless. Off by default, because Umbrel app auth has to be turned off for every app that uses it ([umbrel#2242](https://github.com/getumbrel/umbrel/issues/2242)). Rejected: forward auth on the internal path only, which treats home and away differently for no gain.
15. **An app's hostname can go straight to its container, past umbreld's app gateway.** `apps.<id>.upstream_host` names a container on `umbrel_main_network` (every app's default network) and `upstream_port` its own port, so Traefik calls `http://<container>:<port>` instead of `host.docker.internal:<Umbrel port>`. The gateway, and with it Umbrel's login, is then off the hostname path only. Umbrel's login stays on for `192.168.10.2:<port>`, where umbrelOS 2.0 lets in only the users the app is shared with, while the hostname gets Authentik. That removes the umbrel#2242 trade-off for apps that hold one person's data. Forward auth defaults to on for such a route, since nothing else would guard it, unless the app is in `forward_auth_deny` or sets `forward_auth: false`. The launcher keeps mapping the manifest port, which is what the dashboard links to. Rejected: turning Umbrel's login off and waiting for the firewall to close app ports, which leaves the port open to every LAN device until then.
16. **Each person gets their own desktop app, `vyrmy-desktop-<n>`, with a browser and Signal that stay signed in.** umbrelOS 2.0 shares one copy of an app, with one set of data, among every user it is shared with, and its gateway tells the app nothing about who is signed in (`app-gateway.ts`), so a per-person browser needs one app per person. The slots are numbered (Desktop 1, Desktop 2), so no names go into this public repo; names live in the owner's `edge.yaml` and Umbrel's sharing. Each slot runs `ghcr.io/vyrmy/umbrel-desktop`: linuxserver's Webtop (Ubuntu, XFCE, Selkies streaming) with its Chromium, plus Signal Desktop from Signal's apt repository and WhatsApp Web in its own Chromium app window, all started with the session. WhatsApp has no Linux app, so the window is `chromium --app=https://web.whatsapp.com/` on the same profile as the browser, which keeps it linked; at login it waits for the browser's singleton socket before joining it, because Webtop's `wrapped-chromium` clears the singleton lock whenever no Chromium is running. Rejected: unofficial WhatsApp wrappers (WasIstLos, ZapZap), each a third-party binary with every message. `scripts/new-desktop-slot <n>` adds a slot. The image is rebuilt every Monday and `scripts/pin-desktop-image` commits the new digest and a date version, because Signal Desktop stops working about 90 days after each release and Chromium needs its patches. The hostname uses decision 15, Cloudflare Access outside and an Authentik application bound to the one person. Rejected: Thunderbird alone (the first request), which covers mail but not calendars, WhatsApp Web or Signal; one shared app routing on the `X-authentik-username` header, which anyone on the LAN could forge at the Umbrel port; and spawning containers per user through the Docker socket, which puts host root inside an internet-facing app.

17. **Each desktop is a client on its own private network, with Traefik as its only way into the Umbrel.** Edge defines one network per slot, `desk-<n>` (Docker name `vyrmy-edge_desk-<n>`, subnet `10.31.<n>.0/24`), with `internal: true` and `com.docker.network.bridge.inhibit_ipv4: "true"`, and attaches Traefik to each at `10.31.<n>.2`. Slot `n` puts its desktop container on that network alone, at `10.31.<n>.10`, beside its own egress proxy (decision 18). A plain internal network is not enough: on Docker 29.9 the host could still reach the desktop, and the desktop could reach any host service on the bridge's gateway address; with no host address on the bridge, both stop. One network per slot, because on a shared one each desktop could reach the others' port 3000. Edge owns the networks so that Traefik never depends on a desktop being installed, and each desktop's manifest lists `dependencies: [vyrmy-edge]`, which stops umbreld uninstalling Edge underneath it (it orders nothing else). umbreld's app gateway can no longer reach the desktop, so the slot's Umbrel port serves a placeholder page behind Umbrel's login, and the desktop itself opens only at its hostname; this replaces decision 15's note about Umbrel's login on `192.168.10.2:<port>` for the desktops. Rejected: a plain internal network (the host holes above); one network for every desktop; networks owned by the desktop apps (Traefik would fail to start whenever one is missing); spawning containers per user through the Docker socket (decision 16).
18. **A desktop reaches the internet only through its own Squid, which refuses private destinations.** Each slot runs `egress`, `ubuntu/squid` pinned by digest, as uid 13 with a read-only root, no capabilities and its configuration inline in `command`, on `desk-<n>` at `10.31.<n>.3` and on the slot's own normal bridge. Squid checks the address it actually connects to, so a public name that resolves to a private address, a mixed answer, an IP literal in decimal or octal form and every IPv6 literal are refused (64 of 64 escape attempts refused in a local test). Ports 80, 443 and 1025 to 65535 are allowed, and only refusals are logged, because the desktop is one person's browsing. In the image, `/etc/chromium.d/90-egress` gives every Chromium launch, the WhatsApp window included, `--proxy-server`, a bypass list for `DOMAIN` and `*.DOMAIN`, and a `--host-resolver-rules` mapping those names to Traefik's address; a managed policy locks the proxy setting. Signal reads only `HTTPS_PROXY`, which `wrapped-signal` sets. Both addresses are IP literals, so the desktop does no DNS of its own. Calls do not connect: Signal's RingRTC has no proxy support and needs UDP, and WhatsApp's are expected to behave the same. Rejected: tinyproxy (it filters the host string, so a public name resolving to `10.x` got through); a routing container with NAT (calls would work, but it needs `NET_ADMIN` in both the desktop and the router and a hand-set default route; revisit if calls matter).
19. **Traefik serves the tunnel on its own entrypoints and places each route by its flags.** `websecure` (`:443`) keeps serving the LAN, the VPN, the `umbrel_main_network` aliases and the desktops. `tunnel` (`10.30.0.2:8443`) serves cloudflared, on a fixed `edge` subnet `10.30.0.0/24`. A route with `internal` true is on `websecure`, one with `external` true on `tunnel`, and the `outpost-*`, `edge-assets` and `edge-umbrel-fallback` routers copy the list of the route they serve. `internal: false` therefore means the LAN gets 404, and a stray ingress entry or DNS record cannot reach a home-only app. The hardening flags (`aliasHeadersStrategy`, `encodedCharacters`) are per entrypoint and are repeated on every one. Rejected: one entrypoint for everything (the tunnel's hostname list was the only control).
20. **Home Assistant alone sees each visitor's real address, through a second, trusting tunnel entrypoint.** `apps.<id>.real_ip: true` puts an external route on `tunnel-realip` (`10.30.0.2:8444`), which trusts forwarded headers from the `edge` subnet and does not append its own, so `X-Forwarded-For` ends with the address Cloudflare appended. A middleware on that route pins `X-Forwarded-Host` and `X-Forwarded-Proto` and drops `X-Forwarded-Uri`, `X-Forwarded-Method`, `X-Forwarded-Port` and `X-Real-Ip`, which a trusting entrypoint would otherwise pass on from the client. Home Assistant walks `X-Forwarded-For` from the right past its trusted proxies, so it gets Cloudflare's entry: local-only webhooks and users stop working from the internet, and a forged header changes nothing. `real_ip` is refused with forward auth, because a client's `X-Forwarded-Uri` would reach Authentik's skip rules. Rejected: trusting forwarded headers on `tunnel` itself (Authentik records the leftmost entry, which the client chooses, and forward auth would see client values); stripping middleware on every other route (one missing middleware reopens the bypass).
21. **cloudflared checks the Access token for every route with `access` true.** With `access.team_name` set, each such ingress rule carries `originRequest.access` with `required: true`, the team name and that hostname's Access app `aud`. A request with no token, a wrong audience or an invalid token is refused by cloudflared, so a deleted or edited Access app no longer leaves its hostname open. Access apps are now created and updated before the tunnel ingress is written, since the ingress needs their `aud`, and a hostname whose app is missing gets no ingress entry and no DNS record. Rejected: checking the token in Traefik (a JWT plugin would run inside the process that holds the certificate token).
22. **Desktops cannot open the Umbrel dashboard or Edge's own page.** Routes for ids in `desktop_deny` (`umbrel` and `vyrmy-edge` by default, overridable with `apps.<id>.desktops`) get a `no-desktops` middleware, an IP allowlist of every IPv4 range except `10.31.0.0/16`, plus all of IPv6. The dashboard installs apps and opens a terminal on the host, and a desktop's browser is the most exposed program in the house. Rejected: a separate Traefik entrypoint for desktops (Chromium can map a name to an address but not to another port, and `websecure` already holds `:443` on every interface).

## Stack

| Component | Choice | Version |
|---|---|---|
| Reverse proxy | Traefik | v3.x (pin the latest v3 minor at build time) |
| Tunnel connector | cloudflared | latest release at build time, pinned by digest |
| Sync service | Python | 3.12 |
| HTTP client | httpx | 0.27+ |
| Models and validation | pydantic | v2 |
| YAML | PyYAML | 6.x |
| Tests | pytest, respx (httpx mocking) | current |
| Lint and types | ruff, mypy `--strict` | current |
| Personal desktop | linuxserver Webtop (Ubuntu XFCE, Selkies) with Chromium, plus Signal Desktop | rebuilt weekly, pinned by digest |
| CI | GitHub Actions, GHCR | n/a |
| Host | umbrelOS on the Dell 3080 | whatever is installed; check 1.x or 2.x in task 001 |

## Architecture

```
                         Internet
                            │
                   Cloudflare edge (DNS, Access, Tunnel)
                            │  outbound QUIC from cloudflared
┌───────────────────────────┼─────────────────────────── Umbrel (192.168.10.2) ─┐
│  umbrel-edge app          │                                                  │
│   ┌────────────┐   bridge "edge"   ┌───────────┐        ┌──────────────┐     │
│   │ cloudflared├──────────────────►│  traefik  │◄───────┤ sync (Python)│     │
│   └────────────┘   https://traefik │           │ writes │              │     │
│                                    │  macvlan  │ dynamic│ reads app-data│    │
│                                    │192.168.10.4 yml   │ calls CF API  │    │
│                                    └─────┬─────┘        │ calls UniFi API│   │
│                     host.docker.internal:│<port>        └──────────────┘     │
│   Umbrel apps (app_proxy ports) ◄────────┘                                   │
└──────────────────────────────────────────────────────────────────────────────┘
        ▲ LAN clients resolve app.DOMAIN → 192.168.10.4 via UniFi A records
```

**Where each component runs.** Everything runs on the Umbrel, in one Umbrel app with three containers: `traefik`, `cloudflared` and `sync`. Cloudflare provides DNS for `DOMAIN`, the tunnel and Access. The UCG Fiber provides LAN DNS.

**Network topology.**

- Traefik listens on `192.168.10.4:80` (redirect to HTTPS) and `:443`. That address is on the Servers network, outside the DHCP pool (`.6` to `.254`), with a UniFi reservation for the fixed macvlan MAC.
- Firewall additions: Main → `192.168.10.4` TCP 80/443, and VPN → `192.168.10.4` TCP 80/443. IoT gets nothing.
- `cloudflared` makes outbound connections only. The Servers zone already allows internet access.
- Traefik's other networks: `edge` (`10.30.0.0/24`, Traefik at `10.30.0.2`, shared with cloudflared and sync) and one `desk-<n>` per desktop (`10.31.<n>.0/24`, internal, no host address; Traefik `.2`, the slot's egress proxy `.3`, the desktop `.10`). Entrypoints: `websecure` `:443` on every interface, `tunnel` `10.30.0.2:8443`, `tunnel-realip` `10.30.0.2:8444`, `dashboard` `:8080` (decisions 19 and 20).
- Traefik reaches apps at `host.docker.internal:<port>`, which is `10.21.0.1` on `umbrel_main_network`. Home Assistant runs in host network mode on port 8123 and is reached the same way. Apps therefore see Traefik's requests coming from `10.21.0.5`.

**Request paths.**

- *At home:* the client asks the UCG for `jellyfin.DOMAIN`, gets the local A record `192.168.10.4`, connects over TLS with the wildcard certificate, and Traefik proxies to `host.docker.internal:<jellyfin port>`.
- *Away:* public DNS returns a CNAME to `<tunnel-id>.cfargotunnel.com`. Cloudflare Access challenges the visitor. The tunnel delivers the request to `cloudflared`, which checks the Access token (decision 21) and calls `https://traefik:8443` (`:8444` for a `real_ip` route) with `originServerName: jellyfin.DOMAIN`. Traefik then proxies it as it does at home, if the route is external.
- *From a desktop:* Chromium maps `*.DOMAIN` to Traefik's address on the desktop's own network and connects there directly, so the request takes the home path (`websecure`, no Access) and meets the same logins as at home. Every other name goes to the slot's Squid, which resolves it and refuses private destinations.

**Background work.** `sync` runs a reconcile loop every 60 seconds, and immediately when `edge.yaml` changes. Each pass has four stages:

1. Discover the apps and build the desired state.
2. Write the Traefik file.
3. Reconcile UniFi.
4. Reconcile Cloudflare: tunnel ingress, then DNS, then Access.

Each stage is independent. If one fails, the others still run, the error is recorded, and the next pass retries. A crash mid-pass is safe, because every write is an idempotent upsert. The Traefik file is written to a temporary file and renamed into place, so Traefik never reads a half-written file.

**Secrets and configuration.** Secrets live in `${APP_DATA_DIR}/data/secrets.env`, mode 0600, created by hand once and never committed:

- `CF_DNS_API_TOKEN`: read by Traefik for the DNS-01 challenge. Zone DNS Edit on `DOMAIN` only.
- `CF_API_TOKEN`: read by `sync`. Zone DNS Edit on `DOMAIN`, Account Cloudflare Tunnel Edit, and Account Access Apps and Policies Edit.
- `CF_ACCOUNT_ID`, `CF_ZONE_ID`, `CF_TUNNEL_ID`: read by `sync`.
- `TUNNEL_TOKEN`: read by `cloudflared`.
- `UNIFI_API_KEY`: read by `sync`. Created under Control Plane → Integrations.
- `UNIFI_HOST`, `UNIFI_SITE_ID`: read by `sync`.
- `UNIFI_CERT_SHA256` (optional): read by `sync`. The SHA-256 fingerprint of the UniFi gateway's certificate, as hex with or without colons, in either case. When it is set, `sync` fetches the certificate from `UNIFI_HOST` (port 443 unless `UNIFI_HOST` carries one) at the start of each pass and compares fingerprints before sending the API key. A mismatch fails the UniFi stage with both fingerprints in the error. On a match, every connection in that pass trusts that certificate alone. When it is unset, the connection is unverified and `sync` logs a warning once per start. Get the value with `openssl s_client -connect <host>:443 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256`, and use what follows the `=`. If the gateway's certificate is renewed or regenerated, the stage fails until the pin is updated, and an expired certificate fails the connection even when the pin matches.

`sync` gets the whole file as its environment; its own token already covers what the other two keys allow. Traefik and `cloudflared` face the internet, and Traefik runs a third-party plugin, so neither sees `secrets.env` or mounts the data folder. On every start, the `init` service copies `CF_DNS_API_TOKEN` to `data/secrets/traefik/cf_dns_api_token` and `TUNNEL_TOKEN` to `data/secrets/cloudflared/tunnel_token`, each mode 0400 for that container's user, and each container mounts only its own folder (Traefik reads it as `CF_DNS_API_TOKEN_FILE`, `cloudflared` with `--token-file`). Edit `secrets.env` as before and restart the app. Traefik's other mounts are its certificate store, `data/acme/`, and `sync`'s routes, `data/traefik/dynamic/`, read-only. Edge 0.4.0 and earlier kept the store at `data/acme.json`; `init` copies it across once.

Non-secret configuration lives in `${APP_DATA_DIR}/data/edge.yaml`. The dashboard itself gets a route as a built-in app with id `umbrel`, upstream port 80.

**Environments.** One, production. For changes, run `sync --dry-run` locally against a copy of the app-data manifests. It prints the diff and makes no API writes.

**Observability.**

- Logs are structured JSON on stdout, visible in the Umbrel app logs. Traefik keeps its error log but has no access log, because some apps put tokens in query strings (Umbrel Files' `/api/files/view?token=`). Turn it on briefly with `--accesslog=true` when debugging.
- `sync` serves `GET /healthz` on the bridge only.
- Uptime Kuma (already installed) monitors `/healthz` and each app hostname. It alerts you through whatever notifier you set in Uptime Kuma. It runs on the Umbrel, which cannot reach `192.168.10.4`, so it reaches a hostname only if that name is a network alias on Traefik's `umbrel_main_network` endpoint (see Risks). Add the names it monitors to that alias list.

**Cost.** £0 a month: Cloudflare Tunnel, DNS and Access (up to 50 users) are free, and the domain is one you already own. Let's Encrypt is free.

## Structure

```
umbrel-edge/                         # GitHub repo, added to Umbrel as a community app store
├── umbrel-app-store.yml             # store id "vyrmy", store name (public repo)
├── vyrmy-edge/                  # the Umbrel app (folder = <store id>-<app id>)
│   ├── umbrel-app.yml               # manifest: id, name, port (Traefik dashboard, LAN only)
│   ├── docker-compose.yml           # init, traefik, cloudflared, sync; macvlan + bridge networks;
│   │                                #   Traefik static config as command flags
│   └── edge.example.yaml            # annotated example of edge.yaml
├── sync/                            # Python package, built into the sync image
│   ├── pyproject.toml
│   ├── src/umbrel_edge/
│   │   ├── __main__.py              # CLI: run loop, --once, --dry-run
│   │   ├── config.py                # loads edge.yaml + env into Settings (pydantic)
│   │   ├── models.py                # AppManifest, AppPolicy, Route, DesiredState
│   │   ├── discovery.py             # reads installed apps' manifests → list[AppManifest]
│   │   ├── desired.py               # manifests + config → DesiredState (pure)
│   │   ├── traefik_writer.py        # DesiredState → dynamic YAML, atomic write
│   │   ├── unifi.py                 # UniFi Integration API client + reconciler
│   │   ├── cloudflare.py            # tunnel ingress, DNS, Access clients + reconcilers
│   │   ├── ownership.py             # tracks UniFi record ids this service created
│   │   ├── health.py                # /healthz server and last-run state
│   │   ├── launcher.py              # DesiredState → launcher.js, served at /__edge/launcher.js
│   │   └── loop.py                  # orchestration, per-stage error isolation
│   └── tests/                       # pytest; respx fakes for both APIs
├── vyrmy-desktop-<n>/               # one personal desktop per person (decision 16)
│   ├── umbrel-app.yml               # id vyrmy-desktop-<n>, port 18100 + n, version = image date
│   └── docker-compose.yml           # desktop (on vyrmy-edge_desk-<n> only), egress (Squid), placeholder
├── desktop/                         # the umbrel-desktop image: Webtop + Signal, autostart files,
│                                    #   /etc/chromium.d/90-egress, the egress policy init script
├── scripts/
│   ├── new-desktop-slot             # copies Desktop 1 to Desktop <n>
│   └── pin-desktop-image            # sets every slot's image digest and version (run by CI)
├── .github/workflows/build.yml      # build + push sync image to GHCR, run tests
├── .github/workflows/desktop.yml    # weekly umbrel-desktop build, then pin-desktop-image
└── docs/architecture/               # this plan and the task files
```

## Data model

No database. The persistent state is:

- `edge.yaml` (input, owned by you).
- `ownership.json` (UniFi record ids, owned by `sync`). A hostname goes into `pending` just before its create request and leaves once the id is stored. A pending name whose record has the proxy IP is the only kind of unowned record `sync` ever adopts, so a lost response or a crash cannot orphan a record, and a hand-made record never becomes `sync`'s. A definite 4xx refusal clears the mark. Version 1 files still load.
- Traefik's `acme/acme.json`.
- The remote resources in UniFi and Cloudflare.

The models:

```python
# sync/src/umbrel_edge/models.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, IPvAnyAddress

SUBDOMAIN_RE = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"

class AppManifest(BaseModel):
    """What discovery reads from app-data/<id>/umbrel-app.yml."""
    id: str
    name: str
    port: int = Field(ge=1, le=65535)

class AppPolicy(BaseModel):
    """Per-app override in edge.yaml. None means 'use the default'."""
    subdomain: str | None = Field(default=None, pattern=SUBDOMAIN_RE)
    internal: bool | None = None
    external: bool | None = None
    access: bool | None = None
    upstream_port: int | None = Field(default=None, ge=1, le=65535)
    upstream_scheme: Literal["http", "https"] = "http"
    upstream_host: str | None = None    # container on umbrel_main_network (decision 15); needs upstream_port
    forward_auth: bool | None = None
    real_ip: bool = False               # tunnel-realip (decision 20); needs external, refuses forward auth
    desktops: bool | None = None        # None: false for ids in desktop_deny (decision 22)

class Defaults(BaseModel):
    internal: bool = True
    external: bool = True
    access: bool = True
    forward_auth: bool = False

class ForwardAuthSettings(BaseModel):
    """Authentik's embedded outpost (decision 14)."""
    address: str        # http://host.docker.internal:<port>/outpost.goauthentik.io/auth/traefik
    outpost_url: str    # http://host.docker.internal:<port>
    response_headers: list[str] = DEFAULT_AUTH_RESPONSE_HEADERS   # the X-authentik-* list

class AccessSettings(BaseModel):
    allowed_emails: list[str] = Field(min_length=1)
    session_duration: str = "24h"
    team_name: str | None = Field(default=None, pattern=r"^[a-z0-9-]+$")   # decision 21

class EdgeConfig(BaseModel):
    """The whole of edge.yaml."""
    domain: str
    proxy_ip: IPvAnyAddress
    defaults: Defaults = Defaults()
    external_deny: list[str] = [
        "home-assistant", "portainer", "arcane", "code-server", "termix",
        "denny-olivetin", "torbrowser", "denny-librewolf", "wireguard",
        "tailscale", "vyrmy-edge", "umbrel",
    ]
    forward_auth_deny: list[str] = ["authentik", "denny-authentik"]   # never behind forward auth by default
    exclude: list[str] = ["mosquitto"]   # apps to ignore entirely (no web UI)
    desktop_deny: list[str] = ["umbrel", "vyrmy-edge"]   # never reachable from a desktop by default
    access: AccessSettings
    forward_auth: ForwardAuthSettings | None = None
    apps: dict[str, AppPolicy] = {}

class Route(BaseModel):
    """One app's resolved desired state."""
    app_id: str
    hostname: str                        # e.g. "jellyfin.example.com"
    upstream: str                        # e.g. "http://host.docker.internal:8096"
    internal: bool
    external: bool
    access: bool
    forward_auth: bool = False
    real_ip: bool = False
    desktops: bool = True

class DesiredState(BaseModel):
    routes: list[Route]                  # sorted by hostname, unique hostnames
    forward_auth: ForwardAuthSettings | None = None   # set when any route is protected
```

`ownership.json`:

```json
{ "version": 2, "unifi_records": { "jellyfin.example.com": "<dns-policy-id>" }, "pending": [] }
```

Rules applied in `desired.py`:

- The subdomain defaults to the app id.
- `external` is forced false for ids in `external_deny` unless `apps.<id>.external` is set explicitly.
- `access` only matters when `external` is true.
- Hostname collisions are a validation error, not a silent overwrite.
- The upstream is `<scheme>://host.docker.internal:<upstream_port or manifest port>`, or `<scheme>://<upstream_host>:<upstream_port>` when `upstream_host` is set. `upstream_host` without `upstream_port` is a validation error, because the manifest port belongs to umbreld's gateway, not the container.
- `forward_auth` defaults to `defaults.forward_auth` (false), or to true when `upstream_host` is set, and is forced false for ids in `forward_auth_deny` unless `apps.<id>.forward_auth` is set explicitly. The built-in list holds `authentik` and `denny-authentik` (the community store id installed on this Umbrel): Authentik serves the login flow, so protecting its own hostname denies or loops every login and locks out every protected app. If any app, or the default, turns it on and there is no top-level `forward_auth` block, the config is rejected with a message naming those apps. `build` checks again, so a protected route is never published without its login.
- `real_ip: true` is rejected on a route that is not external, and on one with forward auth on.
- `desktops` is false for ids in `desktop_deny` unless `apps.<id>.desktops` is set explicitly. `DESKTOP_NETWORKS` is `10.31.0.0/16` in `models.py` and must match the `desk-<n>` subnets in Edge's compose.

## Contracts

**CLI** (`python -m umbrel_edge`):

| Flag | Behaviour |
|---|---|
| (none) | Run the reconcile loop and serve `/healthz` on `:9000`. |
| `--once` | One pass. Exit code 0 on success, 1 if any stage failed. |
| `--dry-run` | Compute and print the diff for every stage. No writes. Implies `--once`. |

**Health endpoint.** `GET /healthz` on `sync:9000`, bridge network only.

- `200` `{"status": "ok", "last_success": "<ISO 8601>", "routes": <int>}` when the last pass succeeded within 5 minutes.
- `503` `{"status": "degraded", "last_success": "<ISO 8601 | null>", "errors": [{"stage": "config|traefik|unifi|cloudflare_tunnel|cloudflare_dns|cloudflare_access|loop", "message": "<str>"}]}` otherwise.

**Traefik dynamic file** (`/data/traefik/dynamic/apps.yml`, written by `sync`, read by Traefik's file provider). One router per route: rule ``Host(`<hostname>`)``, entrypoints `websecure` if internal, then `tunnel` if external (`tunnel-realip` instead when `real_ip`), TLS with certResolver `cloudflare` and domain `*.DOMAIN`. One service per route, with loadBalancer server `<upstream>` and passHostHeader true. Only routes with `internal` or `external` true are written. Companion routers (`outpost-*`, `edge-assets`, `edge-umbrel-fallback`) use the entrypoints of the route they serve. A `real_ip` route lists middleware `realip-<id>` (`headers.customRequestHeaders`: `X-Forwarded-Host: <hostname>`, `X-Forwarded-Proto: https`, and empty `X-Forwarded-Uri`, `X-Forwarded-Method`, `X-Forwarded-Port`, `X-Real-Ip`). A route with `desktops` false lists `no-desktops` first (`ipAllowList.sourceRange`: `0.0.0.0/0` minus `10.31.0.0/16` as CIDRs, plus `::/0`).

**Traefik static flags** added for decisions 19 and 20, each new entrypoint also getting `http.aliasHeadersStrategy=delete` and the three `http.encodedCharacters.allowEncoded*=false` flags `websecure` has:

```
--entrypoints.tunnel.address=10.30.0.2:8443
--entrypoints.tunnel-realip.address=10.30.0.2:8444
--entrypoints.tunnel-realip.forwardedHeaders.trustedIPs=10.30.0.0/24
--entrypoints.tunnel-realip.forwardedHeaders.notAppendXForwardedFor=true
```

Forward auth (decision 14), written only when at least one route has `forward_auth` true:

- One middleware, `authentik`: `forwardAuth` with `address`, `trustForwardHeader: true`, `maxResponseBodySize: 4194304` (as in Authentik's Traefik template) and `authResponseHeaders` from `forward_auth.response_headers`. Field names follow [Traefik's ForwardAuth reference](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/forwardauth/).
- Each protected route's router lists `authentik` first in `middlewares`.
- Each protected hostname gets a router `outpost-<id>`: rule ``Host(`<hostname>`) && PathPrefix(`/outpost.goauthentik.io/`)``, priority 1000 (above any app router, whose priority is its rule length), no middleware, the same entrypoint and TLS as the app router, to one shared service `authentik-outpost` with loadBalancer server `forward_auth.outpost_url` and passHostHeader true.
- When the `umbrel` dashboard route is protected, its middlewares are `authentik` then `launcher-inject`, so the body rewrite never sees a login redirect, and `edge-assets` and `edge-umbrel-fallback` get `authentik` too, so neither is a way round the login.

What the owner sets up in Authentik for each protected app (checked against [Authentik's forward auth docs](https://docs.goauthentik.io/add-secure-apps/providers/proxy/forward_auth/) and [embedded outpost docs](https://docs.goauthentik.io/add-secure-apps/outposts/embedded/), 5 October 2026):

- A Proxy provider in "Forward auth (single application)" mode with External host `https://<app>.DOMAIN`, or one provider in "Forward auth (domain level)" mode with cookie domain `DOMAIN` for all of them (one login for every app, but no per-app policies).
- An application using that provider, with the policy bindings you want.
- The provider assigned to the embedded outpost (Applications, Outposts), and the outpost's `authentik_host` set to Authentik's full URL.
- Umbrel app auth turned off for the protected app (`umbreld client apps.setSettings.mutate --appId <id> --appProxyAuthEnabled false`), otherwise umbrelOS sends the visitor to its own login on port 2000 after Authentik's ([umbrel#2242](https://github.com/getumbrel/umbrel/issues/2242)).
- The embedded outpost answers on Authentik's own ports (9000 HTTP, 9443 HTTPS). Through `host.docker.internal:<port>`, `address` and `outpost_url` reach Authentik's app_proxy port instead, so Umbrel app auth has to be off for Authentik itself as well; it has its own login. Check on the first protected app that the app_proxy passes the `X-Forwarded-*` headers through unchanged, since the outpost uses them to know which host it is protecting.

**UniFi Integration API** (base `https://<UNIFI_HOST>/proxy/network/integration/v1`, header `X-API-KEY`). Create, list and delete DNS policies under `sites/{siteId}/dns/policies`, type A, domain `<hostname>`, IPv4 `proxy_ip`. Confirm exact field names against [developer.ui.com](https://developer.ui.com/network/v10.1.84/creatednspolicy) in task 003. `sync` deletes only ids present in `ownership.json`.

**Cloudflare API** (base `https://api.cloudflare.com/client/v4`, bearer `CF_API_TOKEN`):

- Tunnel ingress: `PUT /accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations` with `config.ingress`. There is one entry per external route: `{"hostname": "<h>", "service": "https://traefik:8443", "originRequest": {"originServerName": "<h>"}}`, with `:8444` for a `real_ip` route and, when `access.team_name` is set and the route has `access` true, `"access": {"required": true, "teamName": "<team>", "audTag": ["<aud>"]}` inside `originRequest`. The list ends with `{"service": "http_status:404"}`. The whole list is replaced on each change, so this tunnel must not be edited by hand.
- DNS: `GET/POST/PATCH/DELETE /zones/{zone_id}/dns_records`. Each record is a CNAME `<h>` → `<tunnel_id>.cfargotunnel.com`, proxied, with comment `managed-by=umbrel-edge`. Records without that comment are never touched.
- Access: apps via `GET/POST/PUT/DELETE /accounts/{account_id}/access/apps`, policies via the same verbs on `/accounts/{account_id}/access/policies`. There is one reusable allow policy, named `umbrel-edge:allowed-emails`, with one `include` rule per address in `access.allowed_emails`. Each app is named `umbrel-edge:<h>`, type `self_hosted`, domain `<h>`, session duration from config, and references that policy as `"policies": [{"id": "<policy id>", "precedence": 1}]`. Cloudflare now recommends reusable policies and does not allow app-scoped ones on new apps, so the policy is not inlined. Order within a pass: Access create and update, tunnel ingress, DNS, Access delete. A name is therefore never public without its Access app, and if the Access stage fails, new access-true names get neither an ingress entry nor DNS.

**Desktop slot** (`vyrmy-desktop-<n>/docker-compose.yml`, decisions 17 and 18):

- `desktop`: on `desk` only, at `10.31.<n>.10`. Environment adds `EGRESS_PROXY: http://10.31.<n>.3:3128`, `EGRESS_DIRECT_DOMAIN: bebitwise.dev`, `EGRESS_DIRECT_HOST: 10.31.<n>.2` and `SELKIES_ENABLE_DUAL_MODE: "false|locked"`.
- `egress`: `ubuntu/squid` pinned by digest, `user: "13:13"`, `read_only: true`, `tmpfs: [/tmp]`, `cap_drop: [ALL]`, `no-new-privileges`, on `desk` at `10.31.<n>.3` and on `egress`. Its `command` writes this configuration and runs `squid -N`:

  ```
  http_port 3128
  visible_hostname egress
  pid_filename none
  pinger_enable off
  coredump_dir /tmp
  cache deny all
  cache_mem 0 MB
  cache_log /dev/stderr
  cache_store_log none
  via off
  forwarded_for delete
  shutdown_lifetime 1 seconds
  acl clients src 10.31.<n>.10/32
  acl never_dst dst 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.0.2.0/24 192.88.99.0/24 192.168.0.0/16 198.18.0.0/15 198.51.100.0/24 203.0.113.0/24 224.0.0.0/3
  acl never_dst6 dst ::/128 ::1/128 ::ffff:0:0/96 64:ff9b::/96 64:ff9b:1::/48 100::/64 2001::/32 2001:db8::/32 2002::/16 fc00::/7 fe80::/10 fec0::/10 ff00::/8
  acl v6_literal dstdom_regex :
  acl ok_ports port 80 443 1025-65535
  acl refused http_status 403
  access_log stdio:/dev/stdout refused
  http_access deny !clients
  http_access deny !ok_ports
  http_access deny never_dst
  http_access deny never_dst6
  http_access deny v6_literal
  http_access allow all
  ```

  `v6_literal` is there because Squid in a container without IPv6 drops the `never_dst6` lines as empty ACLs, which would leave IPv6 literals unrefused.
- `placeholder`: a pinned `nginx:alpine-slim` on `default`, serving one page saying the desktop opens at its own address. `app_proxy` points at it, so Umbrel's login still guards the slot's port.
- Networks: `desk: {external: true, name: vyrmy-edge_desk-<n>}`, `egress: {driver: bridge}`.
- Manifest: `dependencies: [vyrmy-edge]`.

**Error shape inside `sync`.** Every client raises `StageError(stage: str, message: str, retriable: bool)`. `loop.py` catches it per stage and records it for `/healthz`. HTTP 429 and 5xx responses are retriable with exponential backoff (3 attempts). 4xx responses are not. A POST is never repeated once it may have reached the server (a timeout after sending, or a 5xx), because a repeat could create the resource twice; the next pass re-plans instead.

## Risks and open questions

- **Umbrel app auth on custom hostnames.** With app_proxy auth on, umbrelOS 2.0 redirects to its own login on port 2000 with its own certificate ([umbrel#2242](https://github.com/getumbrel/umbrel/issues/2242)). That is the one known source of certificate errors: every hostname Traefik and Cloudflare serve is covered by `*.DOMAIN`, but port 2000 is not. Task 006 tests this app by app. The fix is to turn app auth off for that app with `umbreld client apps.setSettings.mutate --appId <id> --appProxyAuthEnabled false`. Externally, Access then covers the app. Internally, anyone on Main could open it without a login unless `apps.<id>.forward_auth` puts Authentik in front of it (decision 14), so do it per app and list the ones affected. The per-app findings (own login, risk without Umbrel auth, SSO support) are in [app-auth-audit.md](app-auth-audit.md). About 30 apps have no real login of their own and stay behind Umbrel auth until Authentik forward auth is in front of them. The built-in `external_deny` names Tor Browser as `tor-browser`, but its app id is `torbrowser`, so `edge.yaml` lists it explicitly.
- **Forward auth protects only the hostname path.** With Umbrel app auth off, a protected app is still reachable with no login at `192.168.10.2:<port>` from any network the firewall lets reach the Umbrel (today that includes IoT, through the "IoT to Umbrel" rule). The protection is only complete once direct access to app ports on the Umbrel is blocked at the UniFi firewall, allowing only what is needed. Apps that publish a port or use host networking (see [app-auth-audit.md](app-auth-audit.md)) are open on those ports whatever Traefik does.
- **The launcher script depends on the dashboard's link format.** If an umbrelOS update stops building links as `<host>:<port>`, apps open at the old address again until `launcher.js` is updated. The dashboard's Content-Security-Policy and response compression could also block or garble the injected tag. Task 007 checks both before relying on it.
- **The app-data layout is assumed.** Task 002 confirms that each `app-data/<id>/umbrel-app.yml` exists and carries `port`. If it doesn't, discovery falls back to parsing `app_proxy` `PORT` from the app's `docker-compose.yml`.
- **The installed-apps list is umbreld's internal store.** `apps` in `umbrel.yaml` is not a published interface. If a future umbrelOS moves or renames it, discovery warns on every pass and goes back to treating every app-data folder as installed, leftovers included, until discovery is updated.
- **Discovery sees the whole Umbrel data directory.** The app mounts `${APP_DATA_DIR}/../..` read-only at `/umbrel`. `sync` can therefore read every app's data folder, everyone's files under `home` and `members`, and `umbrel.yaml`, which also holds the owner's password hash and TOTP secret. It reads only `umbrel.yaml` and each `app-data/<id>/umbrel-app.yml`. A narrower mount does not work: `umbrel.yaml` sits at the top of that directory and is replaced by rename (decision 11).
- **Host facts (recorded 1 October 2026, Docker versions 5 October 2026).** umbrelOS 2.0 on the Dell, so 443 on the host is taken, which this design avoids. The LAN interface is `enp2s0`, the macvlan parent. Docker Engine 28.5.0 (API 1.51) and Docker Compose v5.5.1, so per-network `mac_address` (Engine 25+) and `gw_priority` (Engine 28+, Compose 2.33.1+) are available.
- **The sync image is pulled from GHCR anonymously.** The `umbrel-edge-sync` package must be public. A new GHCR package can start private even when its repo is public, so check after the first build.
- **UniFi API field names** for DNS policies are taken from third-party clients. Task 003 checks them against the official docs before writing the client.
- **Macvlan MAC and IP stability.** The compose file pins `mac_address` and `ipv4_address` on the `lan` network entry. A service-level `mac_address` lands on the first network instead (here `umbrel_main_network`), which is what happened in the first deployment. UniFi also gets a reservation, so the gateway never hands `.4` to anything else.
- **What an app update delivers.** umbrelOS copies only `docker-compose.yml`, `*.template`, `exports.sh`, `torrc`, `hooks` and `umbrel-app.yml` into app data on an update, and offers an update only when `version` in `umbrel-app.yml` changes. Any other file in the app folder arrives only at first install, so Traefik's static configuration lives in compose `command` flags. Never uninstall and reinstall to pick up a change: uninstalling deletes app data, including `acme/acme.json`, `edge.yaml` and `secrets.env`.
- **Traefik does not trust `X-Forwarded-For` from cloudflared, on purpose.** Cloudflare appends the visitor's IP to any `X-Forwarded-For` the client sent and does not let rules rewrite it, and Authentik takes the leftmost entry (`authentik/root/middleware.py`, 2025.10.2). Trusting it would let an external client choose the IP Authentik sees. Tunnelled requests therefore reach apps with cloudflared's bridge address. Brute-force protection for `auth.bebitwise.dev` comes from a Cloudflare rate-limiting rule keyed on the real client IP, plus MFA. Authentik restricts the superuser to requests without `Cf-Connecting-Ip`, which Cloudflare always sets on tunnelled requests. If per-IP data inside Authentik is ever needed, the safe route is a middleware on the auth route that replaces `X-Forwarded-For` with `Cf-Connecting-Ip` for requests from the edge bridge only.
- **Header aliases are stripped on `websecure`.** PHP and CGI backends read `X_authentik_email` and `X-Authentik-Email` as the same variable, so a client could otherwise spoof the identity headers forward auth sets. `--entryPoints.websecure.http.aliasHeadersStrategy=delete` (Traefik 3.7.12+) removes every request header whose name has anything but letters, digits and dashes. An app that relies on an underscore header name would stop receiving it.
- **Forward auth must reach Authentik's container directly.** Found on 7 October 2026: umbrelOS's app proxy (the Umbrel app port, 3973 for `denny-authentik`) drops `X-Forwarded-Host`, so the embedded outpost cannot tell which app a forward-auth check is for and answers 404. `forward_auth.address` and `outpost_url` therefore use `http://denny-authentik_server_1:9000` on `umbrel_main_network`. Authentik's Django API rejects Host headers containing underscores, so anything that calls Authentik's API by container name (an outpost's `authentik_host`, for example) must use `https://auth.bebitwise.dev` instead. The forward-auth endpoint is served by the Go outpost from `X-Forwarded-Host`, which is expected to accept the container name; the first protected app confirms it.
- **Apps on the Umbrel reach Traefik through network aliases, not `192.168.10.4`.** A macvlan child cannot talk to its parent host, so nothing on the Umbrel can reach `.4`. Instead, Traefik's endpoint on `umbrel_main_network` carries Docker network aliases (`auth.bebitwise.dev` today), and every app container on that network resolves those names to Traefik through Docker's embedded DNS, then gets the same certificate and routes as a LAN client. OIDC apps need this for back-channel calls to Authentik. Containers in host network mode (Home Assistant, Matter Server, OTBR, Homebridge) do not use Docker's DNS, so they cannot use the aliases. Aliases are static, so a new name needs an Edge release. Release 0.2.1 tried a host macvlan shim (`lan-shim`, `192.168.10.5`) instead; it never passed traffic and needed `NET_ADMIN` on the host, so 0.3.0 replaced it with a one-shot service that removed the `edge-shim` interface. 0.4.1 drops that service, which ran with `NET_ADMIN` on the host network at every start.
- **umbrel_main_network is fixed by umbrelOS.** umbreld hardcodes `10.21.0.0/16` with gateway `10.21.0.1`, and its own Tor hidden services depend on that address. If a future umbrelOS moves it, `host.docker.internal` has to follow.
- **Apps that need raw TCP or UDP** (Transmission peers, WireGuard, Mosquitto) are not HTTP. They get no route, and only their web UI is proxied. Mosquitto has no web UI and is excluded by default.
- **The domain is `bebitwise.dev`, shared with other uses.** `DOMAIN` above means `bebitwise.dev`, so apps live at `<app>.bebitwise.dev`. `home.bebitwise.dev` was the first choice, but `<app>.home.bebitwise.dev` is two levels deep and Cloudflare's free certificate only covers one level; Advanced Certificate Manager at $10 a month would fix that. The apex already serves a site through Cloudflare, so `sync` must never touch a record it did not create. A clash with an existing name (for example an app with id `www`) is logged as a conflict and that app gets no external route until it is given another subdomain in `edge.yaml`. `.dev` is on the HSTS preload list, so browsers refuse plain HTTP on every name. Traefik's port 80 only redirects, so nothing changes.
- **Authentik is public, without Cloudflare Access (13 October 2026).** `auth.bebitwise.dev` is external with `access: false`. Every forward-auth and OIDC login from outside sends the browser there, phone apps that use OIDC (KitchenOwl) fetch tokens from it directly and cannot pass an Access login, and Access can later use Authentik as its identity provider, which would be circular behind Access. Its guards: MFA for every user, a Cloudflare rate-limiting rule keyed on the visitor's IP, and an Authentik policy that keeps superusers to requests without `Cf-Connecting-Ip` (see the `X-Forwarded-For` entry above). The owner sets all three up by hand (task 008).
- **A desktop holds every session its person signs in to.** Whoever gets past its Authentik application (hostname) or Umbrel's login (port) has their email, WhatsApp and Signal. So each desktop's Authentik application is bound to one user and needs MFA, and the Umbrel app is shared with that person only. The owner can still open every desktop at its port, as the umbrelOS owner can open every app. Each slot's compose file turns off Selkies' command messages, file transfers, shared and extra-player sessions, and cross-site websocket origins, none of which a one-person desktop needs. Browser data is stored unencrypted in the app's data folder (Chromium uses `--password-store=basic` in Webtop), which `sync` can read through its `/umbrel` mount (see "Discovery sees the whole Umbrel data directory").
- **Chromium and Signal run with `--no-sandbox` on the desktops.** The first release set `seccomp=unconfined` so their own per-process sandboxes could work, but on 24 October 2026 both crashed at start on the Umbrel with `sandbox/linux/services/credentials.cc:131 ... Permission denied`: the kernel refuses the unprivileged user namespaces those sandboxes need. Docker's seccomp filter is back on, so Webtop's `wrapped-chromium` and `wrapped-signal` fall back to `--no-sandbox`, as Umbrel's own Chromium app does. A page that exploits the browser then runs as the desktop's `abc` user with that person's sessions, with no root (sudo is disabled) and Docker's default confinement around it. Chromium's weekly rebuild is the main defence. Getting the sandboxes back would mean allowing unprivileged user namespaces on the host, which is a host change outside this app.
- **A desktop's stream goes through Cloudflare's tunnel.** Selkies sends the screen as video over a websocket. Cloudflare's terms restrict serving video on the free plan; a household remote desktop is small next to what that clause targets, but if Cloudflare objects, the fallback is the WireGuard VPN, where the same hostname resolves to `192.168.10.4`.
- **Edge must be updated before the desktops that need its networks (decisions 17 and 18).** A desktop whose `vyrmy-edge_desk-<n>` network does not exist fails to start; umbreld retries three times, then shows the app as stopped until it is opened again. Docker keeps the networks across reboots and app restarts, so this bites only on the first rollout, after Edge is reinstalled, or after `docker network prune`. Release notes for the desktop slots say to update Edge first.
- **A shipped `desk-<n>` definition is frozen.** Compose cannot recreate a network a running container is attached to, so changing a `desk-<n>` subnet or option makes Edge's next start fail until every desktop is stopped. Adding a new `desk-<n>` is fine.
- **The new subnets must not clash with home networks.** `10.30.0.0/24` and `10.31.0.0/16` become routes on the Umbrel. If a VLAN, the VPN or Tailscale uses either range, pick other ones before task 009 and 012 ship (the audit script's host addresses section shows the routes).
- **Docker on the Umbrel is 28.5; the network tests ran on 29.9.** `inhibit_ipv4`, the embedded DNS refusing external names on internal networks, and the isolation results are re-checked on the Umbrel in task 012's and 014's acceptance.
- **Squid is pinned by digest and nothing updates it.** Refresh it by hand with the desktop releases until the weekly workflow does.
- **Home Assistant is public too, without Access (24 October 2026).** This overrides decision 9's VPN-only stance for one reason: the claude.ai connector to Home Assistant's MCP server (`/api/mcp`) connects from Anthropic's servers and signs in with Home Assistant's own OAuth, which neither Access nor forward auth can sit in front of. The companion app then works away from home without the VPN as well. Home Assistant's login is the only gate, so every Home Assistant user needs MFA, and the Cloudflare rate-limiting rule also counts `/auth/login_flow`. Home Assistant's own IP ban is no help here and must stay off (`login_attempts_threshold` unset): umbreld's gateway replaces `X-Forwarded-For` with Traefik's address (`app-gateway.ts`), so every visitor, at home or away, reaches Home Assistant from one IP and a ban would lock everyone out.
