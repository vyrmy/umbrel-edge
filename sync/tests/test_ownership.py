from __future__ import annotations

import json
from pathlib import Path

import pytest

from umbrel_edge.models import StageError
from umbrel_edge.ownership import Ownership


def test_version_1_file_loads_with_nothing_pending(tmp_path: Path) -> None:
    (tmp_path / "ownership.json").write_text(
        json.dumps({"version": 1, "unifi_records": {"a.example.com": "p1"}})
    )
    own = Ownership.load(tmp_path)
    assert own.unifi_records == {"a.example.com": "p1"}
    assert own.pending == set()


def test_pending_round_trips(tmp_path: Path) -> None:
    own = Ownership(tmp_path / "ownership.json", {"a.example.com": "p1"})
    own.pending.add("b.example.com")
    own.save()
    saved = json.loads((tmp_path / "ownership.json").read_text())
    assert saved == {
        "version": 2,
        "unifi_records": {"a.example.com": "p1"},
        "pending": ["b.example.com"],
    }
    again = Ownership.load(tmp_path)
    assert again.unifi_records == {"a.example.com": "p1"}
    assert again.pending == {"b.example.com"}


@pytest.mark.parametrize(
    "content",
    [
        {"version": 2, "unifi_records": {}, "pending": "a.example.com"},
        {"version": 2, "unifi_records": {}, "pending": [1]},
        {"version": 3, "unifi_records": {}},
    ],
)
def test_invalid_pending_or_unknown_version_is_an_error(
    tmp_path: Path, content: dict[str, object]
) -> None:
    (tmp_path / "ownership.json").write_text(json.dumps(content))
    with pytest.raises(StageError):
        Ownership.load(tmp_path)
