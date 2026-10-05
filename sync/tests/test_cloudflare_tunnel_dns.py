from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from umbrel_edge.cloudflare import (
    CloudflareClient,
    Credentials,
    build_ingress,
    external_routes,
    reconcile_dns,
    reconcile_tunnel,
)
from umbrel_edge.config import Settings
from umbrel_edge.loop import run_pass
from umbrel_edge.models import DesiredState, Route, StageError

API = "https://api.cloudflare.com/client/v4"
TUNNEL = f"{API}/accounts/acc/cfd_tunnel/tun/configurations"
DNS = f"{API}/zones/zone/dns_records"
TARGET = "tun.cfargotunnel.com"
COMMENT = "managed-by=umbrel-edge"


def _route(host: str, *, external: bool = True, access: bool = False) -> Route:
    return Route(
        app_id=host.split(".")[0],
        hostname=host,
        upstream="http://host.docker.internal:80",
        internal=True,
        external=external,
        access=access,
    )


def _state(*routes: Route) -> DesiredState:
    return DesiredState(routes=list(routes))


def _ingress_entry(host: str) -> dict[str, Any]:
    return {
        "hostname": host,
        "service": "https://traefik:443",
        "originRequest": {"originServerName": host},
    }


def _config(*hosts: str) -> httpx.Response:
    ingress = [_ingress_entry(h) for h in hosts] + [{"service": "http_status:404"}]
    return httpx.Response(200, json={"success": True, "result": {"config": {"ingress": ingress}}})


def _record(rid: str, name: str, *, managed: bool = True, **extra: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "id": rid,
        "name": name,
        "type": "CNAME",
        "content": TARGET,
        "proxied": True,
        "comment": COMMENT if managed else None,
    }
    return rec | extra


def _records(*records: dict[str, Any], total_pages: int = 1) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "success": True,
            "result": list(records),
            "result_info": {"total_pages": total_pages},
        },
    )


@pytest.fixture
def client() -> CloudflareClient:
    sleeps: list[float] = []
    c = CloudflareClient(Credentials("tok", "acc", "zone", "tun"), sleep=sleeps.append)
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


# --- ingress ---


def test_ingress_is_sorted_and_ends_with_catch_all() -> None:
    ingress = build_ingress([_route("b.x.dev"), _route("a.x.dev")])
    assert ingress == [
        _ingress_entry("a.x.dev"),
        _ingress_entry("b.x.dev"),
        {"service": "http_status:404"},
    ]


def test_ingress_with_no_routes_is_only_the_catch_all() -> None:
    assert build_ingress([]) == [{"service": "http_status:404"}]


def test_every_external_route_is_published_regardless_of_access() -> None:
    state = _state(
        _route("open.x.dev"),
        _route("gated.x.dev", access=True),
        _route("internal.x.dev", external=False),
    )
    assert [r.hostname for r in external_routes(state)] == ["open.x.dev", "gated.x.dev"]


@respx.mock
def test_put_when_ingress_differs(client: CloudflareClient) -> None:
    respx.get(TUNNEL).mock(return_value=_config("old.x.dev"))
    put = respx.put(TUNNEL).mock(return_value=httpx.Response(200, json={"success": True}))
    plan = reconcile_tunnel(client, _state(_route("new.x.dev")))
    assert json.loads(put.calls[0].request.content) == {
        "config": {"ingress": [_ingress_entry("new.x.dev"), {"service": "http_status:404"}]}
    }
    assert put.calls[0].request.headers["Authorization"] == "Bearer tok"
    assert plan.added == ["new.x.dev"]
    assert plan.removed == ["old.x.dev"]


@respx.mock
def test_no_put_when_nothing_changed(client: CloudflareClient) -> None:
    respx.get(TUNNEL).mock(return_value=_config("a.x.dev"))
    put = respx.put(TUNNEL)
    plan = reconcile_tunnel(client, _state(_route("a.x.dev")))
    assert not plan.changed
    assert not put.called


@respx.mock
def test_hand_edited_deny_listed_entry_is_overwritten(client: CloudflareClient) -> None:
    respx.get(TUNNEL).mock(return_value=_config("a.x.dev", "portainer.x.dev"))
    put = respx.put(TUNNEL).mock(return_value=httpx.Response(200, json={"success": True}))
    reconcile_tunnel(client, _state(_route("a.x.dev"), _route("portainer.x.dev", external=False)))
    sent = json.loads(put.calls[0].request.content)["config"]["ingress"]
    assert [e.get("hostname") for e in sent] == ["a.x.dev", None]


@respx.mock
def test_tunnel_with_no_config_yet_gets_one(client: CloudflareClient) -> None:
    respx.get(TUNNEL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": None}})
    )
    put = respx.put(TUNNEL).mock(return_value=httpx.Response(200, json={"success": True}))
    reconcile_tunnel(client, _state(_route("a.x.dev")))
    assert put.called


@respx.mock
def test_tunnel_dry_run_never_puts(client: CloudflareClient) -> None:
    respx.get(TUNNEL).mock(return_value=_config())
    put = respx.put(TUNNEL)
    plan = reconcile_tunnel(client, _state(_route("a.x.dev")), dry_run=True)
    assert plan.added == ["a.x.dev"]
    assert not put.called


# --- DNS ---


@respx.mock
def test_dns_create(client: CloudflareClient) -> None:
    respx.get(DNS).mock(return_value=_records())
    post = respx.post(DNS).mock(return_value=httpx.Response(200, json={"success": True}))
    reconcile_dns(client, _state(_route("a.x.dev")))
    assert json.loads(post.calls[0].request.content) == {
        "type": "CNAME",
        "name": "a.x.dev",
        "content": TARGET,
        "proxied": True,
        "ttl": 1,
        "comment": COMMENT,
    }


@respx.mock
def test_dns_update_managed_record_with_wrong_content(client: CloudflareClient) -> None:
    respx.get(DNS).mock(return_value=_records(_record("r1", "a.x.dev", content="old.example.")))
    patch = respx.patch(f"{DNS}/r1").mock(return_value=httpx.Response(200, json={"success": True}))
    reconcile_dns(client, _state(_route("a.x.dev")))
    assert json.loads(patch.calls[0].request.content)["content"] == TARGET


@respx.mock
def test_dns_in_sync_makes_no_writes(client: CloudflareClient) -> None:
    respx.get(DNS).mock(return_value=_records(_record("r1", "a.x.dev")))
    result = reconcile_dns(client, _state(_route("a.x.dev")))
    assert result.empty
    assert respx.calls.call_count == 1


@respx.mock
def test_dns_deletes_managed_record_for_removed_route(client: CloudflareClient) -> None:
    respx.get(DNS).mock(return_value=_records(_record("r1", "gone.x.dev")))
    delete = respx.delete(f"{DNS}/r1").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    reconcile_dns(client, _state())
    assert delete.called


@respx.mock
def test_unmanaged_records_are_never_touched(client: CloudflareClient) -> None:
    respx.get(DNS).mock(
        return_value=_records(
            _record("w", "www.x.dev", managed=False),
            _record("o", "other.x.dev", managed=False),
            _record("m", "a.x.dev", comment="someone else"),
        )
    )
    writes = [respx.post(DNS), respx.patch(url__startswith=DNS), respx.delete(url__startswith=DNS)]
    result = reconcile_dns(client, _state(_route("www.x.dev"), _route("a.x.dev")))
    assert sorted(result.conflicts) == ["a.x.dev", "www.x.dev"]
    assert not any(w.called for w in writes)


@respx.mock
def test_dns_pagination(client: CloudflareClient) -> None:
    respx.get(DNS, params={"page": 1, "per_page": 100}).mock(
        return_value=_records(_record("r1", "a.x.dev"), total_pages=2)
    )
    respx.get(DNS, params={"page": 2, "per_page": 100}).mock(
        return_value=_records(_record("r2", "b.x.dev"), total_pages=2)
    )
    assert [r["id"] for r in client.list_dns_records()] == ["r1", "r2"]


@respx.mock
def test_dns_dry_run_writes_nothing(client: CloudflareClient) -> None:
    respx.get(DNS).mock(return_value=_records(_record("r1", "gone.x.dev")))
    post, delete = respx.post(DNS), respx.delete(url__startswith=DNS)
    result = reconcile_dns(client, _state(_route("a.x.dev")), dry_run=True)
    assert result.create == ["a.x.dev"]
    assert "gone.x.dev" in result.delete
    assert not post.called and not delete.called


@respx.mock
def test_dns_publishes_access_routes_too(client: CloudflareClient) -> None:
    respx.get(DNS).mock(return_value=_records())
    post = respx.post(DNS).mock(return_value=httpx.Response(200, json={"success": True}))
    reconcile_dns(client, _state(_route("gated.x.dev", access=True)))
    assert post.called


@respx.mock
def test_dns_withheld_hostnames_are_neither_created_nor_updated(client: CloudflareClient) -> None:
    respx.get(DNS).mock(
        return_value=_records(_record("r1", "old.x.dev", content="wrong.example.com"))
    )
    post = respx.post(DNS).mock(return_value=httpx.Response(200, json={"success": True}))
    patch = respx.patch(f"{DNS}/r1")
    state = _state(_route("new.x.dev", access=True), _route("old.x.dev", access=True))
    reconcile_dns(client, state, withheld=frozenset({"new.x.dev", "old.x.dev"}))
    assert not post.called and not patch.called


@respx.mock
def test_dns_without_publish_only_deletes(client: CloudflareClient) -> None:
    respx.get(DNS).mock(return_value=_records(_record("r1", "gone.x.dev")))
    post = respx.post(DNS)
    delete = respx.delete(f"{DNS}/r1").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    reconcile_dns(client, _state(_route("a.x.dev")), publish=False)
    assert not post.called and delete.called


# --- retry policy and errors ---


@respx.mock
def test_5xx_retries_with_backoff_then_succeeds(client: CloudflareClient) -> None:
    respx.get(TUNNEL).mock(
        side_effect=[httpx.Response(503), httpx.Response(429), _config("a.x.dev")]
    )
    assert len(client.get_ingress()) == 2
    assert client.sleeps == [1.0, 2.0]  # type: ignore[attr-defined]


@respx.mock
def test_exhausted_retries_raise_retriable_error(client: CloudflareClient) -> None:
    respx.get(TUNNEL).mock(return_value=httpx.Response(500))
    with pytest.raises(StageError) as err:
        client.get_ingress()
    assert err.value.stage == "cloudflare_tunnel"
    assert err.value.retriable


@respx.mock
def test_4xx_is_not_retried(client: CloudflareClient) -> None:
    route = respx.get(DNS).mock(return_value=httpx.Response(403))
    with pytest.raises(StageError) as err:
        client.list_dns_records()
    assert err.value.stage == "cloudflare_dns"
    assert not err.value.retriable
    assert route.call_count == 1


@respx.mock
def test_success_false_envelope_is_an_error(client: CloudflareClient) -> None:
    respx.get(DNS).mock(
        return_value=httpx.Response(
            200, json={"success": False, "errors": [{"code": 1, "message": "bad zone"}]}
        )
    )
    with pytest.raises(StageError, match="bad zone"):
        client.list_dns_records()


def test_credentials_absent_when_any_missing() -> None:
    env = {"CF_API_TOKEN": "t", "CF_ACCOUNT_ID": "a", "CF_ZONE_ID": "z"}
    assert Credentials.from_env(env) is None
    assert Credentials.from_env(env | {"CF_TUNNEL_ID": "u"}) is not None


# --- loop wiring ---

_CONFIG = (
    "domain: bebitwise.dev\nproxy_ip: 192.168.10.4\naccess:\n  allowed_emails: [me@example.com]\n"
)


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


def _unset_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("UNIFI_HOST", "UNIFI_API_KEY", "UNIFI_SITE_ID"):
        monkeypatch.delenv(name, raising=False)


def _set_cf(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (
        ("CF_API_TOKEN", "tok"),
        ("CF_ACCOUNT_ID", "acc"),
        ("CF_ZONE_ID", "zone"),
        ("CF_TUNNEL_ID", "tun"),
    ):
        monkeypatch.setenv(name, value)


def _mock_access_in_sync() -> None:
    respx.get(f"{API}/accounts/acc/access/apps").mock(return_value=_records())
    respx.get(f"{API}/accounts/acc/access/policies").mock(return_value=_records())
    ok = httpx.Response(200, json={"success": True, "result": {"id": "pol"}})
    respx.post(f"{API}/accounts/acc/access/policies").mock(return_value=ok)
    respx.post(f"{API}/accounts/acc/access/apps").mock(return_value=ok)


def test_stages_skipped_without_secrets_log_once(
    tmp_path: Path,
    app_data: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _unset_secrets(monkeypatch)
    for name in ("CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_ZONE_ID", "CF_TUNNEL_ID"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    with caplog.at_level(logging.INFO, logger="umbrel_edge.loop"):
        result = run_pass(_settings(tmp_path, app_data))
    assert result.ok
    assert sum("cloudflare stages skipped" in r.getMessage() for r in caplog.records) == 1


@respx.mock
def test_tunnel_failure_isolated_and_stops_dns_publishing(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unset_secrets(monkeypatch)
    _set_cf(monkeypatch)
    respx.get(TUNNEL).mock(return_value=httpx.Response(403))
    respx.get(DNS).mock(return_value=_records())
    _mock_access_in_sync()
    post = respx.post(DNS)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    result = run_pass(_settings(tmp_path, app_data))
    assert [e.stage for e in result.errors] == ["cloudflare_tunnel"]
    assert (tmp_path / "dynamic" / "apps.yml").exists()
    assert not post.called


@respx.mock
def test_dry_run_reads_only(
    tmp_path: Path,
    app_data: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _unset_secrets(monkeypatch)
    _set_cf(monkeypatch)
    respx.get(TUNNEL).mock(return_value=_config())
    respx.get(DNS).mock(return_value=_records(_record("r1", "gone.x.dev")))
    respx.get(f"{API}/accounts/acc/access/apps").mock(return_value=_records())
    respx.get(f"{API}/accounts/acc/access/policies").mock(return_value=_records())
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    result = run_pass(_settings(tmp_path, app_data), dry_run=True)
    assert result.ok
    assert all(call.request.method == "GET" for call in respx.calls)
    assert "- gone.x.dev" in capsys.readouterr().out
