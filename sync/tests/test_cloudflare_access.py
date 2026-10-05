from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from umbrel_edge.cloudflare import (
    POLICY_NAME,
    CloudflareClient,
    Credentials,
    apply_access_changes,
    apply_access_deletions,
    plan_access,
    plan_access_from_api,
)
from umbrel_edge.config import Settings
from umbrel_edge.loop import run_pass
from umbrel_edge.models import DesiredState, Route, StageError

API = "https://api.cloudflare.com/client/v4"
APPS = f"{API}/accounts/acc/access/apps"
POLICIES = f"{API}/accounts/acc/access/policies"
DNS = f"{API}/zones/zone/dns_records"
TUNNEL = f"{API}/accounts/acc/cfd_tunnel/tun/configurations"
EMAILS = ["me@example.com"]
OK = httpx.Response(200, json={"success": True})


def _route(host: str, *, access: bool = True, external: bool = True) -> Route:
    return Route(
        app_id=host.split(".")[0],
        hostname=host,
        upstream="http://host.docker.internal:80",
        internal=True,
        external=external,
        access=access,
    )


def _page(*items: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200, json={"success": True, "result": list(items), "result_info": {"total_pages": 1}}
    )


def _policy(emails: list[str] | None = None, pid: str = "pol", name: str = POLICY_NAME) -> Any:
    return {
        "id": pid,
        "name": name,
        "decision": "allow",
        "include": [{"email": {"email": e}} for e in (emails or EMAILS)],
    }


def _app(host: str, aid: str = "a1", **extra: Any) -> dict[str, Any]:
    app: dict[str, Any] = {
        "id": aid,
        "name": f"umbrel-edge:{host}",
        "type": "self_hosted",
        "domain": host,
        "session_duration": "24h",
        "policies": [{"id": "pol", "precedence": 1}],
    }
    return app | extra


@pytest.fixture
def client() -> CloudflareClient:
    sleeps: list[float] = []
    c = CloudflareClient(Credentials("tok", "acc", "zone", "tun"), sleep=sleeps.append)
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


# --- diffing ---


def test_in_sync_plans_nothing() -> None:
    plan = plan_access(["a.x.dev"], [_app("a.x.dev")], [_policy()], EMAILS, "24h")
    assert not (plan.create or plan.update or plan.delete or plan.policy_action)
    assert plan.describe() == "cloudflare_access: no changes"


def test_new_route_creates_policy_and_app() -> None:
    plan = plan_access(["a.x.dev"], [], [], EMAILS, "24h")
    assert plan.policy_action == "create"
    assert plan.create == ["a.x.dev"]
    assert plan.missing == {"a.x.dev"}


def test_session_duration_change_updates_the_app() -> None:
    plan = plan_access(["a.x.dev"], [_app("a.x.dev")], [_policy()], EMAILS, "12h")
    assert plan.update == {"a.x.dev": "a1"}


def test_policy_pointing_elsewhere_updates_the_app() -> None:
    app = _app("a.x.dev", policies=[{"id": "other"}])
    plan = plan_access(["a.x.dev"], [app], [_policy()], EMAILS, "24h")
    assert plan.update == {"a.x.dev": "a1"}


def test_email_change_updates_the_single_policy() -> None:
    plan = plan_access(
        ["a.x.dev"], [_app("a.x.dev")], [_policy(["old@example.com"])], EMAILS, "24h"
    )
    assert plan.policy_action == "update"
    assert not plan.update


def test_removed_route_deletes_app_and_last_one_deletes_policy() -> None:
    plan = plan_access([], [_app("gone.x.dev", "g1")], [_policy()], EMAILS, "24h")
    assert plan.delete == {"gone.x.dev": "g1"}
    assert plan.policy_action == "delete"


def test_duplicate_managed_apps_keep_the_first() -> None:
    apps = [_app("a.x.dev", "a1"), _app("a.x.dev", "a2")]
    plan = plan_access(["a.x.dev"], apps, [_policy()], EMAILS, "24h")
    assert list(plan.delete.values()) == ["a2"]


# --- ownership ---


def test_unprefixed_apps_are_never_modified_or_deleted() -> None:
    mine = {"id": "u1", "name": "Grafana", "domain": "grafana.x.dev", "type": "self_hosted"}
    plan = plan_access([], [mine], [_policy(name="Corporate policy", pid="u2")], EMAILS, "24h")
    assert not plan.delete and plan.policy_action == ""


def test_unmanaged_app_on_the_same_domain_is_a_conflict() -> None:
    theirs = {"id": "u1", "name": "Mine", "domain": "a.x.dev", "type": "self_hosted"}
    plan = plan_access(["a.x.dev"], [theirs], [_policy()], EMAILS, "24h")
    assert plan.conflicts == ["a.x.dev"]
    assert not plan.create
    assert "! a.x.dev" in plan.describe()


@respx.mock
def test_conflict_is_logged_and_nothing_written(
    client: CloudflareClient, caplog: pytest.LogCaptureFixture
) -> None:
    theirs = {"id": "u1", "name": "Mine", "domain": "a.x.dev", "type": "self_hosted"}
    respx.get(APPS).mock(return_value=_page(theirs))
    respx.get(POLICIES).mock(return_value=_page(_policy()))
    plan = plan_access_from_api(client, DesiredState(routes=[_route("a.x.dev")]), EMAILS, "24h")
    with caplog.at_level(logging.WARNING, logger="umbrel_edge.cloudflare"):
        apply_access_changes(client, plan)
    assert all(c.request.method == "GET" for c in respx.calls)
    assert any("conflict" in r.getMessage() for r in caplog.records)


# --- request shapes ---


@respx.mock
def test_requests_use_reusable_policy_and_prefixed_names(client: CloudflareClient) -> None:
    respx.get(APPS).mock(return_value=_page())
    respx.get(POLICIES).mock(return_value=_page())
    pol = respx.post(POLICIES).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"id": "pol9"}})
    )
    app = respx.post(APPS).mock(return_value=OK)
    plan = plan_access_from_api(client, DesiredState(routes=[_route("a.x.dev")]), EMAILS, "24h")
    apply_access_changes(client, plan)
    assert json.loads(pol.calls[0].request.content) == {
        "name": POLICY_NAME,
        "decision": "allow",
        "include": [{"email": {"email": "me@example.com"}}],
    }
    assert json.loads(app.calls[0].request.content) == {
        "name": "umbrel-edge:a.x.dev",
        "type": "self_hosted",
        "domain": "a.x.dev",
        "session_duration": "24h",
        "policies": [{"id": "pol9", "precedence": 1}],
    }


@respx.mock
def test_pagination_and_4xx_error_stage(client: CloudflareClient) -> None:
    respx.get(APPS).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "success": True,
                    "result": [_app("a.x.dev")],
                    "result_info": {"total_pages": 2},
                },
            ),
            _page(_app("b.x.dev", "a2")),
        ]
    )
    assert len(client.list_access_apps()) == 2
    respx.get(POLICIES).mock(return_value=httpx.Response(403))
    with pytest.raises(StageError) as err:
        client.list_access_policies()
    assert err.value.stage == "cloudflare_access"
    assert not err.value.retriable


# --- ordering through a full pass ---

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


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("UNIFI_HOST", "UNIFI_API_KEY", "UNIFI_SITE_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (
        ("CF_API_TOKEN", "tok"),
        ("CF_ACCOUNT_ID", "acc"),
        ("CF_ZONE_ID", "zone"),
        ("CF_TUNNEL_ID", "tun"),
    ):
        monkeypatch.setenv(name, value)


def _calls() -> list[str]:
    return [
        f"{c.request.method} {c.request.url.path.removeprefix('/client/v4')}"
        for c in respx.calls
        if c.request.method != "GET"
    ]


@respx.mock
def test_access_created_before_dns_and_deleted_after(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    respx.get(TUNNEL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": {}}})
    )
    respx.put(TUNNEL).mock(return_value=OK)
    # A removed route has a managed DNS record and a managed Access app.
    respx.get(DNS).mock(
        return_value=_page(
            {"id": "d1", "name": "gone.bebitwise.dev", "comment": "managed-by=umbrel-edge"}
        )
    )
    respx.get(APPS).mock(return_value=_page(_app("gone.bebitwise.dev", "g1")))
    respx.get(POLICIES).mock(return_value=_page(_policy()))
    respx.post(DNS).mock(return_value=OK)
    respx.delete(f"{DNS}/d1").mock(return_value=OK)
    respx.post(APPS).mock(return_value=OK)
    respx.delete(f"{APPS}/g1").mock(return_value=OK)

    result = run_pass(_settings(tmp_path, app_data))

    assert result.ok
    calls = _calls()
    create_app = calls.index("POST /accounts/acc/access/apps")
    create_dns = calls.index("POST /zones/zone/dns_records")
    delete_dns = calls.index("DELETE /zones/zone/dns_records/d1")
    delete_app = calls.index("DELETE /accounts/acc/access/apps/g1")
    assert create_app < create_dns
    assert delete_dns < delete_app


@respx.mock
def test_failed_access_create_withholds_dns_for_that_route(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    respx.get(TUNNEL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": {}}})
    )
    respx.put(TUNNEL).mock(return_value=OK)
    respx.get(DNS).mock(return_value=_page())
    respx.get(APPS).mock(return_value=_page())
    respx.get(POLICIES).mock(return_value=_page(_policy()))
    respx.post(APPS).mock(return_value=httpx.Response(403))
    post_dns = respx.post(DNS).mock(return_value=OK)

    result = run_pass(_settings(tmp_path, app_data))

    assert [e.stage for e in result.errors] == ["cloudflare_access"]
    assert not post_dns.called


@respx.mock
def test_access_listing_failure_withholds_dns_but_not_tunnel(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    put = respx.put(TUNNEL).mock(return_value=OK)
    respx.get(TUNNEL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": {}}})
    )
    respx.get(DNS).mock(return_value=_page())
    respx.get(APPS).mock(return_value=httpx.Response(403))
    post_dns = respx.post(DNS).mock(return_value=OK)

    result = run_pass(_settings(tmp_path, app_data))

    assert [e.stage for e in result.errors] == ["cloudflare_access"]
    assert put.called and not post_dns.called


@respx.mock
def test_dry_run_reads_only_and_prints_the_access_diff(
    tmp_path: Path,
    app_data: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _env(monkeypatch)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    respx.get(TUNNEL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": {}}})
    )
    respx.get(DNS).mock(return_value=_page())
    respx.get(APPS).mock(return_value=_page(_app("gone.bebitwise.dev", "g1")))
    respx.get(POLICIES).mock(return_value=_page())

    result = run_pass(_settings(tmp_path, app_data), dry_run=True)

    assert result.ok
    assert all(c.request.method == "GET" for c in respx.calls)
    out = capsys.readouterr().out
    assert "+ access jellyfin.bebitwise.dev" in out
    assert "- access gone.bebitwise.dev" in out


@respx.mock
def test_access_false_deletes_only_that_apps_access_app(client: CloudflareClient) -> None:
    respx.get(APPS).mock(return_value=_page(_app("a.x.dev", "a1"), _app("b.x.dev", "b1")))
    respx.get(POLICIES).mock(return_value=_page(_policy()))
    delete_b = respx.delete(f"{APPS}/b1").mock(return_value=OK)
    state = DesiredState(routes=[_route("a.x.dev"), _route("b.x.dev", access=False)])
    plan = plan_access_from_api(client, state, EMAILS, "24h")
    apply_access_changes(client, plan)
    apply_access_deletions(client, plan)
    assert delete_b.call_count == 1
    assert [c.request.method for c in respx.calls].count("DELETE") == 1


@respx.mock
def test_unmanaged_dns_record_gets_no_access_app_or_ingress(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    respx.get(TUNNEL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": {}}})
    )
    put = respx.put(TUNNEL).mock(return_value=OK)
    # The existing public site already uses the jellyfin name, with no managed comment.
    respx.get(DNS).mock(
        return_value=_page({"id": "w1", "name": "jellyfin.bebitwise.dev", "type": "CNAME"})
    )
    respx.get(APPS).mock(return_value=_page())
    respx.get(POLICIES).mock(return_value=_page(_policy()))
    post_apps = respx.post(APPS).mock(return_value=OK)
    post_dns = respx.post(DNS).mock(return_value=OK)

    result = run_pass(_settings(tmp_path, app_data))

    assert result.ok
    assert not any("jellyfin" in c.request.content.decode() for c in post_apps.calls)
    assert "jellyfin.bebitwise.dev" not in put.calls.last.request.content.decode()
    assert not any("jellyfin" in c.request.content.decode() for c in post_dns.calls)


@respx.mock
def test_failed_access_create_removes_existing_dns_record(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    respx.get(TUNNEL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": {}}})
    )
    respx.put(TUNNEL).mock(return_value=OK)
    respx.get(DNS).mock(
        return_value=_page(
            {
                "id": "d9",
                "name": "jellyfin.bebitwise.dev",
                "type": "CNAME",
                "content": "tun.cfargotunnel.com",
                "proxied": True,
                "comment": "managed-by=umbrel-edge",
            }
        )
    )
    respx.get(APPS).mock(return_value=_page())
    respx.get(POLICIES).mock(return_value=_page(_policy()))
    respx.post(APPS).mock(return_value=httpx.Response(403))
    delete = respx.delete(f"{DNS}/d9").mock(return_value=OK)

    result = run_pass(_settings(tmp_path, app_data))

    assert [e.stage for e in result.errors] == ["cloudflare_access"]
    assert delete.called


@respx.mock
def test_access_listing_failure_keeps_existing_dns_record(
    tmp_path: Path, app_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    (tmp_path / "edge.yaml").write_text(_CONFIG)
    respx.get(TUNNEL).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {"config": {}}})
    )
    respx.put(TUNNEL).mock(return_value=OK)
    respx.get(DNS).mock(
        return_value=_page(
            {
                "id": "d9",
                "name": "jellyfin.bebitwise.dev",
                "type": "CNAME",
                "content": "tun.cfargotunnel.com",
                "proxied": True,
                "comment": "managed-by=umbrel-edge",
            }
        )
    )
    respx.get(APPS).mock(return_value=httpx.Response(403))
    delete = respx.delete(f"{DNS}/d9").mock(return_value=OK)

    run_pass(_settings(tmp_path, app_data))

    assert not delete.called


def test_duplicate_managed_policies_are_collapsed() -> None:
    plan = plan_access(
        ["a.example.com"], [_app("a.example.com")], [_policy(), _policy(pid="dup")], EMAILS, "24h"
    )
    assert plan.policy_id == "pol"
    assert plan.extra_policies == ["dup"]


@respx.mock
def test_policy_post_is_not_retried_after_a_read_timeout(client: CloudflareClient) -> None:
    post = respx.post(POLICIES).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(StageError) as err:
        client.create_access_policy({"name": POLICY_NAME})
    assert post.call_count == 1
    assert err.value.retriable
