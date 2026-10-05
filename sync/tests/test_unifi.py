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
    assert saved == {"version": 2, "unifi_records": {"a.example.com": "p1"}, "pending": []}


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


CF_API = "https://api.cloudflare.com/client/v4"
CF_OK = httpx.Response(200, json={"success": True, "result": {"id": "pol"}})


def _cf_env_and_mocks(monkeypatch: pytest.MonkeyPatch) -> dict[str, respx.Route]:
    for name, value in (
        ("CF_API_TOKEN", "tok"),
        ("CF_ACCOUNT_ID", "acc"),
        ("CF_ZONE_ID", "zone"),
        ("CF_TUNNEL_ID", "tun"),
    ):
        monkeypatch.setenv(name, value)
    empty = httpx.Response(
        200, json={"success": True, "result": [], "result_info": {"total_pages": 1}}
    )
    tunnel = f"{CF_API}/accounts/acc/cfd_tunnel/tun/configurations"
    respx.get(tunnel).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": {}}})
    )
    respx.get(f"{CF_API}/zones/zone/dns_records").mock(return_value=empty)
    respx.get(f"{CF_API}/accounts/acc/access/apps").mock(return_value=empty)
    respx.get(f"{CF_API}/accounts/acc/access/policies").mock(return_value=empty)
    respx.post(f"{CF_API}/accounts/acc/access/policies").mock(return_value=CF_OK)
    respx.post(f"{CF_API}/accounts/acc/access/apps").mock(return_value=CF_OK)
    return {
        "tunnel": respx.put(tunnel).mock(return_value=CF_OK),
        "dns": respx.post(f"{CF_API}/zones/zone/dns_records").mock(return_value=CF_OK),
    }


@respx.mock
def test_unifi_failure_does_not_stop_traefik(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNIFI_HOST", "192.168.10.1")
    monkeypatch.setenv("UNIFI_API_KEY", "key")
    monkeypatch.setenv("UNIFI_SITE_ID", "site-1")
    respx.get(BASE).mock(return_value=httpx.Response(401))
    cf = _cf_env_and_mocks(monkeypatch)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    result = run_pass(_settings(tmp_path, app_data))
    assert [e.stage for e in result.errors] == ["unifi"]
    assert (tmp_path / "dynamic" / "apps.yml").exists()
    # The Cloudflare stages run after UniFi, so these prove the failure did not end the pass.
    assert cf["tunnel"].called
    assert cf["dns"].called


@respx.mock
def test_hand_made_record_with_the_proxy_ip_stays_a_conflict(
    client: UnifiClient, tmp_path: Path
) -> None:
    respx.get(BASE).mock(return_value=_page(_record("hand", "umbrel.bebitwise.dev")))
    own = _own(tmp_path)
    for _ in range(2):
        result = reconcile(client, _state("umbrel.bebitwise.dev"), IP, own)
        assert result.conflicts == ["umbrel.bebitwise.dev"]
        assert result.adopt == {}
    # The route goes: the record is still not ours, so it is left alone.
    result = reconcile(client, DesiredState(routes=[]), IP, own)
    assert result.empty
    assert [m for m in respx.calls if m.request.method != "GET"] == []
    assert own.unifi_records == {}
    assert own.pending == set()
    assert not (tmp_path / "ownership.json").exists()


@respx.mock
def test_create_is_not_retried_after_a_read_timeout(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(return_value=_page())
    post = respx.post(BASE).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(StageError) as err:
        reconcile(client, _state("a.example.com"), IP, _own(tmp_path))
    assert post.call_count == 1
    assert err.value.retriable


@respx.mock
def test_lost_create_response_is_adopted_on_the_next_pass(
    client: UnifiClient, tmp_path: Path
) -> None:
    # The server creates the record but the response never arrives.
    respx.get(BASE).mock(side_effect=[_page(), _page(_record("lost", "a.example.com"))])
    post = respx.post(BASE).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(StageError):
        reconcile(client, _state("a.example.com"), IP, _own(tmp_path))
    own = Ownership.load(tmp_path)
    assert own.pending == {"a.example.com"}

    result = reconcile(client, _state("a.example.com"), IP, own)

    assert result.adopt == {"a.example.com": "lost"}
    assert result.conflicts == []
    assert post.call_count == 1
    again = Ownership.load(tmp_path)
    assert again.unifi_records == {"a.example.com": "lost"}
    assert again.pending == set()


class _Crash(BaseException):
    pass


@respx.mock
def test_crash_between_create_and_save_is_adopted_on_the_next_pass(
    client: UnifiClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(BASE).mock(side_effect=[_page(), _page(_record("p1", "a.example.com"))])
    post = respx.post(BASE).mock(return_value=httpx.Response(201, json={"id": "p1"}))
    real_save = Ownership.save
    saves: list[int] = []

    def crash_after_post(self: Ownership) -> None:
        if post.called:
            raise _Crash
        saves.append(1)
        real_save(self)

    monkeypatch.setattr(Ownership, "save", crash_after_post)
    with pytest.raises(_Crash):
        reconcile(client, _state("a.example.com"), IP, _own(tmp_path))
    assert saves == [1]
    monkeypatch.setattr(Ownership, "save", real_save)

    own = Ownership.load(tmp_path)
    assert own.unifi_records == {} and own.pending == {"a.example.com"}
    result = reconcile(client, _state("a.example.com"), IP, own)

    assert result.adopt == {"a.example.com": "p1"}
    assert post.call_count == 1
    again = Ownership.load(tmp_path)
    assert again.unifi_records == {"a.example.com": "p1"}
    assert again.pending == set()


@respx.mock
def test_pending_with_no_record_is_retried(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(return_value=_page())
    post = respx.post(BASE).mock(return_value=httpx.Response(201, json={"id": "p1"}))
    own = _own(tmp_path)
    own.pending.add("a.example.com")
    reconcile(client, _state("a.example.com"), IP, own)
    assert post.call_count == 1
    assert Ownership.load(tmp_path).unifi_records == {"a.example.com": "p1"}
    assert Ownership.load(tmp_path).pending == set()


@respx.mock
def test_pending_is_cleared_when_no_longer_wanted(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(return_value=_page())
    own = _own(tmp_path)
    own.pending.add("a.example.com")
    result = reconcile(client, DesiredState(routes=[]), IP, own)
    assert result.clear_pending == ["a.example.com"]
    assert [m for m in respx.calls if m.request.method != "GET"] == []
    assert Ownership.load(tmp_path).pending == set()


@respx.mock
def test_pending_lost_record_no_longer_wanted_is_adopted_then_deleted(
    client: UnifiClient, tmp_path: Path
) -> None:
    respx.get(BASE).mock(return_value=_page(_record("lost", "a.example.com")))
    delete = respx.delete(f"{BASE}/lost").mock(return_value=httpx.Response(200))
    own = _own(tmp_path)
    own.pending.add("a.example.com")
    reconcile(client, DesiredState(routes=[]), IP, own)
    assert delete.call_count == 1
    saved = Ownership.load(tmp_path)
    assert saved.unifi_records == {} and saved.pending == set()


@respx.mock
def test_pending_name_with_a_different_address_is_not_adopted(
    client: UnifiClient, tmp_path: Path
) -> None:
    respx.get(BASE).mock(return_value=_page(_record("hand", "a.example.com", "10.0.0.9")))
    own = _own(tmp_path)
    own.pending.add("a.example.com")
    for state in (_state("a.example.com"), DesiredState(routes=[])):
        result = reconcile(client, state, IP, own)
        assert result.adopt == {}
    assert [m for m in respx.calls if m.request.method != "GET"] == []
    assert own.unifi_records == {}
    assert own.pending == set()


@respx.mock
def test_rejected_create_clears_pending_so_a_later_hand_made_record_is_never_adopted(
    client: UnifiClient, tmp_path: Path
) -> None:
    # Pass 1: UniFi refuses the create outright, so nothing exists to recover.
    respx.get(BASE).mock(
        side_effect=[
            _page(),
            _page(_record("hand", "a.example.com")),
            _page(_record("hand", "a.example.com")),
        ]
    )
    respx.post(BASE).mock(return_value=httpx.Response(403))
    own = _own(tmp_path)
    with pytest.raises(StageError) as err:
        reconcile(client, _state("a.example.com"), IP, own)
    assert not err.value.retriable
    assert Ownership.load(tmp_path).pending == set()

    # The owner then makes the record by hand: it stays a conflict and survives the route going.
    result = reconcile(client, _state("a.example.com"), IP, own)
    assert result.conflicts == ["a.example.com"]
    assert result.adopt == {}
    reconcile(client, DesiredState(routes=[]), IP, own)
    assert [c for c in respx.calls if c.request.method == "DELETE"] == []
    assert own.unifi_records == {}


@respx.mock
def test_create_without_an_id_keeps_pending(client: UnifiClient, tmp_path: Path) -> None:
    # A 2xx without an id may still have created the record, so the intent is kept.
    respx.get(BASE).mock(return_value=_page())
    respx.post(BASE).mock(return_value=httpx.Response(201, json={}))
    with pytest.raises(StageError) as err:
        reconcile(client, _state("a.example.com"), IP, _own(tmp_path))
    assert err.value.retriable
    assert Ownership.load(tmp_path).pending == {"a.example.com"}


@respx.mock
def test_create_is_not_retried_after_a_5xx(client: UnifiClient, tmp_path: Path) -> None:
    # The server may have created the record before failing; a retry could make a duplicate.
    respx.get(BASE).mock(return_value=_page())
    post = respx.post(BASE).mock(return_value=httpx.Response(502))
    with pytest.raises(StageError) as err:
        reconcile(client, _state("a.example.com"), IP, _own(tmp_path))
    assert post.call_count == 1
    assert err.value.retriable
    assert Ownership.load(tmp_path).pending == {"a.example.com"}


@respx.mock
def test_create_is_retried_after_a_429(client: UnifiClient, tmp_path: Path) -> None:
    respx.get(BASE).mock(return_value=_page())
    post = respx.post(BASE).mock(
        side_effect=[httpx.Response(429), httpx.Response(201, json={"id": "p1"})]
    )
    own = _own(tmp_path)
    reconcile(client, _state("a.example.com"), IP, own)
    assert post.call_count == 2
    assert own.unifi_records == {"a.example.com": "p1"}
