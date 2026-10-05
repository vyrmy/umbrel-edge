from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from umbrel_edge import traefik_writer
from umbrel_edge.desired import build
from umbrel_edge.discovery import discover
from umbrel_edge.models import DesiredState, EdgeConfig, StageError


def _state(config: EdgeConfig, app_data: Path) -> DesiredState:
    return build(discover(app_data), config)


def test_one_router_and_service_per_route(config: EdgeConfig, app_data: Path) -> None:
    doc = yaml.safe_load(traefik_writer.render(_state(config, app_data), config.domain))
    routers = doc["http"]["routers"]
    assert routers["app-jellyfin"]["rule"] == "Host(`jellyfin.bebitwise.dev`)"
    assert routers["app-jellyfin"]["tls"]["domains"] == [
        {"main": "bebitwise.dev", "sans": ["*.bebitwise.dev"]}
    ]
    server = doc["http"]["services"]["app-jellyfin"]["loadBalancer"]["servers"][0]
    assert server == {"url": "http://host.docker.internal:8096"}
    assert routers["dashboard"]["middlewares"] == ["docker-only"]


def test_write_is_skipped_when_unchanged(
    config: EdgeConfig, app_data: Path, tmp_path: Path
) -> None:
    state = _state(config, app_data)
    assert traefik_writer.write(state, config.domain, tmp_path) is True
    assert traefik_writer.write(state, config.domain, tmp_path) is False


def test_no_temp_files_left_behind(config: EdgeConfig, app_data: Path, tmp_path: Path) -> None:
    traefik_writer.write(_state(config, app_data), config.domain, tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["apps.yml"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_read_only_directory_raises_stage_error(
    config: EdgeConfig, app_data: Path, tmp_path: Path
) -> None:
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(StageError) as exc:
            traefik_writer.write(_state(config, app_data), config.domain, tmp_path)
        assert exc.value.stage == "traefik"
    finally:
        tmp_path.chmod(0o700)


def test_dashboard_entrypoint_only_admits_umbrel_main_network(
    config: EdgeConfig, app_data: Path
) -> None:
    doc = yaml.safe_load(traefik_writer.render(_state(config, app_data), config.domain))
    allow = doc["http"]["middlewares"]["docker-only"]["ipAllowList"]["sourceRange"]
    # umbreld proxies the app page from the host side of umbrel_main_network.
    assert allow == ["10.21.0.0/16"]


COMPOSE = Path(__file__).parents[2] / "vyrmy-edge" / "docker-compose.yml"


def _traefik_flags() -> dict[str, str]:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    flags: dict[str, str] = {}
    for arg in compose["services"]["traefik"]["command"]:
        key, _, value = arg.removeprefix("--").partition("=")
        flags[key.lower()] = value
    return flags


def test_static_config_lives_in_compose_flags() -> None:
    # An app update only copies docker-compose.yml, so no static config file may be mounted.
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    traefik = compose["services"]["traefik"]
    assert not any("traefik.yml" in v for v in traefik["volumes"])
    assert not any(a.startswith("--configfile") for a in map(str.lower, traefik["command"]))


def test_static_flags_match_what_the_writer_references(config: EdgeConfig, app_data: Path) -> None:
    flags = _traefik_flags()
    doc = yaml.safe_load(traefik_writer.render(_state(config, app_data), config.domain))["http"]
    for router in doc["routers"].values():
        for entrypoint in router["entryPoints"]:
            assert f"entrypoints.{entrypoint}.address" in flags
        if "tls" in router:
            resolver = router["tls"]["certResolver"]
            assert f"certificatesresolvers.{resolver}.acme.dnschallenge.provider" in flags
    (plugin,) = doc["middlewares"]["launcher-inject"]["plugin"]
    assert f"experimental.plugins.{plugin}.modulename" in flags
    assert f"experimental.plugins.{plugin}.version" in flags
    # A plugin that fails to download or load must not stop Traefik starting.
    assert flags.get("experimental.abortonpluginfailure", "false") == "false"
    assert flags["providers.file.directory"] == "/data/traefik/dynamic"
