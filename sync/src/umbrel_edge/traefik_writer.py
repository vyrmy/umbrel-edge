"""DesiredState -> Traefik dynamic YAML, written atomically and only when it changes."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from umbrel_edge.models import DesiredState, Route, StageError

FILE_NAME = "apps.yml"
# Docker networks only. The LAN (192.168.0.0/16) cannot reach the dashboard entrypoint.
DASHBOARD_SOURCES = ["10.0.0.0/8", "172.16.0.0/12"]


def render(state: DesiredState, domain: str) -> str:
    routers: dict[str, Any] = {
        "dashboard": {
            "rule": "PathPrefix(`/`)",
            "entryPoints": ["dashboard"],
            "service": "api@internal",
            "middlewares": ["docker-only"],
        }
    }
    services: dict[str, Any] = {}
    transports: dict[str, Any] = {}
    for route in state.routes:
        name = _name(route)
        routers[name] = {
            "rule": f"Host(`{route.hostname}`)",
            "entryPoints": ["websecure"],
            "service": name,
            "tls": {
                "certResolver": "cloudflare",
                "domains": [{"main": domain, "sans": [f"*.{domain}"]}],
            },
        }
        service: dict[str, Any] = {
            "loadBalancer": {"servers": [{"url": route.upstream}], "passHostHeader": True}
        }
        if route.upstream.startswith("https://"):
            # App-internal self-signed certificates on the host; traffic never leaves the Dell.
            transports["upstream-self-signed"] = {"insecureSkipVerify": True}
            service["loadBalancer"]["serversTransport"] = "upstream-self-signed"
        services[name] = service
    http: dict[str, Any] = {
        "routers": routers,
        "services": services,
        "middlewares": {"docker-only": {"ipAllowList": {"sourceRange": DASHBOARD_SOURCES}}},
    }
    if transports:
        http["serversTransports"] = transports
    header = "# Written by umbrel-edge sync. Do not edit: changes are overwritten.\n"
    return header + yaml.safe_dump({"http": http}, sort_keys=True, default_flow_style=False)


def write(state: DesiredState, domain: str, directory: Path) -> bool:
    """Returns True when the file changed."""
    content = render(state, domain)
    target = directory / FILE_NAME
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_text(encoding="utf-8") == content:
            return False
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".apps.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise StageError("traefik", f"cannot write {target}: {exc}", retriable=True) from exc
    return True


def _name(route: Route) -> str:
    return "app-" + re.sub(r"[^a-z0-9-]", "-", route.app_id.lower())
