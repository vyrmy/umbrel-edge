"""Cloudflare client and reconcilers: tunnel ingress and public DNS (Access follows in task 005)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from umbrel_edge.http import request_json
from umbrel_edge.models import DesiredState, Route, StageError

log = logging.getLogger(__name__)

TUNNEL_STAGE = "cloudflare_tunnel"
DNS_STAGE = "cloudflare_dns"
BASE_URL = "https://api.cloudflare.com/client/v4"
ORIGIN_SERVICE = "https://traefik:443"
CATCH_ALL = {"service": "http_status:404"}
MANAGED_COMMENT = "managed-by=umbrel-edge"
PAGE_SIZE = 100


@dataclass(frozen=True)
class Credentials:
    api_token: str
    account_id: str
    zone_id: str
    tunnel_id: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Credentials | None:
        """None when any secret is absent, so the stages are skipped rather than failed."""
        token, account, zone, tunnel = (
            env.get(n, "").strip()
            for n in ("CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_ZONE_ID", "CF_TUNNEL_ID")
        )
        if not (token and account and zone and tunnel):
            return None
        return cls(api_token=token, account_id=account, zone_id=zone, tunnel_id=tunnel)

    @property
    def tunnel_target(self) -> str:
        return f"{self.tunnel_id}.cfargotunnel.com"


def published(state: DesiredState) -> tuple[list[Route], list[Route]]:
    """Split external routes into (published, held back).

    Task 004 gate: until Cloudflare Access exists (task 005), a route with access true is not
    published, so nothing goes public without a login in front. Task 005 deletes the filter and
    returns every external route.
    """
    external = [r for r in state.routes if r.external]
    return [r for r in external if not r.access], [r for r in external if r.access]


def _log_held_back(held: list[Route]) -> None:
    for route in held:
        log.info(
            "route not published: Access is not implemented yet",
            extra={"hostname": route.hostname},
        )


class CloudflareClient:
    def __init__(
        self,
        creds: Credentials,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._creds = creds
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {creds.api_token}", "Accept": "application/json"},
            timeout=15.0,
            transport=transport,
        )

    @property
    def creds(self) -> Credentials:
        return self._creds

    def close(self) -> None:
        self._http.close()

    @property
    def _tunnel_path(self) -> str:
        c = self._creds
        return f"/accounts/{c.account_id}/cfd_tunnel/{c.tunnel_id}/configurations"

    @property
    def _dns_path(self) -> str:
        return f"/zones/{self._creds.zone_id}/dns_records"

    def get_ingress(self) -> list[dict[str, Any]]:
        result = self._call(TUNNEL_STAGE, "GET", self._tunnel_path).get("result")
        config = result.get("config") if isinstance(result, dict) else None
        ingress = config.get("ingress") if isinstance(config, dict) else None
        return [e for e in ingress if isinstance(e, dict)] if isinstance(ingress, list) else []

    def put_ingress(self, ingress: list[dict[str, Any]]) -> None:
        self._call(TUNNEL_STAGE, "PUT", self._tunnel_path, json={"config": {"ingress": ingress}})

    def list_dns_records(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            body = self._call(
                DNS_STAGE, "GET", self._dns_path, params={"page": page, "per_page": PAGE_SIZE}
            )
            result = body.get("result")
            out += [r for r in result if isinstance(r, dict)] if isinstance(result, list) else []
            info = body.get("result_info")
            total = int(info.get("total_pages", 1)) if isinstance(info, dict) else 1
            if page >= total:
                return out
            page += 1

    def create_dns(self, hostname: str) -> None:
        self._call(DNS_STAGE, "POST", self._dns_path, json=self._dns_body(hostname))

    def update_dns(self, record_id: str, hostname: str) -> None:
        self._call(
            DNS_STAGE, "PATCH", f"{self._dns_path}/{record_id}", json=self._dns_body(hostname)
        )

    def delete_dns(self, record_id: str) -> None:
        self._call(DNS_STAGE, "DELETE", f"{self._dns_path}/{record_id}", missing_ok=True)

    def _dns_body(self, hostname: str) -> dict[str, Any]:
        return {
            "type": "CNAME",
            "name": hostname,
            "content": self._creds.tunnel_target,
            "proxied": True,
            "ttl": 1,  # automatic, which is the only value a proxied record accepts
            "comment": MANAGED_COMMENT,
        }

    def _call(self, stage: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        body = request_json(self._http, stage, method, path, sleep=self._sleep, **kwargs)
        if body and body.get("success") is False:
            raise StageError(stage, f"{method} {path}: {_api_errors(body)}")
        return body


def _api_errors(body: dict[str, Any]) -> str:
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        return "; ".join(
            str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors
        )
    return "the API reported failure"


# --- Tunnel ingress -------------------------------------------------------------------------


def build_ingress(routes: list[Route]) -> list[dict[str, Any]]:
    """One entry per hostname in sorted order, then the mandatory catch-all."""
    entries: list[dict[str, Any]] = [
        {
            "hostname": hostname,
            "service": ORIGIN_SERVICE,
            "originRequest": {"originServerName": hostname},
        }
        for hostname in sorted({r.hostname for r in routes})
    ]
    return [*entries, dict(CATCH_ALL)]


def _normalise(ingress: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """The fields we set, so keys Cloudflare adds (such as an empty originRequest) are not drift."""
    out: list[tuple[str, str, str]] = []
    for entry in ingress:
        origin = entry.get("originRequest")
        sni = origin.get("originServerName", "") if isinstance(origin, dict) else ""
        out.append((str(entry.get("hostname", "")), str(entry.get("service", "")), str(sni)))
    return out


@dataclass
class TunnelPlan:
    ingress: list[dict[str, Any]]
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: bool = False

    def describe(self) -> str:
        lines = [f"+ ingress {h}" for h in self.added] + [f"- ingress {h}" for h in self.removed]
        if self.changed and not lines:
            lines = ["~ ingress entries differ"]
        return "\n".join(lines) or "cloudflare_tunnel: no changes"


def plan_tunnel(routes: list[Route], current: list[dict[str, Any]]) -> TunnelPlan:
    ingress = build_ingress(routes)
    have = {e.get("hostname") for e in current if e.get("hostname")}
    want = {e["hostname"] for e in ingress if "hostname" in e}
    return TunnelPlan(
        ingress=ingress,
        added=sorted(want - have),
        removed=sorted(str(h) for h in have - want),
        changed=_normalise(ingress) != _normalise(current),
    )


def reconcile_tunnel(
    client: CloudflareClient, state: DesiredState, *, dry_run: bool = False
) -> TunnelPlan:
    routes, held = published(state)
    _log_held_back(held)
    result = plan_tunnel(routes, client.get_ingress())
    if result.changed and not dry_run:
        client.put_ingress(result.ingress)
        log.info("tunnel ingress updated", extra={"hostnames": len(routes)})
    return result


# --- Public DNS -----------------------------------------------------------------------------


@dataclass
class DnsPlan:
    create: list[str] = field(default_factory=list)
    update: dict[str, str] = field(default_factory=dict)  # hostname -> record id
    delete: dict[str, str] = field(default_factory=dict)  # hostname -> record id
    conflicts: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.create or self.update or self.delete)

    def describe(self) -> str:
        lines = [f"+ {h}" for h in sorted(self.create)]
        lines += [f"~ {h}" for h in sorted(self.update)]
        lines += [f"- {h}" for h in sorted(self.delete)]
        lines += [f"! {h} exists and is not managed; skipped" for h in sorted(self.conflicts)]
        return "\n".join(lines) or "cloudflare_dns: no changes"


def _is_managed(record: dict[str, Any]) -> bool:
    return record.get("comment") == MANAGED_COMMENT


def plan_dns(want: list[str], records: list[dict[str, Any]], target: str) -> DnsPlan:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_name.setdefault(str(record.get("name", "")).lower(), []).append(record)
    result = DnsPlan()
    for hostname in want:
        same = by_name.get(hostname.lower(), [])
        managed = [r for r in same if _is_managed(r)]
        if len(managed) != len(same):
            result.conflicts.append(hostname)
        elif not managed:
            result.create.append(hostname)
        elif len(managed) == 1:
            r = managed[0]
            if r.get("type") != "CNAME" or r.get("content") != target or not r.get("proxied"):
                result.update[hostname] = str(r["id"])
        else:
            # Duplicate managed records: keep the first, delete the rest.
            first, *rest = managed
            if first.get("type") != "CNAME" or first.get("content") != target:
                result.update[hostname] = str(first["id"])
            result.delete.update({f"{hostname} (duplicate {r['id']})": str(r["id"]) for r in rest})
    wanted = {h.lower() for h in want}
    for record in records:
        name = str(record.get("name", "")).lower()
        if _is_managed(record) and name not in wanted:
            result.delete[str(record.get("name", ""))] = str(record["id"])
    return result


def reconcile_dns(
    client: CloudflareClient,
    state: DesiredState,
    *,
    dry_run: bool = False,
    publish: bool = True,
) -> DnsPlan:
    """publish=False (the tunnel stage failed) suppresses creates and updates, so a public name
    never points at a tunnel that may not know it yet. Deletions still run."""
    routes, held = published(state)
    _log_held_back(held)
    result = plan_dns(
        sorted({r.hostname for r in routes}),
        client.list_dns_records(),
        client.creds.tunnel_target,
    )
    for hostname in result.conflicts:
        log.warning(
            "cloudflare dns conflict: unmanaged record, skipped", extra={"hostname": hostname}
        )
    if not publish:
        result.create, result.update = [], {}
    if dry_run:
        return result
    for hostname in result.create:
        client.create_dns(hostname)
        log.info("dns record created", extra={"hostname": hostname})
    for hostname, record_id in result.update.items():
        client.update_dns(record_id, hostname)
        log.info("dns record updated", extra={"hostname": hostname})
    for hostname, record_id in result.delete.items():
        client.delete_dns(record_id)
        log.info("dns record deleted", extra={"hostname": hostname})
    return result
