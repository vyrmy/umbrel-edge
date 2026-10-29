"""Task 015: desktops cannot open the dashboard or Edge's own page (decision 22)."""

from __future__ import annotations

from ipaddress import IPv4Network, ip_address, ip_network
from pathlib import Path
from typing import Any

import yaml

from umbrel_edge import traefik_writer
from umbrel_edge.desired import build
from umbrel_edge.discovery import discover
from umbrel_edge.models import DESKTOP_NETWORKS, AppPolicy, EdgeConfig

COMPOSE = Path(__file__).resolve().parents[2] / "vyrmy-edge" / "docker-compose.yml"
ADDRESS = "http://host.docker.internal:9000/outpost.goauthentik.io/auth/traefik"
OUTPOST = "http://host.docker.internal:9000"


def _config(*, forward_auth: bool = False, **apps: AppPolicy) -> EdgeConfig:
    data: dict[str, Any] = {
        "domain": "bebitwise.dev",
        "proxy_ip": "192.168.10.4",
        "access": {"allowed_emails": ["me@example.com"]},
        "apps": {k: v.model_dump(exclude_unset=True) for k, v in apps.items()},
    }
    if forward_auth:
        data["forward_auth"] = {"address": ADDRESS, "outpost_url": OUTPOST}
    return EdgeConfig.model_validate(data)


def _http(config: EdgeConfig, app_data: Path, *, dashboard: bool = True) -> dict[str, Any]:
    state = build(discover(app_data, include_dashboard=dashboard), config)
    doc: dict[str, Any] = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]
    return doc


def _source_range() -> list[str]:
    return list(traefik_writer.NOT_DESKTOPS)


def test_source_range_never_admits_a_desktop() -> None:
    v4 = [IPv4Network(c) for c in _source_range() if ":" not in c]
    assert not any(net.overlaps(DESKTOP_NETWORKS) for net in v4)
    for desktop in ("10.31.0.0", "10.31.1.10", "10.31.2.2", "10.31.255.255"):
        assert not any(ip_address(desktop) in net for net in v4)


def test_source_range_admits_everything_else() -> None:
    nets = [ip_network(c) for c in _source_range()]
    v4 = [n for n in nets if isinstance(n, IPv4Network)]
    for wanted in ("192.168.10.0/24", "10.21.0.0/16", "10.30.0.0/24", "8.8.8.8/32"):
        assert any(IPv4Network(wanted).subnet_of(n) for n in v4)
    assert sum(n.num_addresses for n in v4) == 2**32 - DESKTOP_NETWORKS.num_addresses
    assert ip_network("::/0") in nets


def test_desk_networks_in_edge_compose_are_desktop_networks() -> None:
    networks = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["networks"]
    desks = {k: v for k, v in networks.items() if k.startswith("desk-")}
    assert desks
    for spec in desks.values():
        for pool in spec["ipam"]["config"]:
            assert IPv4Network(pool["subnet"]).subnet_of(DESKTOP_NETWORKS)


def test_dashboard_routers_refuse_desktops_first(app_data: Path) -> None:
    doc = _http(_config(), app_data)
    assert doc["middlewares"]["no-desktops"] == {"ipAllowList": {"sourceRange": _source_range()}}
    routers = doc["routers"]
    assert routers["app-umbrel"]["middlewares"] == ["no-desktops", "launcher-inject"]
    assert routers["edge-assets"]["middlewares"] == ["no-desktops"]
    assert routers["edge-umbrel-fallback"]["middlewares"] == ["no-desktops"]
    assert "middlewares" not in routers["app-jellyfin"]


def test_refusal_comes_before_the_login(app_data: Path) -> None:
    config = _config(forward_auth=True, umbrel=AppPolicy(forward_auth=True))
    routers = _http(config, app_data)["routers"]
    assert routers["app-umbrel"]["middlewares"] == ["no-desktops", "authentik", "launcher-inject"]
    assert routers["outpost-umbrel"]["middlewares"] == ["no-desktops"]
    assert routers["edge-assets"]["middlewares"] == ["no-desktops", "authentik"]


def test_no_middleware_when_no_route_needs_it(app_data: Path) -> None:
    doc = _http(_config(), app_data, dashboard=False)
    assert "no-desktops" not in doc["middlewares"]
    assert not any("no-desktops" in r.get("middlewares", []) for r in doc["routers"].values())


def test_policy_overrides_the_deny_list(app_data: Path) -> None:
    config = _config(umbrel=AppPolicy(desktops=True), jellyfin=AppPolicy(desktops=False))
    routers = _http(config, app_data)["routers"]
    assert routers["app-umbrel"]["middlewares"] == ["launcher-inject"]
    assert routers["app-jellyfin"]["middlewares"] == ["no-desktops"]


def test_edge_itself_is_denied_by_default() -> None:
    assert "vyrmy-edge" in EdgeConfig.model_fields["desktop_deny"].default
