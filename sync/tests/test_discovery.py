from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from umbrel_edge import discovery
from umbrel_edge.discovery import DASHBOARD, discover
from umbrel_edge.models import AppManifest


@pytest.fixture(autouse=True)
def _fresh_skip_log() -> Iterator[None]:
    # Other test modules run discovery too, so start each test with nothing reported yet.
    discovery._reported_skips.clear()
    yield
    discovery._reported_skips.clear()


@pytest.fixture
def unlisted_root(app_data: Path, tmp_path: Path) -> Path:
    """A copy of the app-data fixture with no umbrel.yaml beside it."""
    shutil.copytree(app_data, tmp_path / "app-data")
    return tmp_path


def _ids(manifests: list[AppManifest]) -> list[str]:
    return [m.id for m in manifests]


def test_reads_valid_manifests_and_skips_bad_ones(app_data: Path) -> None:
    assert _ids(discover(app_data)) == ["umbrel", "home-assistant", "jellyfin", "mosquitto"]


def test_dashboard_can_be_left_out(app_data: Path) -> None:
    assert DASHBOARD.id not in _ids(discover(app_data, include_dashboard=False))


def test_missing_root_returns_only_dashboard(tmp_path: Path) -> None:
    assert discover(tmp_path / "nope") == [DASHBOARD]


def test_port_is_read(app_data: Path) -> None:
    by_id = {m.id: m for m in discover(app_data)}
    assert by_id["jellyfin"].port == 8096


def test_folder_umbreld_does_not_list_is_skipped(app_data: Path) -> None:
    assert (app_data / "lobe-chat" / "umbrel-app.yml").is_file()
    assert "lobe-chat" not in _ids(discover(app_data))


def test_skipped_folder_is_logged_once_at_info(
    app_data: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="umbrel_edge.discovery"):
        discover(app_data)
        discover(app_data)
    skips = [r for r in caplog.records if "lobe-chat" in r.getMessage()]
    assert [r.levelno for r in skips] == [logging.INFO]


def test_folder_is_logged_again_after_it_was_installed(
    unlisted_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    listed = unlisted_root / "umbrel.yaml"
    with caplog.at_level(logging.INFO, logger="umbrel_edge.discovery"):
        listed.write_text("apps: [jellyfin]\n")
        discover(unlisted_root / "app-data")
        listed.write_text("apps: [jellyfin, lobe-chat]\n")
        assert "lobe-chat" in _ids(discover(unlisted_root / "app-data"))
        listed.write_text("apps: [jellyfin]\n")
        discover(unlisted_root / "app-data")
    assert len([r for r in caplog.records if "lobe-chat" in r.getMessage()]) == 2


def test_empty_list_means_no_apps(unlisted_root: Path) -> None:
    (unlisted_root / "umbrel.yaml").write_text("apps: []\n")
    assert discover(unlisted_root / "app-data") == [DASHBOARD]


def test_missing_list_falls_back_to_every_folder_with_a_warning(
    unlisted_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="umbrel_edge.discovery"):
        ids = _ids(discover(unlisted_root / "app-data"))
    assert ids == ["umbrel", "home-assistant", "jellyfin", "lobe-chat", "mosquitto"]
    assert any("umbrel.yaml" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("apps: [unclosed\n", id="invalid-yaml"),
        pytest.param("- jellyfin\n", id="not-a-mapping"),
        pytest.param("version: 2.0.0\n", id="no-apps-key"),
        pytest.param("apps: jellyfin\n", id="apps-not-a-list"),
        pytest.param("apps: [jellyfin, 7]\n", id="non-string-id"),
        pytest.param("", id="empty-file"),
    ],
)
def test_unusable_list_falls_back_to_every_folder_with_a_warning(
    unlisted_root: Path, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    (unlisted_root / "umbrel.yaml").write_text(content)
    with caplog.at_level(logging.WARNING, logger="umbrel_edge.discovery"):
        ids = _ids(discover(unlisted_root / "app-data"))
    assert "lobe-chat" in ids
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_unreadable_list_falls_back_to_every_folder(unlisted_root: Path) -> None:
    (unlisted_root / "umbrel.yaml").mkdir()
    assert "lobe-chat" in _ids(discover(unlisted_root / "app-data"))


def test_undecodable_list_falls_back_to_every_folder(unlisted_root: Path) -> None:
    (unlisted_root / "umbrel.yaml").write_bytes(b"apps: [\xff\xfe]\n")
    assert "lobe-chat" in _ids(discover(unlisted_root / "app-data"))
