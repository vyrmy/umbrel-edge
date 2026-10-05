from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from umbrel_edge import cloudflare, loop, traefik_writer
from umbrel_edge.config import Settings
from umbrel_edge.health import HealthState
from umbrel_edge.loop import PassResult, run_pass

CF = "https://api.cloudflare.com/client/v4"
TUNNEL = f"{CF}/accounts/acc/cfd_tunnel/tun/configurations"
DNS = f"{CF}/zones/zone/dns_records"
APPS = f"{CF}/accounts/acc/access/apps"
POLICIES = f"{CF}/accounts/acc/access/policies"
UNIFI = "https://192.168.10.1/proxy/network/integration/v1/sites/site-1/dns/policies"
CONFIG = (
    "domain: bebitwise.dev\nproxy_ip: 192.168.10.4\naccess:\n  allowed_emails: [me@example.com]\n"
)
OK = httpx.Response(200, json={"success": True, "result": {"id": "pol"}})


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


def _page(*items: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200, json={"success": True, "result": list(items), "result_info": {"total_pages": 1}}
    )


def _env(monkeypatch: pytest.MonkeyPatch, *, unifi: bool) -> None:
    secrets = {"CF_API_TOKEN": "tok", "CF_ACCOUNT_ID": "acc", "CF_ZONE_ID": "zone"}
    secrets |= {"CF_TUNNEL_ID": "tun"}
    if unifi:
        secrets |= {"UNIFI_HOST": "192.168.10.1", "UNIFI_API_KEY": "key", "UNIFI_SITE_ID": "site-1"}
    else:
        for name in ("UNIFI_HOST", "UNIFI_API_KEY", "UNIFI_SITE_ID"):
            monkeypatch.delenv(name, raising=False)
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)


def _mock_cloudflare(apps: httpx.Response | None = None) -> dict[str, respx.Route]:
    respx.get(TUNNEL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": {}}})
    )
    respx.get(DNS).mock(return_value=_page())
    respx.get(APPS).mock(return_value=apps or _page())
    respx.get(POLICIES).mock(return_value=_page())
    respx.post(POLICIES).mock(return_value=OK)
    respx.post(APPS).mock(return_value=OK)
    return {
        "tunnel": respx.put(TUNNEL).mock(return_value=OK),
        "dns": respx.post(DNS).mock(return_value=OK),
    }


@respx.mock
def test_unexpected_unifi_error_is_isolated(
    tmp_path: Path,
    app_data: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _env(monkeypatch, unifi=True)
    (tmp_path / "edge.yaml").write_text(CONFIG)
    # A null data list makes the client raise a plain TypeError, not a StageError.
    respx.get(UNIFI).mock(return_value=httpx.Response(200, json={"data": None}))
    cf = _mock_cloudflare()

    with caplog.at_level(logging.ERROR, logger="umbrel_edge.loop"):
        result = run_pass(_settings(tmp_path, app_data))

    assert [e.stage for e in result.errors] == ["unifi"]
    assert "TypeError" in result.errors[0].message
    assert cf["tunnel"].called
    assert cf["dns"].called
    assert any(r.exc_info for r in caplog.records)


@respx.mock
def test_unexpected_error_in_one_cloudflare_stage_lets_the_rest_run(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, unifi=False)
    (tmp_path / "edge.yaml").write_text(CONFIG)
    gone = {
        "id": "g1",
        "name": "umbrel-edge:gone.bebitwise.dev",
        "type": "self_hosted",
        "domain": "gone.bebitwise.dev",
    }
    _mock_cloudflare(apps=_page(gone))
    delete_app = respx.delete(f"{APPS}/g1").mock(return_value=OK)

    def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(cloudflare, "reconcile_dns", broken)
    result = run_pass(_settings(tmp_path, app_data))

    assert [e.stage for e in result.errors] == ["cloudflare_dns"]
    assert result.errors[0].message == "RuntimeError('bug')"
    assert delete_app.called


@respx.mock
def test_unexpected_traefik_error_is_isolated(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, unifi=False)
    (tmp_path / "edge.yaml").write_text(CONFIG)
    cf = _mock_cloudflare()

    def broken(*args: object, **kwargs: object) -> bool:
        raise ValueError("bad")

    monkeypatch.setattr(traefik_writer, "write", broken)
    result = run_pass(_settings(tmp_path, app_data))

    assert [e.stage for e in result.errors] == ["traefik"]
    assert cf["tunnel"].called


class _Stop(BaseException):
    pass


def test_run_forever_survives_an_error_escaping_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes: list[PassResult | Exception] = [
        KeyError("boom"),
        PassResult(state=None, errors=[]),
    ]
    seen: list[list[dict[str, str]]] = []
    health = HealthState()

    def fake_pass(settings: Settings, *, dry_run: bool = False) -> PassResult:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def fake_wait(settings: Settings) -> None:
        seen.append(list(health.errors))
        if not outcomes:
            raise _Stop

    monkeypatch.setattr(loop, "run_pass", fake_pass)
    monkeypatch.setattr(loop, "_wait", fake_wait)
    with pytest.raises(_Stop):
        loop.run_forever(_settings(tmp_path, tmp_path), health)

    assert seen[0] == [{"stage": "loop", "message": "KeyError('boom')"}]
    assert seen[1] == []
    assert health.last_success is not None
