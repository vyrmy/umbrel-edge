"""Pure function: manifests + edge.yaml -> desired state. No I/O."""

from __future__ import annotations

import logging
import re

from umbrel_edge.models import (
    SUBDOMAIN_RE,
    AppManifest,
    AppPolicy,
    DesiredState,
    EdgeConfig,
    Route,
    StageError,
)

log = logging.getLogger(__name__)

_SUBDOMAIN = re.compile(SUBDOMAIN_RE)


def build(manifests: list[AppManifest], config: EdgeConfig) -> DesiredState:
    routes: list[Route] = []
    owner: dict[str, str] = {}
    for manifest in manifests:
        if manifest.id in config.exclude:
            continue
        policy = config.apps.get(manifest.id, AppPolicy())
        subdomain = policy.subdomain or manifest.id
        if not _SUBDOMAIN.fullmatch(subdomain):
            log.warning("skipping %s: %r is not a valid hostname label", manifest.id, subdomain)
            continue
        route = _route(manifest, policy, subdomain, config)
        if not (route.internal or route.external):
            continue
        if route.hostname in owner:
            raise StageError(
                "config",
                f"{route.hostname} is claimed by both {owner[route.hostname]} and {manifest.id}",
            )
        owner[route.hostname] = manifest.id
        routes.append(route)
    if mixed := sorted(r.app_id for r in routes if r.real_ip and r.forward_auth):
        # Validation runs on load; a config changed afterwards must still fail closed.
        raise StageError("config", f"real_ip and forward_auth are both on for {', '.join(mixed)}")
    protected = sorted(r.app_id for r in routes if r.forward_auth)
    if protected and config.forward_auth is None:
        # Fail closed: never publish a route without the login it asked for.
        raise StageError(
            "config",
            f"forward_auth is on for {', '.join(protected)} but there is no forward_auth block",
        )
    return DesiredState(
        routes=sorted(routes, key=lambda r: r.hostname),
        forward_auth=config.forward_auth if protected else None,
    )


def _route(manifest: AppManifest, policy: AppPolicy, subdomain: str, config: EdgeConfig) -> Route:
    d = config.defaults
    internal = d.internal if policy.internal is None else policy.internal
    external = config.external_for(manifest.id)
    access = (d.access if policy.access is None else policy.access) and external
    if policy.desktops is not None:
        desktops = policy.desktops
    else:
        desktops = manifest.id not in config.desktop_deny
    host = policy.upstream_host or "host.docker.internal"
    port = policy.upstream_port or manifest.port
    return Route(
        app_id=manifest.id,
        hostname=f"{subdomain}.{config.domain}",
        upstream=f"{policy.upstream_scheme}://{host}:{port}",
        internal=internal,
        external=external,
        access=access,
        forward_auth=config.forward_auth_for(manifest.id),
        desktops=desktops,
        real_ip=policy.real_ip and external,
    )
