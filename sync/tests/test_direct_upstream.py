"""upstream_host: a route straight to an app's container, past umbreld's gateway (decision 15)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from umbrel_edge.desired import build
from umbrel_edge.models import AppManifest, AppPolicy, EdgeConfig, StageError

DESKTOP = AppManifest(id="vyrmy-desktop-1", name="Desktop 1", port=18101)
CONTAINER = "vyrmy-desktop-1_desktop_1"
FORWARD_AUTH = {
    "address": "http://denny-authentik_server_1:9000/outpost.goauthentik.io/auth/traefik",
    "outpost_url": "http://denny-authentik_server_1:9000",
}


def _config(app: dict[str, Any], *, forward_auth: bool = True) -> EdgeConfig:
    doc: dict[str, Any] = {
        "domain": "bebitwise.dev",
        "proxy_ip": "192.168.10.4",
        "access": {"allowed_emails": ["me@example.com"]},
        "apps": {DESKTOP.id: app},
    }
    if forward_auth:
        doc["forward_auth"] = FORWARD_AUTH
    return EdgeConfig.model_validate(doc)


def test_upstream_is_the_container_not_the_host_port() -> None:
    config = _config({"upstream_host": CONTAINER, "upstream_port": 3000})
    route = build([DESKTOP], config).routes[0]
    assert route.upstream == f"http://{CONTAINER}:3000"


def test_upstream_scheme_applies_to_the_container() -> None:
    config = _config(
        {"upstream_host": CONTAINER, "upstream_port": 3001, "upstream_scheme": "https"}
    )
    assert build([DESKTOP], config).routes[0].upstream == f"https://{CONTAINER}:3001"


def test_upstream_host_needs_the_container_port() -> None:
    # The manifest port is umbreld's gateway on the host, never the container's own port.
    with pytest.raises(ValidationError, match="upstream_port"):
        AppPolicy(upstream_host=CONTAINER)


@pytest.mark.parametrize("name", ["", "-leading-dash", "has space", "a/b", "http://x"])
def test_upstream_host_must_be_a_container_name(name: str) -> None:
    with pytest.raises(ValidationError):
        AppPolicy(upstream_host=name, upstream_port=3000)


def test_forward_auth_defaults_on_past_the_gateway() -> None:
    # Skipping the gateway skips Umbrel's login, so the hostname would otherwise be open.
    config = _config({"upstream_host": CONTAINER, "upstream_port": 3000})
    assert config.defaults.forward_auth is False
    assert build([DESKTOP], config).routes[0].forward_auth is True


def test_forward_auth_can_still_be_turned_off_explicitly() -> None:
    config = _config({"upstream_host": CONTAINER, "upstream_port": 3000, "forward_auth": False})
    assert build([DESKTOP], config).routes[0].forward_auth is False


def test_forward_auth_deny_still_wins() -> None:
    # Authentik straight to its container must not be put behind its own login.
    authentik = AppManifest(id="denny-authentik", name="Authentik", port=3973)
    config = EdgeConfig.model_validate(
        {
            "domain": "bebitwise.dev",
            "proxy_ip": "192.168.10.4",
            "access": {"allowed_emails": ["me@example.com"]},
            "apps": {
                authentik.id: {"upstream_host": "denny-authentik_server_1", "upstream_port": 9000}
            },
        }
    )
    assert build([authentik], config).routes[0].forward_auth is False


def test_missing_forward_auth_block_is_a_config_error() -> None:
    with pytest.raises(ValidationError, match=DESKTOP.id):
        _config({"upstream_host": CONTAINER, "upstream_port": 3000}, forward_auth=False)


def test_explicit_off_needs_no_forward_auth_block() -> None:
    config = _config(
        {"upstream_host": CONTAINER, "upstream_port": 3000, "forward_auth": False},
        forward_auth=False,
    )
    assert build([DESKTOP], config).forward_auth is None


def test_build_fails_closed_without_the_block() -> None:
    config = _config({"upstream_host": CONTAINER, "upstream_port": 3000})
    config.forward_auth = None
    with pytest.raises(StageError, match=DESKTOP.id):
        build([DESKTOP], config)


def test_launcher_still_maps_the_umbrel_port() -> None:
    # The dashboard links to the gateway port from the manifest, so that is what the
    # launcher must rewrite, whatever Traefik's upstream is.
    config = _config({"subdomain": "desk-a", "upstream_host": CONTAINER, "upstream_port": 3000})
    state = build([DESKTOP], config)
    assert state.port_map({DESKTOP.id: DESKTOP}) == {18101: "desk-a.bebitwise.dev"}
