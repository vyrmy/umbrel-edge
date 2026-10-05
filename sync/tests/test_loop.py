from __future__ import annotations

import logging
from pathlib import Path

import pytest

from umbrel_edge.config import Settings
from umbrel_edge.loop import run_pass


def _settings(tmp_path: Path, app_data: Path) -> Settings:
    return Settings(
        config_path=tmp_path / "edge.yaml",
        app_data_root=app_data,
        traefik_dynamic_dir=tmp_path / "dynamic",
        state_dir=tmp_path / "state",
        interval_seconds=60,
        health_port=9000,
        self_app_id="vyrmy-edge",
    )


def test_config_error_is_logged(
    tmp_path: Path, app_data: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "edge.yaml").write_text("domain: bebitwise.dev\nunknown_key: 1\n")
    with caplog.at_level(logging.ERROR, logger="umbrel_edge.loop"):
        result = run_pass(_settings(tmp_path, app_data))
    assert [e.stage for e in result.errors] == ["config"]
    assert any(getattr(r, "stage", None) == "config" for r in caplog.records)


def test_pass_writes_routes(tmp_path: Path, app_data: Path) -> None:
    (tmp_path / "edge.yaml").write_text(
        "domain: bebitwise.dev\nproxy_ip: 192.168.10.4\n"
        "access:\n  allowed_emails: [me@example.com]\n"
    )
    result = run_pass(_settings(tmp_path, app_data))
    assert result.ok
    assert "umbrel.bebitwise.dev" in (tmp_path / "dynamic" / "apps.yml").read_text()
