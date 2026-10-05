"""Cloudflare client and reconcilers: tunnel ingress, public DNS and Access."""

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
ACCESS_STAGE = "cloudflare_access"
BASE_URL = "https://api.cloudflare.com/client/v4"
ORIGIN_SERVICE = "https://traefik:443"
CATCH_ALL = {"service": "http_status:404"}
MANAGED_COMMENT = "managed-by=umbrel-edge"
ACCESS_PREFIX = "umbrel-edge:"
POLICY_NAME = f"{ACCESS_PREFIX}allowed-emails"
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


def external_routes(state: DesiredState) -> list[Route]:
    """Every route that is public. Those with access true are only published once their Access
    app exists, which `loop.py` enforces by ordering the stages (see reconcile_access)."""
    return [r for r in state.routes if r.external]


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
        return self._list_all(DNS_STAGE, self._dns_path)

    def _list_all(self, stage: str, path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            body = self._call(stage, "GET", path, params={"page": page, "per_page": PAGE_SIZE})
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

    @property
    def _access_path(self) -> str:
        return f"/accounts/{self._creds.account_id}/access"

    def list_access_apps(self) -> list[dict[str, Any]]:
        return self._list_all(ACCESS_STAGE, f"{self._access_path}/apps")

    def create_access_app(self, body: dict[str, Any]) -> None:
        self._call(ACCESS_STAGE, "POST", f"{self._access_path}/apps", json=body)

    def update_access_app(self, app_id: str, body: dict[str, Any]) -> None:
        self._call(ACCESS_STAGE, "PUT", f"{self._access_path}/apps/{app_id}", json=body)

    def delete_access_app(self, app_id: str) -> None:
        self._call(ACCESS_STAGE, "DELETE", f"{self._access_path}/apps/{app_id}", missing_ok=True)

    def list_access_policies(self) -> list[dict[str, Any]]:
        return self._list_all(ACCESS_STAGE, f"{self._access_path}/policies")

    def create_access_policy(self, body: dict[str, Any]) -> str:
        result = self._call(ACCESS_STAGE, "POST", f"{self._access_path}/policies", json=body)
        inner = result.get("result")
        policy_id = inner.get("id") if isinstance(inner, dict) else None
        if not policy_id:
            raise StageError(ACCESS_STAGE, "policy created but the response carried no id")
        return str(policy_id)

    def update_access_policy(self, policy_id: str, body: dict[str, Any]) -> None:
        self._call(ACCESS_STAGE, "PUT", f"{self._access_path}/policies/{policy_id}", json=body)

    def delete_access_policy(self, policy_id: str) -> None:
        self._call(
            ACCESS_STAGE, "DELETE", f"{self._access_path}/policies/{policy_id}", missing_ok=True
        )

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


def unmanaged_clashes(state: DesiredState, records: list[dict[str, Any]]) -> frozenset[str]:
    """External hostnames whose public name already has a record we did not create. Such a name
    belongs to someone else's site, so it gets no ingress entry and no Access app."""
    taken = {str(r.get("name", "")).lower() for r in records if not _is_managed(r)}
    return frozenset(r.hostname for r in external_routes(state) if r.hostname.lower() in taken)


def without_clashes(state: DesiredState, clashes: frozenset[str]) -> DesiredState:
    """A copy of the state in which clashing names are no longer external."""
    routes = [
        r.model_copy(update={"external": False}) if r.external and r.hostname in clashes else r
        for r in state.routes
    ]
    return state.model_copy(update={"routes": routes})


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
    routes = external_routes(state)
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
    withheld: frozenset[str] = frozenset(),
    unprotected: frozenset[str] = frozenset(),
) -> DnsPlan:
    """publish=False (the tunnel stage failed) suppresses creates and updates, so a public name
    never points at a tunnel that may not know it yet. `withheld` hostnames (an Access app that
    is missing or could not be created) are never created or updated either. Deletions still
    run. `unprotected` hostnames are known to have no Access app, so their managed records are
    deleted: a name must never stay public without its app when access is true."""
    routes = external_routes(state)
    records = client.list_dns_records()
    result = plan_dns(sorted({r.hostname for r in routes}), records, client.creds.tunnel_target)
    for hostname in result.conflicts:
        log.warning(
            "cloudflare dns conflict: unmanaged record, skipped", extra={"hostname": hostname}
        )
    if not publish:
        result.create, result.update = [], {}
    held = {h.lower() for h in withheld}
    for hostname in [h for h in result.create if h.lower() in held]:
        result.create.remove(hostname)
        log.info("dns record held back: no Access app yet", extra={"hostname": hostname})
    for hostname in [h for h in result.update if h.lower() in held]:
        del result.update[hostname]
    bare = {h.lower() for h in unprotected}
    for record in records:
        name = str(record.get("name", "")).lower()
        if _is_managed(record) and name in bare:
            result.update.pop(next((h for h in result.update if h.lower() == name), ""), None)
            result.delete[f"{record['name']} (no Access app)"] = str(record["id"])
            log.warning("dns record removed: no Access app", extra={"hostname": name})
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


# --- Access ---------------------------------------------------------------------------------
#
# Policies are reusable: one allow policy named "umbrel-edge:allowed-emails" holds the address
# list, and every app references it by id. Cloudflare's current guidance is reusable policies,
# and app-scoped policies cannot be attached to new apps. It also means an address removed from
# edge.yaml disappears from every app with a single PUT.


def access_hostnames(state: DesiredState) -> list[str]:
    return sorted({r.hostname for r in external_routes(state) if r.access})


def _emails_of(policy: dict[str, Any]) -> list[str]:
    out: list[str] = []
    include = policy.get("include")
    for rule in include if isinstance(include, list) else []:
        email = rule.get("email") if isinstance(rule, dict) else None
        if isinstance(email, dict) and email.get("email"):
            out.append(str(email["email"]).lower())
    return sorted(out)


def _policy_body(emails: list[str]) -> dict[str, Any]:
    return {
        "name": POLICY_NAME,
        "decision": "allow",
        "include": [{"email": {"email": e}} for e in sorted(emails)],
    }


def _app_body(hostname: str, session_duration: str, policy_id: str) -> dict[str, Any]:
    return {
        "name": f"{ACCESS_PREFIX}{hostname}",
        "type": "self_hosted",
        "domain": hostname,
        "session_duration": session_duration,
        "policies": [{"id": policy_id, "precedence": 1}],
    }


def _policy_ids(app: dict[str, Any]) -> list[str]:
    policies = app.get("policies")
    return (
        sorted(str(p["id"]) for p in policies if isinstance(p, dict) and "id" in p)
        if (isinstance(policies, list))
        else []
    )


@dataclass
class AccessPlan:
    emails: list[str]
    session_duration: str
    policy_id: str | None = None  # None until the policy exists
    policy_action: str = ""  # "", "create", "update" or "delete"
    create: list[str] = field(default_factory=list)
    update: dict[str, str] = field(default_factory=dict)  # hostname -> app id
    delete: dict[str, str] = field(default_factory=dict)  # hostname -> app id
    conflicts: list[str] = field(default_factory=list)
    extra_policies: list[str] = field(default_factory=list)  # duplicate managed policy ids

    @property
    def missing(self) -> frozenset[str]:
        """Hostnames with no usable Access app yet: DNS must wait for these."""
        return frozenset(self.create)

    def describe(self) -> str:
        sign = {"create": "+", "update": "~", "delete": "-"}.get(self.policy_action)
        lines = [f"{sign} policy {POLICY_NAME}"] if sign else []
        lines += [f"- policy {POLICY_NAME} (duplicate {i})" for i in sorted(self.extra_policies)]
        lines += [f"+ access {h}" for h in sorted(self.create)]
        lines += [f"~ access {h}" for h in sorted(self.update)]
        lines += [f"- access {h}" for h in sorted(self.delete)]
        lines += [f"! {h} has an unmanaged Access app; skipped" for h in sorted(self.conflicts)]
        return "\n".join(lines) or "cloudflare_access: no changes"


def plan_access(
    want: list[str],
    apps: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    emails: list[str],
    session_duration: str,
) -> AccessPlan:
    wanted_emails = sorted({e.lower() for e in emails})
    plan = AccessPlan(emails=wanted_emails, session_duration=session_duration)
    managed_policies = [p for p in policies if p.get("name") == POLICY_NAME]
    policy = managed_policies[0] if managed_policies else None
    if policy:
        plan.policy_id = str(policy["id"])
        plan.extra_policies = [str(p["id"]) for p in managed_policies[1:]]
    if not want:
        plan.policy_action = "delete" if policy else ""
    elif policy is None:
        plan.policy_action = "create"
    elif _emails_of(policy) != wanted_emails or policy.get("decision") != "allow":
        plan.policy_action = "update"

    by_name: dict[str, list[dict[str, Any]]] = {}
    for app in apps:
        by_name.setdefault(str(app.get("name", "")), []).append(app)
    unmanaged_domains = {
        str(a.get("domain", "")).lower()
        for a in apps
        if not str(a.get("name", "")).startswith(ACCESS_PREFIX)
    }
    for hostname in want:
        same = by_name.get(f"{ACCESS_PREFIX}{hostname}", [])
        if not same:
            if hostname.lower() in unmanaged_domains:
                plan.conflicts.append(hostname)
            else:
                plan.create.append(hostname)
            continue
        first, *rest = same
        stale = (
            first.get("domain") != hostname
            or first.get("type") != "self_hosted"
            or first.get("session_duration") != session_duration
            or plan.policy_id is None
            or _policy_ids(first) != [plan.policy_id]
        )
        if stale:
            plan.update[hostname] = str(first["id"])
        for extra in rest:
            plan.delete[f"{hostname} (duplicate {extra['id']})"] = str(extra["id"])
    wanted = {h.lower() for h in want}
    for name, group in by_name.items():
        if name.startswith(ACCESS_PREFIX) and name[len(ACCESS_PREFIX) :].lower() not in wanted:
            for app in group:
                plan.delete[name[len(ACCESS_PREFIX) :]] = str(app["id"])
    return plan


def plan_access_from_api(
    client: CloudflareClient,
    state: DesiredState,
    emails: list[str],
    session_duration: str,
) -> AccessPlan:
    """Reads only."""
    return plan_access(
        access_hostnames(state),
        client.list_access_apps(),
        client.list_access_policies(),
        emails,
        session_duration,
    )


def apply_access_changes(client: CloudflareClient, plan: AccessPlan) -> None:
    """The create phase. Runs before the DNS stage, so a new public name already has its Access
    app. Policy first, because the apps reference its id."""
    if plan.policy_action == "create":
        plan.policy_id = client.create_access_policy(_policy_body(plan.emails))
        log.info("access policy created", extra={"emails": len(plan.emails)})
    elif plan.policy_action == "update" and plan.policy_id:
        client.update_access_policy(plan.policy_id, _policy_body(plan.emails))
        log.info("access policy updated", extra={"emails": len(plan.emails)})
    for hostname in plan.conflicts:
        log.warning(
            "cloudflare access conflict: unmanaged app, skipped", extra={"hostname": hostname}
        )
    if not (plan.create or plan.update):
        return
    if plan.policy_id is None:
        raise StageError(ACCESS_STAGE, "no allow policy to attach apps to")
    for hostname in plan.create:
        client.create_access_app(_app_body(hostname, plan.session_duration, plan.policy_id))
        log.info("access app created", extra={"hostname": hostname})
    for hostname, app_id in plan.update.items():
        client.update_access_app(app_id, _app_body(hostname, plan.session_duration, plan.policy_id))
        log.info("access app updated", extra={"hostname": hostname})


def apply_access_deletions(client: CloudflareClient, plan: AccessPlan) -> None:
    """The delete phase. Runs after the DNS stage, so a name is never public without its app.
    The shared policy goes last, once nothing references it."""
    for hostname, app_id in plan.delete.items():
        client.delete_access_app(app_id)
        log.info("access app deleted", extra={"hostname": hostname})
    for policy_id in plan.extra_policies:
        client.delete_access_policy(policy_id)
        log.info("duplicate access policy deleted")
    if plan.policy_action == "delete" and plan.policy_id:
        client.delete_access_policy(plan.policy_id)
        log.info("access policy deleted")
