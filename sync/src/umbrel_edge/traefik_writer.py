"""DesiredState -> Traefik dynamic YAML, written atomically and only when it changes."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from umbrel_edge.discovery import DASHBOARD
from umbrel_edge.models import DesiredState, Route, StageError

FILE_NAME = "apps.yml"
# umbrel_main_network only: umbreld proxies the app page to :8080 from 10.21.0.1.
DASHBOARD_SOURCES = ["10.21.0.0/16"]
# sync's own server, reached over the edge bridge (container names use the underscore form).
SYNC_URL = "http://vyrmy-edge_sync_1:9000"
LAUNCHER_PATH = "/__edge/"
LAUNCHER_TAG = f'<script src="{LAUNCHER_PATH}launcher.js"></script>'
# Wins over the umbrel router, whose rule is shorter.
ASSETS_PRIORITY = 1000
# Loses to the umbrel router (priority = rule length) while that router works.
FALLBACK_PRIORITY = 1


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
            "tls": _tls(domain),
        }
        service: dict[str, Any] = {
            "loadBalancer": {"servers": [{"url": route.upstream}], "passHostHeader": True}
        }
        if route.upstream.startswith("https://"):
            # App-internal self-signed certificates on the host; traffic never leaves the Dell.
            transports["upstream-self-signed"] = {"insecureSkipVerify": True}
            service["loadBalancer"]["serversTransport"] = "upstream-self-signed"
        services[name] = service
    middlewares: dict[str, Any] = {
        "docker-only": {"ipAllowList": {"sourceRange": DASHBOARD_SOURCES}}
    }
    dashboard = next((r for r in state.routes if r.app_id == DASHBOARD.id), None)
    if dashboard is not None:
        # Task 007: the dashboard route only. Its HTML gets the launcher tag, and
        # /__edge/ goes to sync, which serves launcher.js.
        routers[_name(dashboard)]["middlewares"] = ["launcher-inject"]
        routers["edge-assets"] = {
            "rule": f"Host(`{dashboard.hostname}`) && PathPrefix(`{LAUNCHER_PATH}`)",
            "priority": ASSETS_PRIORITY,
            "entryPoints": ["websecure"],
            "service": "edge-sync",
            "tls": _tls(domain),
        }
        # If the plugin fails to load, Traefik drops the umbrel router and this one takes
        # over, so the dashboard loses only the launcher.
        routers["edge-umbrel-fallback"] = {
            "rule": f"Host(`{dashboard.hostname}`)",
            "priority": FALLBACK_PRIORITY,
            "entryPoints": ["websecure"],
            "service": _name(dashboard),
            "tls": _tls(domain),
        }
        services["edge-sync"] = {"loadBalancer": {"servers": [{"url": SYNC_URL}]}}
        middlewares["launcher-inject"] = {
            "plugin": {
                "rewrite-body": {
                    "rewrites": [{"regex": "</head>", "replacement": LAUNCHER_TAG + "</head>"}],
                    "monitoring": {"methods": ["GET"], "types": ["text/html"]},
                    "lastModified": True,
                }
            }
        }
    http: dict[str, Any] = {
        "routers": routers,
        "services": services,
        "middlewares": middlewares,
    }
    if transports:
        http["serversTransports"] = transports
    header = "# Written by umbrel-edge sync. Do not edit: changes are overwritten.\n"
    return header + yaml.safe_dump({"http": http}, sort_keys=True, default_flow_style=False)


def _tls(domain: str) -> dict[str, Any]:
    return {
        "certResolver": "cloudflare",
        "domains": [{"main": domain, "sans": [f"*.{domain}"]}],
    }


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
