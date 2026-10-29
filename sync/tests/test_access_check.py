"""Task 011: cloudflared checks the Access token on every access-true hostname (decision 21)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from umbrel_edge.cloudflare import (
    CloudflareClient,
    Credentials,
    apply_access_changes,
    build_ingress,
    plan_access,
    plan_tunnel,
)
from umbrel_edge.config import Settings
from umbrel_edge.loop import run_pass
from umbrel_edge.models import AccessSettings, Route

API = "https://api.cloudflare.com/client/v4"
APPS = f"{API}/accounts/acc/access/apps"
POLICIES = f"{API}/accounts/acc/access/policies"
DNS = f"{API}/zones/zone/dns_records"
TUNNEL = f"{API}/accounts/acc/cfd_tunnel/tun/configurations"
OK = httpx.Response(200, json={"success": True})
POLICY = {
    "id": "pol",
    "name": "umbrel-edge:allowed-emails",
    "decision": "allow",
    "include": [{"email": {"email": "me@example.com"}}],
}
CONFIG = (
    "domain: bebitwise.dev\nproxy_ip: 192.168.10.4\n"
    "access:\n  allowed_emails: [me@example.com]\n  team_name: home\n"
)


def _route(host: str, *, access: bool = True) -> Route:
    return Route(
        app_id=host.split(".")[0],
        hostname=host,
        upstream="http://host.docker.internal:80",
        internal=True,
        external=True,
        access=access,
    )


def _app(host: str, aid: str, aud: str, *, managed: bool = True) -> dict[str, Any]:
    return {
        "id": aid,
        "name": f"umbrel-edge:{host}" if managed else f"mine {host}",
        "type": "self_hosted",
        "domain": host,
        "session_duration": "24h",
        "policies": [{"id": "pol", "precedence": 1}],
        "aud": aud,
    }


def _page(*items: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200, json={"success": True, "result": list(items), "result_info": {"total_pages": 1}}
    )


def _ingress(*entries: dict[str, Any]) -> httpx.Response:
    ingress = [*entries, {"service": "http_status:404"}]
    return httpx.Response(200, json={"success": True, "result": {"config": {"ingress": ingress}}})


def _entry(host: str, aud: str | None) -> dict[str, Any]:
    origin: dict[str, Any] = {"originServerName": host}
    if aud:
        origin["access"] = {"required": True, "teamName": "home", "audTag": [aud]}
    return {"hostname": host, "service": "https://traefik:8443", "originRequest": origin}


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


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("UNIFI_HOST", "UNIFI_API_KEY", "UNIFI_SITE_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (
        ("CF_API_TOKEN", "tok"),
        ("CF_ACCOUNT_ID", "acc"),
        ("CF_ZONE_ID", "zone"),
        ("CF_TUNNEL_ID", "tun"),
    ):
        monkeypatch.setenv(name, value)


def _created(request: httpx.Request) -> httpx.Response:
    domain = json.loads(request.content)["domain"]
    result = {"id": "new-" + domain, "aud": "aud-" + domain}
    return httpx.Response(200, json={"success": True, "result": result})


def _non_get() -> list[str]:
    return [
        f"{c.request.method} {c.request.url.path.removeprefix('/client/v4')}"
        for c in respx.calls
        if c.request.method != "GET"
    ]


def _put_ingress(put: respx.Route) -> dict[str, dict[str, Any]]:
    sent = json.loads(put.calls[-1].request.content)["config"]["ingress"]
    return {e["hostname"]: e for e in sent if "hostname" in e}


# --- the ingress body ---


def test_team_name_must_be_a_bare_team() -> None:
    AccessSettings(allowed_emails=["me@example.com"], team_name="home-1")
    with pytest.raises(ValidationError):
        AccessSettings(allowed_emails=["me@example.com"], team_name="home.cloudflareaccess.com")


def test_ingress_without_team_name_is_unchanged() -> None:
    (entry, _) = build_ingress([_route("a.x.dev")], auds={"a.x.dev": "aud-a"})
    assert entry == _entry("a.x.dev", None)


def test_ingress_with_team_name_checks_access_true_routes_only() -> None:
    ingress = build_ingress(
        [_route("a.x.dev"), _route("open.x.dev", access=False)],
        team_name="home",
        auds={"a.x.dev": "aud-a"},
    )
    assert ingress[0] == _entry("a.x.dev", "aud-a")
    assert ingress[1] == _entry("open.x.dev", None)


def test_access_true_route_without_aud_is_left_out() -> None:
    ingress = build_ingress([_route("a.x.dev")], team_name="home", auds={})
    assert ingress == [{"service": "http_status:404"}]


def test_changed_or_dropped_check_is_drift() -> None:
    routes = [_route("a.x.dev")]
    tail = {"service": "http_status:404"}
    same = plan_tunnel(
        routes, [_entry("a.x.dev", "aud-a"), tail], team_name="home", auds={"a.x.dev": "aud-a"}
    )
    assert not same.changed
    for current in ([_entry("a.x.dev", "old"), tail], [_entry("a.x.dev", None), tail]):
        plan = plan_tunnel(routes, current, team_name="home", auds={"a.x.dev": "aud-a"})
        assert plan.changed and not plan.added and not plan.removed


# --- where the aud comes from ---


def test_plan_keeps_the_aud_of_managed_and_owner_apps() -> None:
    apps = [_app("a.x.dev", "a1", "aud-a"), _app("b.x.dev", "b1", "aud-b", managed=False)]
    plan = plan_access(["a.x.dev", "b.x.dev", "c.x.dev"], apps, [POLICY], ["me@example.com"], "24h")
    assert plan.aud == {"a.x.dev": "aud-a", "b.x.dev": "aud-b"}
    assert plan.without_aud(["a.x.dev", "b.x.dev", "c.x.dev"]) == {"c.x.dev"}


@respx.mock
def test_created_app_supplies_its_aud() -> None:
    respx.post(APPS).mock(side_effect=_created)
    client = CloudflareClient(Credentials("tok", "acc", "zone", "tun"), sleep=lambda _: None)
    plan = plan_access(["c.x.dev"], [], [POLICY], ["me@example.com"], "24h")
    apply_access_changes(client, plan)
    assert plan.aud == {"c.x.dev": "aud-c.x.dev"}


# --- the pass ---


@respx.mock
def test_apps_are_created_before_the_ingress_that_needs_their_aud(
    tmp_path: Path, app_data: Path, env: None
) -> None:
    (tmp_path / "edge.yaml").write_text(CONFIG)
    respx.get(TUNNEL).mock(return_value=_ingress())
    put = respx.put(TUNNEL).mock(return_value=OK)
    respx.get(DNS).mock(return_value=_page())
    respx.get(APPS).mock(return_value=_page())
    respx.get(POLICIES).mock(return_value=_page(POLICY))
    respx.post(APPS).mock(side_effect=_created)
    respx.post(DNS).mock(return_value=OK)

    result = run_pass(_settings(tmp_path, app_data))

    assert result.ok
    calls = _non_get()
    first_app = calls.index("POST /accounts/acc/access/apps")
    ingress = calls.index("PUT /accounts/acc/cfd_tunnel/tun/configurations")
    first_dns = calls.index("POST /zones/zone/dns_records")
    assert first_app < ingress < first_dns
    sent = _put_ingress(put)
    assert sent
    for host, entry in sent.items():
        assert entry["originRequest"]["access"] == {
            "required": True,
            "teamName": "home",
            "audTag": ["aud-" + host],
        }


@respx.mock
def test_failed_create_keeps_that_name_out_of_ingress_and_dns(
    tmp_path: Path, app_data: Path, env: None
) -> None:
    # The app was deleted by hand: the name is in the ingress and DNS, and recreating fails.
    (tmp_path / "edge.yaml").write_text(CONFIG)
    host = "jellyfin.bebitwise.dev"
    respx.get(TUNNEL).mock(return_value=_ingress(_entry(host, "aud-old")))
    put = respx.put(TUNNEL).mock(return_value=OK)
    respx.get(DNS).mock(
        return_value=_page({"id": "d1", "name": host, "comment": "managed-by=umbrel-edge"})
    )
    respx.get(APPS).mock(return_value=_page())
    respx.get(POLICIES).mock(return_value=_page(POLICY))
    respx.post(APPS).mock(return_value=httpx.Response(403))
    post_dns = respx.post(DNS).mock(return_value=OK)
    delete_dns = respx.delete(f"{DNS}/d1").mock(return_value=OK)

    result = run_pass(_settings(tmp_path, app_data))

    assert [e.stage for e in result.errors] == ["cloudflare_access"]
    assert host not in _put_ingress(put)
    assert not post_dns.called
    assert delete_dns.called


@respx.mock
def test_unreadable_access_apps_leave_the_ingress_alone(
    tmp_path: Path, app_data: Path, env: None
) -> None:
    (tmp_path / "edge.yaml").write_text(CONFIG)
    respx.get(TUNNEL).mock(return_value=_ingress(_entry("jellyfin.bebitwise.dev", "aud-j")))
    put = respx.put(TUNNEL).mock(return_value=OK)
    respx.get(DNS).mock(return_value=_page())
    respx.get(APPS).mock(return_value=httpx.Response(403))
    post_dns = respx.post(DNS).mock(return_value=OK)

    result = run_pass(_settings(tmp_path, app_data))

    assert [e.stage for e in result.errors] == ["cloudflare_access"]
    assert not put.called
    assert not post_dns.called


@respx.mock
def test_existing_apps_keep_their_names_checked_with_no_writes(
    tmp_path: Path, app_data: Path, env: None
) -> None:
    (tmp_path / "edge.yaml").write_text(CONFIG)
    respx.get(POLICIES).mock(return_value=_page(POLICY))
    respx.get(TUNNEL).mock(return_value=_ingress())
    put = respx.put(TUNNEL).mock(return_value=OK)
    respx.get(DNS).mock(return_value=_page())
    respx.post(DNS).mock(return_value=OK)
    # First pass learns which hostnames are wanted.
    respx.get(APPS).mock(return_value=_page())
    respx.post(APPS).mock(side_effect=_created)
    run_pass(_settings(tmp_path, app_data))
    hosts = sorted(_put_ingress(put))
    assert hosts
    respx.get(APPS).mock(return_value=_page(*(_app(h, "id-" + h, "aud-" + h) for h in hosts)))
    post_app = respx.post(APPS).mock(side_effect=_created)
    respx.get(TUNNEL).mock(return_value=_ingress(*(_entry(h, "aud-" + h) for h in hosts)))
    # respx returns the same route for the same pattern, so clear the first pass's calls.
    put.reset()
    post_app.reset()

    result = run_pass(_settings(tmp_path, app_data))

    assert result.ok
    assert not post_app.called
    assert not put.called
