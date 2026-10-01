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
