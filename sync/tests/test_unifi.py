from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
import respx

from umbrel_edge.config import Settings
from umbrel_edge.loop import run_pass
from umbrel_edge.models import DesiredState, Route, StageError
from umbrel_edge.ownership import Ownership
from umbrel_edge.unifi import Credentials, UnifiClient, reconcile

BASE = "https://192.168.10.1/proxy/network/integration/v1/sites/site-1/dns/policies"
IP = "192.168.10.4"


def _route(host: str, internal: bool = True) -> Route:
    return Route(
        app_id=host.split(".")[0],
        hostname=host,
        upstream="http://host.docker.internal:80",
        internal=internal,
        external=False,
        access=False,
    )


def _state(*hosts: str) -> DesiredState:
    return DesiredState(routes=[_route(h) for h in hosts])


def _record(pid: str, host: str, ip: str = IP) -> dict[str, object]:
    return {"id": pid, "type": "A_RECORD", "enabled": True, "domain": host, "ipv4Address": ip}


def _page(*records: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"data": list(records), "totalCount": len(records)})


@pytest.fixture
def client() -> UnifiClient:
    sleeps: list[float] = []
    c = UnifiClient(Credentials("192.168.10.1", "key", "site-1"), sleep=sleeps.append)
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


def _own(tmp_path: Path, **records: str) -> Ownership:
    return Ownership(
        tmp_path / "ownership.json", {k.replace("_", "."): v for k, v in records.items()}
    )


@respx.mock
def test_create_records_ownership(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(return_value=_page())
    post = respx.post(BASE).mock(return_value=httpx.Response(201, json={"id": "p1"}))
    own = _own(tmp_path)
    reconcile(client, _state("a.example.com"), IP, own)
    body = json.loads(post.calls[0].request.content)
    assert body == {
        "type": "A_RECORD",
        "enabled": True,
        "domain": "a.example.com",
        "ipv4Address": IP,
        "ttlSeconds": 300,
    }
    assert post.calls[0].request.headers["X-API-KEY"] == "key"
    saved = json.loads((tmp_path / "ownership.json").read_text())
    assert saved == {"version": 1, "unifi_records": {"a.example.com": "p1"}}


@respx.mock
def test_update_owned_record_with_changed_address(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(return_value=_page(_record("p1", "a.example.com", "10.0.0.9")))
    put = respx.put(f"{BASE}/p1").mock(return_value=httpx.Response(200, json={}))
    own = _own(tmp_path, **{"a_example_com": "p1"})
    reconcile(client, _state("a.example.com"), IP, own)
    assert json.loads(put.calls[0].request.content)["ipv4Address"] == IP


@respx.mock
def test_delete_owned_record_no_longer_wanted(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(
        return_value=_page(_record("p1", "a.example.com"), _record("hand", "umbrel.example.com"))
    )
    delete = respx.delete(f"{BASE}/p1").mock(return_value=httpx.Response(200))
    own = _own(tmp_path, **{"a_example_com": "p1"})
    reconcile(client, DesiredState(routes=[]), IP, own)
    assert delete.call_count == 1
    assert json.loads((tmp_path / "ownership.json").read_text())["unifi_records"] == {}


@respx.mock
def test_unowned_clash_is_conflict_and_never_touched(
    client: UnifiClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(BASE).mock(return_value=_page(_record("hand", "a.example.com", "10.0.0.9")))
    with caplog.at_level(logging.WARNING, logger="umbrel_edge.unifi"):
        result = reconcile(client, _state("a.example.com"), IP, _own(tmp_path))
    assert result.conflicts == ["a.example.com"]
    assert [m for m in respx.calls if m.request.method != "GET"] == []
    assert any("conflict" in r.getMessage() for r in caplog.records)
    assert not (tmp_path / "ownership.json").exists()


@respx.mock
def test_unowned_record_is_not_deleted_when_route_goes(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(return_value=_page(_record("hand", "a.example.com")))
    reconcile(client, DesiredState(routes=[]), IP, _own(tmp_path))
    assert respx.calls.call_count == 1


@respx.mock
def test_429_retried_then_succeeds(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(side_effect=[httpx.Response(429), httpx.Response(503), _page()])
    result = reconcile(client, DesiredState(routes=[]), IP, _own(tmp_path))
    assert result.empty
    assert client.sleeps == [1.0, 2.0]  # type: ignore[attr-defined]


@respx.mock
def test_429_exhausted_is_retriable(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(return_value=httpx.Response(429))
    with pytest.raises(StageError) as err:
        reconcile(client, DesiredState(routes=[]), IP, _own(tmp_path))
    assert err.value.stage == "unifi"
    assert err.value.retriable


@respx.mock
def test_401_not_retried(client: UnifiClient, tmp_path: Path) -> None:
    route = respx.get(BASE).mock(return_value=httpx.Response(401))
    with pytest.raises(StageError) as err:
        reconcile(client, DesiredState(routes=[]), IP, _own(tmp_path))
    assert not err.value.retriable
    assert route.call_count == 1
    assert client.sleeps == []  # type: ignore[attr-defined]


@respx.mock
def test_pagination(client: UnifiClient) -> None:
    respx.get(BASE, params={"offset": 0, "limit": 200}).mock(
        return_value=httpx.Response(200, json={"data": [_record("p1", "a.x")], "totalCount": 2})
    )
    respx.get(BASE, params={"offset": 1, "limit": 200}).mock(
        return_value=httpx.Response(200, json={"data": [_record("p2", "b.x")], "totalCount": 2})
    )
    assert [r["id"] for r in client.list_a_records()] == ["p1", "p2"]


@respx.mock
def test_dry_run_writes_nothing(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(return_value=_page())
    result = reconcile(client, _state("a.example.com"), IP, _own(tmp_path), dry_run=True)
    assert result.create == {"a.example.com": IP}
    assert respx.calls.call_count == 1
    assert not (tmp_path / "ownership.json").exists()


def test_corrupt_ownership_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "ownership.json").write_text("{nope")
    with pytest.raises(StageError):
        Ownership.load(tmp_path)


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


_CONFIG = (
    "domain: bebitwise.dev\nproxy_ip: 192.168.10.4\naccess:\n  allowed_emails: [me@example.com]\n"
)


def test_stage_skipped_without_secrets(
    tmp_path: Path,
    app_data: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for name in ("UNIFI_HOST", "UNIFI_API_KEY", "UNIFI_SITE_ID"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    with caplog.at_level(logging.INFO, logger="umbrel_edge.loop"):
        result = run_pass(_settings(tmp_path, app_data))
    assert result.ok
    assert sum("unifi stage skipped" in r.getMessage() for r in caplog.records) == 1


@respx.mock
def test_unifi_failure_does_not_stop_traefik(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNIFI_HOST", "192.168.10.1")
    monkeypatch.setenv("UNIFI_API_KEY", "key")
    monkeypatch.setenv("UNIFI_SITE_ID", "site-1")
    respx.get(BASE).mock(return_value=httpx.Response(401))
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    result = run_pass(_settings(tmp_path, app_data))
    assert [e.stage for e in result.errors] == ["unifi"]
    assert (tmp_path / "dynamic" / "apps.yml").exists()
