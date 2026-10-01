from __future__ import annotations

from pathlib import Path

from umbrel_edge.discovery import DASHBOARD, discover


def test_reads_valid_manifests_and_skips_bad_ones(app_data: Path) -> None:
    ids = [m.id for m in discover(app_data)]
    assert ids == ["umbrel", "home-assistant", "jellyfin", "mosquitto"]


def test_dashboard_can_be_left_out(app_data: Path) -> None:
    assert DASHBOARD.id not in [m.id for m in discover(app_data, include_dashboard=False)]


def test_missing_root_returns_only_dashboard(tmp_path: Path) -> None:
    assert discover(tmp_path / "nope") == [DASHBOARD]


def test_port_is_read(app_data: Path) -> None:
    by_id = {m.id: m for m in discover(app_data)}
    assert by_id["jellyfin"].port == 8096
