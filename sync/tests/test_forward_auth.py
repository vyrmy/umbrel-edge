from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from umbrel_edge import traefik_writer
from umbrel_edge.config import load_edge_config
from umbrel_edge.desired import build
from umbrel_edge.discovery import discover
from umbrel_edge.models import (
    DEFAULT_AUTH_RESPONSE_HEADERS,
    AppManifest,
    AppPolicy,
    EdgeConfig,
    ForwardAuthSettings,
    StageError,
)

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "forward-auth-route.yml"
ADDRESS = "http://host.docker.internal:9000/outpost.goauthentik.io/auth/traefik"
OUTPOST = "http://host.docker.internal:9000"


def _config(**apps: AppPolicy) -> EdgeConfig:
    return EdgeConfig.model_validate(
        {
            "domain": "bebitwise.dev",
            "proxy_ip": "192.168.10.4",
            "access": {"allowed_emails": ["me@example.com"]},
            "forward_auth": {"address": ADDRESS, "outpost_url": OUTPOST},
            "apps": {k: v.model_dump(exclude_unset=True) for k, v in apps.items()},
        }
    )


def _http(config: EdgeConfig, app_data: Path) -> dict[str, Any]:
    state = build(discover(app_data), config)
    doc: dict[str, Any] = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]
    return doc


def test_settings_default_to_authentiks_documented_headers() -> None:
    settings = ForwardAuthSettings(address=ADDRESS, outpost_url=OUTPOST)
    assert settings.response_headers == DEFAULT_AUTH_RESPONSE_HEADERS
    assert "X-authentik-username" in settings.response_headers
    assert "X-authentik-meta-version" in settings.response_headers


def test_forward_auth_is_off_by_default(app_data: Path) -> None:
    state = build(discover(app_data), _config())
    assert not any(r.forward_auth for r in state.routes)


def test_policy_and_default_resolve_per_route(app_data: Path) -> None:
    config = _config(jellyfin=AppPolicy(forward_auth=True))
    flags = {r.app_id: r.forward_auth for r in build(discover(app_data), config).routes}
    assert flags == {"umbrel": False, "home-assistant": False, "jellyfin": True}
    config.defaults.forward_auth = True
    config.apps["umbrel"] = AppPolicy(forward_auth=False)
    flags = {r.app_id: r.forward_auth for r in build(discover(app_data), config).routes}
    assert flags == {"umbrel": False, "home-assistant": True, "jellyfin": True}


def test_protected_app_without_settings_names_the_apps() -> None:
    with pytest.raises(ValidationError) as exc:
        EdgeConfig.model_validate(
            {
                "domain": "bebitwise.dev",
                "proxy_ip": "192.168.10.4",
                "access": {"allowed_emails": ["me@example.com"]},
                "apps": {
                    "jellyfin": {"forward_auth": True},
                    "radarr": {"forward_auth": True},
                    "sonarr": {"forward_auth": False},
                },
            }
        )
    message = str(exc.value)
    assert "jellyfin" in message and "radarr" in message and "sonarr" not in message


def test_default_on_without_settings_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "edge.yaml"
    path.write_text(
        "domain: bebitwise.dev\nproxy_ip: 192.168.10.4\n"
        "access:\n  allowed_emails: [me@example.com]\n"
        "defaults:\n  forward_auth: true\n"
    )
    with pytest.raises(StageError) as exc:
        load_edge_config(path)
    assert exc.value.stage == "config"
    assert "defaults.forward_auth" in exc.value.message


def test_build_refuses_protected_routes_without_settings(
    config: EdgeConfig, app_data: Path
) -> None:
    # Validation runs on load; a config changed afterwards must still fail closed.
    config.apps["jellyfin"] = AppPolicy(forward_auth=True)
    with pytest.raises(StageError) as exc:
        build(discover(app_data), config)
    assert exc.value.stage == "config"
    assert "jellyfin" in exc.value.message


def test_protected_route_golden(app_data: Path) -> None:
    doc = _http(_config(jellyfin=AppPolicy(forward_auth=True)), app_data)
    generated = {
        "routers": {k: doc["routers"][k] for k in ("app-jellyfin", "outpost-jellyfin")},
        "services": {"authentik-outpost": doc["services"]["authentik-outpost"]},
        "middlewares": {"authentik": doc["middlewares"]["authentik"]},
    }
    assert generated == yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))


def test_outpost_router_outranks_the_app_router(app_data: Path) -> None:
    doc = _http(_config(jellyfin=AppPolicy(forward_auth=True)), app_data)
    outpost = doc["routers"]["outpost-jellyfin"]
    app = doc["routers"]["app-jellyfin"]
    # Traefik's default priority is the rule length.
    assert outpost["priority"] > len(app["rule"])
    assert "middlewares" not in outpost
    assert outpost["tls"] == app["tls"]
    assert outpost["entryPoints"] == app["entryPoints"]


def test_unprotected_routes_are_unchanged(app_data: Path) -> None:
    plain = _http(_config(), app_data)
    protected = _http(_config(jellyfin=AppPolicy(forward_auth=True)), app_data)
    for name in ("app-home-assistant", "app-umbrel", "edge-assets", "edge-umbrel-fallback"):
        assert protected["routers"][name] == plain["routers"][name]
    assert not any(k.startswith("outpost-") for k in plain["routers"])
    assert "authentik" not in plain["middlewares"]
    assert "authentik-outpost" not in plain["services"]


def test_settings_alone_add_nothing(config: EdgeConfig, app_data: Path) -> None:
    before = traefik_writer.render(build(discover(app_data), config), config.domain)
    after = traefik_writer.render(build(discover(app_data), _config()), config.domain)
    assert before == after


def test_protected_umbrel_route_authenticates_every_path(app_data: Path) -> None:
    doc = _http(_config(umbrel=AppPolicy(forward_auth=True)), app_data)
    routers = doc["routers"]
    # Authenticate before the body rewrite, so a login page is never rewritten.
    assert routers["app-umbrel"]["middlewares"] == ["authentik", "launcher-inject"]
    # Neither the launcher script nor the plugin-failure fallback may bypass the login.
    assert routers["edge-assets"]["middlewares"] == ["authentik"]
    assert routers["edge-umbrel-fallback"]["middlewares"] == ["authentik"]
    assert routers["outpost-umbrel"]["rule"] == (
        "Host(`umbrel.bebitwise.dev`) && PathPrefix(`/outpost.goauthentik.io/`)"
    )
    assert routers["outpost-umbrel"]["priority"] > routers["edge-umbrel-fallback"]["priority"]


def test_every_protected_route_shares_one_middleware_and_service(app_data: Path) -> None:
    config = _config(
        jellyfin=AppPolicy(forward_auth=True), **{"home-assistant": AppPolicy(forward_auth=True)}
    )
    doc = _http(config, app_data)
    outposts = {k: v for k, v in doc["routers"].items() if k.startswith("outpost-")}
    assert set(outposts) == {"outpost-jellyfin", "outpost-home-assistant"}
    assert {r["service"] for r in outposts.values()} == {"authentik-outpost"}
    for name in ("app-jellyfin", "app-home-assistant"):
        assert doc["routers"][name]["middlewares"] == ["authentik"]


def test_default_on_leaves_authentik_itself_unprotected(app_data: Path) -> None:
    # Authentik's login flow must be reachable, or every protected app is locked out.
    config = _config()
    config.defaults.forward_auth = True
    authentik = AppManifest(id="authentik", name="authentik", port=9000)
    state = build([*discover(app_data), authentik], config)
    flags = {r.app_id: r.forward_auth for r in state.routes}
    assert flags == {"authentik": False, "umbrel": True, "home-assistant": True, "jellyfin": True}
    doc = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]
    assert "middlewares" not in doc["routers"]["app-authentik"]
    assert "outpost-authentik" not in doc["routers"]


def test_forward_auth_deny_is_overridden_only_explicitly() -> None:
    config = _config(authentik=AppPolicy(forward_auth=True))
    config.defaults.forward_auth = True
    config.forward_auth_deny.append("radarr")
    manifests = [
        AppManifest(id="authentik", name="authentik", port=9000),
        AppManifest(id="radarr", name="Radarr", port=7878),
    ]
    flags = {r.app_id: r.forward_auth for r in build(manifests, config).routes}
    assert flags == {"authentik": True, "radarr": False}


def test_forward_auth_deny_defaults_to_authentik() -> None:
    assert _config().forward_auth_deny == ["authentik"]
