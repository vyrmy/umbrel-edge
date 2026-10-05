from __future__ import annotations

from pathlib import Path

import pytest

from umbrel_edge.desired import build
from umbrel_edge.discovery import discover
from umbrel_edge.models import AppManifest, AppPolicy, EdgeConfig, StageError


def _hosts(config: EdgeConfig, app_data: Path) -> dict[str, tuple[bool, bool, bool]]:
    state = build(discover(app_data), config)
    return {r.hostname: (r.internal, r.external, r.access) for r in state.routes}


def test_defaults_every_app_except_excluded(config: EdgeConfig, app_data: Path) -> None:
    hosts = _hosts(config, app_data)
    assert set(hosts) == {
        "umbrel.bebitwise.dev",
        "home-assistant.bebitwise.dev",
        "jellyfin.bebitwise.dev",
    }
    assert hosts["jellyfin.bebitwise.dev"] == (True, True, True)


def test_deny_list_keeps_app_internal(config: EdgeConfig, app_data: Path) -> None:
    assert _hosts(config, app_data)["home-assistant.bebitwise.dev"] == (True, False, False)


@pytest.mark.parametrize("app_id", ["arcane", "denny-olivetin", "torbrowser", "denny-librewolf"])
def test_default_deny_list_uses_umbrel_app_ids(config: EdgeConfig, app_id: str) -> None:
    state = build([AppManifest(id=app_id, name=app_id, port=1)], config)
    assert state.routes[0].external is False


def test_explicit_external_overrides_deny_list(config: EdgeConfig, app_data: Path) -> None:
    config.apps["home-assistant"] = AppPolicy(external=True)
    assert _hosts(config, app_data)["home-assistant.bebitwise.dev"] == (True, True, True)


def test_subdomain_override(config: EdgeConfig, app_data: Path) -> None:
    config.apps["jellyfin"] = AppPolicy(subdomain="tv")
    assert "tv.bebitwise.dev" in _hosts(config, app_data)


def test_collision_names_both_apps(config: EdgeConfig, app_data: Path) -> None:
    config.apps["jellyfin"] = AppPolicy(subdomain="umbrel")
    with pytest.raises(StageError) as exc:
        build(discover(app_data), config)
    assert "umbrel" in exc.value.message and "jellyfin" in exc.value.message


def test_app_with_no_exposure_gets_no_route(config: EdgeConfig, app_data: Path) -> None:
    config.apps["jellyfin"] = AppPolicy(internal=False, external=False)
    assert "jellyfin.bebitwise.dev" not in _hosts(config, app_data)


def test_upstream_override(config: EdgeConfig) -> None:
    config.apps["x"] = AppPolicy(upstream_port=8443, upstream_scheme="https")
    state = build([AppManifest(id="x", name="X", port=1234)], config)
    assert state.routes[0].upstream == "https://host.docker.internal:8443"


def test_invalid_label_is_skipped(config: EdgeConfig) -> None:
    state = build([AppManifest(id="Bad_Id", name="Bad", port=1)], config)
    assert state.routes == []


def test_routes_sorted_by_hostname(config: EdgeConfig, app_data: Path) -> None:
    hostnames = [r.hostname for r in build(discover(app_data), config).routes]
    assert hostnames == sorted(hostnames)
