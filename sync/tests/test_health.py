from __future__ import annotations

from datetime import UTC, datetime, timedelta

from umbrel_edge.health import HealthState


def test_ok_after_clean_pass() -> None:
    state = HealthState()
    state.record(routes=3, errors=[])
    code, body = state.snapshot()
    assert code == 200 and body["routes"] == 3


def test_degraded_with_errors() -> None:
    state = HealthState()
    state.record(routes=3, errors=[{"stage": "traefik", "message": "read-only"}])
    code, body = state.snapshot()
    assert code == 503 and body["errors"] == [{"stage": "traefik", "message": "read-only"}]


def test_degraded_when_stale() -> None:
    state = HealthState()
    state.record(routes=1, errors=[])
    code, _ = state.snapshot(now=datetime.now(UTC) + timedelta(minutes=6))
    assert code == 503
