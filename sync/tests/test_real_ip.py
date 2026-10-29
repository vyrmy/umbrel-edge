"""Task 010: Home Assistant sees visitors' real addresses through tunnel-realip (decision 20)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from umbrel_edge import traefik_writer
from umbrel_edge.cloudflare import build_ingress
from umbrel_edge.desired import build
from umbrel_edge.discovery import discover
from umbrel_edge.models import AppPolicy, EdgeConfig, StageError

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "real-ip-route.yml"
COMPOSE = Path(__file__).parents[2] / "vyrmy-edge" / "docker-compose.yml"
ADDRESS = "http://host.docker.internal:9000/outpost.goauthentik.io/auth/traefik"
OUTPOST = "http://host.docker.internal:9000"
HA = AppPolicy(external=True, real_ip=True)


def _config(apps: dict[str, AppPolicy], *, forward_auth: bool = False) -> EdgeConfig:
    data: dict[str, Any] = {
        "domain": "bebitwise.dev",
        "proxy_ip": "192.168.10.4",
        "access": {"allowed_emails": ["me@example.com"]},
        "apps": {k: v.model_dump(exclude_unset=True) for k, v in apps.items()},
    }
    if forward_auth:
        data["forward_auth"] = {"address": ADDRESS, "outpost_url": OUTPOST}
    return EdgeConfig.model_validate(data)


def _http(config: EdgeConfig, app_data: Path) -> dict[str, Any]:
    state = build(discover(app_data), config)
    doc: dict[str, Any] = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]
    return doc


def _flags() -> dict[str, str]:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    flags: dict[str, str] = {}
    for arg in compose["services"]["traefik"]["command"]:
        key, _, value = arg.removeprefix("--").partition("=")
        flags[key.lower()] = value
    return flags


def test_real_ip_needs_an_external_route() -> None:
    # home-assistant is in external_deny, so real_ip alone is not enough.
    with pytest.raises(ValidationError, match="home-assistant, which is not external"):
        _config({"home-assistant": AppPolicy(real_ip=True)})


def test_real_ip_refuses_explicit_forward_auth() -> None:
    policy = AppPolicy(external=True, real_ip=True, forward_auth=True)
    with pytest.raises(ValidationError, match="home-assistant, which also has forward_auth"):
        _config({"home-assistant": policy}, forward_auth=True)


def test_real_ip_refuses_forward_auth_by_default() -> None:
    # upstream_host turns forward auth on unless it is set false.
    policy = AppPolicy(external=True, real_ip=True, upstream_host="ha_web_1", upstream_port=8123)
    with pytest.raises(ValidationError, match="which also has forward_auth"):
        _config({"home-assistant": policy}, forward_auth=True)
    policy = policy.model_copy(update={"forward_auth": False})
    _config({"home-assistant": policy}, forward_auth=True)


def test_build_fails_closed_if_the_config_changes_after_loading(app_data: Path) -> None:
    config = _config({"home-assistant": HA}, forward_auth=True)
    config.apps["home-assistant"] = HA.model_copy(update={"forward_auth": True})
    with pytest.raises(StageError, match="real_ip and forward_auth"):
        build(discover(app_data), config)


def test_real_ip_route_uses_the_trusting_entrypoint(app_data: Path) -> None:
    routers = _http(_config({"home-assistant": HA}), app_data)["routers"]
    assert routers["app-home-assistant"]["entryPoints"] == ["websecure", "tunnel-realip"]
    assert routers["app-jellyfin"]["entryPoints"] == ["websecure", "tunnel"]


def test_real_ip_route_golden(app_data: Path) -> None:
    doc = _http(_config({"home-assistant": HA}), app_data)
    generated = {
        "routers": {"app-home-assistant": doc["routers"]["app-home-assistant"]},
        "middlewares": {"realip-home-assistant": doc["middlewares"]["realip-home-assistant"]},
    }
    assert generated == yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))


def test_other_routes_get_no_realip_middleware(app_data: Path) -> None:
    doc = _http(_config({"jellyfin": AppPolicy(forward_auth=True)}, forward_auth=True), app_data)
    assert not any(k.startswith("realip-") for k in doc["middlewares"])
    assert "authentik" in doc["routers"]["app-jellyfin"]["middlewares"]
    assert all("tunnel-realip" not in r["entryPoints"] for r in doc["routers"].values())


def test_real_ip_route_never_gets_the_login(app_data: Path) -> None:
    config = _config(
        {"jellyfin": AppPolicy(forward_auth=True), "home-assistant": HA}, forward_auth=True
    )
    routers = _http(config, app_data)["routers"]
    assert "authentik" not in routers["app-home-assistant"].get("middlewares", [])
    assert "outpost-home-assistant" not in routers


def test_ingress_sends_real_ip_routes_to_8444(app_data: Path) -> None:
    state = build(discover(app_data), _config({"home-assistant": HA}))
    services = {e.get("hostname"): e["service"] for e in build_ingress(state.routes)}
    assert services["home-assistant.bebitwise.dev"] == "https://traefik:8444"
    assert services["jellyfin.bebitwise.dev"] == "https://traefik:8443"


def test_trusting_entrypoint_flags() -> None:
    flags = _flags()
    assert flags["entrypoints.tunnel-realip.address"] == "10.30.0.2:8444"
    assert flags["entrypoints.tunnel-realip.forwardedheaders.trustedips"] == "10.30.0.0/24"
    assert flags["entrypoints.tunnel-realip.forwardedheaders.notappendxforwardedfor"] == "true"
    # Only this entrypoint trusts forwarded headers.
    trusting = {k.split(".")[1] for k in flags if ".forwardedheaders." in k}
    assert trusting == {"tunnel-realip"}
