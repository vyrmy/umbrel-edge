"""Task 009: the tunnel has its own entrypoint, and each route sits on the ones its flags pick."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any

import pytest
import yaml

from umbrel_edge import traefik_writer
from umbrel_edge.desired import build
from umbrel_edge.discovery import discover
from umbrel_edge.models import AppPolicy, EdgeConfig, Route

COMPOSE = Path(__file__).parents[2] / "vyrmy-edge" / "docker-compose.yml"
ADDRESS = "http://host.docker.internal:9000/outpost.goauthentik.io/auth/traefik"
OUTPOST = "http://host.docker.internal:9000"
# The flags that make an entrypoint safe to face clients; every app-facing one needs them all.
HARDENING = {
    "http.aliasheadersstrategy": "delete",
    "http.encodedcharacters.allowencodedbackslash": "false",
    "http.encodedcharacters.allowencodednullcharacter": "false",
    "http.encodedcharacters.allowencodedsemicolon": "false",
}


def _compose() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return doc


def _flags() -> dict[str, str]:
    flags: dict[str, str] = {}
    for arg in _compose()["services"]["traefik"]["command"]:
        key, _, value = arg.removeprefix("--").partition("=")
        flags[key.lower()] = value
    return flags


def _route(*, internal: bool, external: bool) -> Route:
    return Route(
        app_id="app",
        hostname="app.bebitwise.dev",
        upstream="http://host.docker.internal:80",
        internal=internal,
        external=external,
        access=external,
    )


def _http(app_data: Path, **apps: AppPolicy) -> dict[str, Any]:
    config = EdgeConfig.model_validate(
        {
            "domain": "bebitwise.dev",
            "proxy_ip": "192.168.10.4",
            "access": {"allowed_emails": ["me@example.com"]},
            "forward_auth": {"address": ADDRESS, "outpost_url": OUTPOST},
            "apps": {k: v.model_dump(exclude_unset=True) for k, v in apps.items()},
        }
    )
    state = build(discover(app_data), config)
    doc: dict[str, Any] = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]
    return doc


@pytest.mark.parametrize(
    ("internal", "external", "expected"),
    [
        (True, False, ["websecure"]),
        (False, True, ["tunnel"]),
        (True, True, ["websecure", "tunnel"]),
    ],
)
def test_flags_pick_the_entrypoints(internal: bool, external: bool, expected: list[str]) -> None:
    assert traefik_writer.entry_points(_route(internal=internal, external=external)) == expected


def test_external_only_route_does_not_answer_at_home(app_data: Path) -> None:
    routers = _http(app_data, jellyfin=AppPolicy(internal=False, forward_auth=True))["routers"]
    assert routers["app-jellyfin"]["entryPoints"] == ["tunnel"]
    # The login must complete wherever the app answers.
    assert routers["outpost-jellyfin"]["entryPoints"] == ["tunnel"]


def test_internal_only_route_does_not_answer_through_the_tunnel(app_data: Path) -> None:
    routers = _http(app_data)["routers"]
    # home-assistant is in external_deny.
    assert routers["app-home-assistant"]["entryPoints"] == ["websecure"]


def test_dashboard_companions_copy_the_dashboard_route(app_data: Path) -> None:
    routers = _http(app_data, umbrel=AppPolicy(external=True, forward_auth=True))["routers"]
    expected = routers["app-umbrel"]["entryPoints"]
    assert expected == ["websecure", "tunnel"]
    for name in ("edge-assets", "edge-umbrel-fallback", "outpost-umbrel"):
        assert routers[name]["entryPoints"] == expected


def test_umbrels_own_dashboard_router_stays_on_its_entrypoint(app_data: Path) -> None:
    assert _http(app_data)["routers"]["dashboard"]["entryPoints"] == ["dashboard"]


@pytest.mark.parametrize("entrypoint", ["websecure", "tunnel", "tunnel-realip"])
def test_every_app_entrypoint_is_hardened(entrypoint: str) -> None:
    flags = _flags()
    for flag, value in HARDENING.items():
        assert flags.get(f"entrypoints.{entrypoint}.{flag}") == value, flag


def test_tunnel_listens_only_on_traefiks_edge_address() -> None:
    compose = _compose()
    host, _, port = _flags()["entrypoints.tunnel.address"].rpartition(":")
    assert port == "8443"
    assert host == compose["services"]["traefik"]["networks"]["edge"]["ipv4_address"]
    (pool,) = compose["networks"]["edge"]["ipam"]["config"]
    assert IPv4Address(host) in IPv4Network(pool["subnet"])


def test_tunnel_trusts_no_forwarded_headers() -> None:
    # cloudflared's X-Forwarded-For is set by Cloudflare's edge, not by a proxy we run.
    assert not any(k.startswith("entrypoints.tunnel.forwardedheaders") for k in _flags())
