"""UniFi Network Integration API client and DNS reconciler (A records for internal routes)."""

from __future__ import annotations

import functools
import hashlib
import logging
import re
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from umbrel_edge.http import request_json
from umbrel_edge.models import DesiredState, StageError
from umbrel_edge.ownership import Ownership

log = logging.getLogger(__name__)

STAGE = "unifi"
TTL_SECONDS = 300
PAGE_SIZE = 200  # the documented maximum for the list endpoint
PIN_ENV = "UNIFI_CERT_SHA256"
DEFAULT_PORT = 443
FETCH_TIMEOUT_SECONDS = 10.0
# Gets the PEM certificate a server presents. Injectable so tests need no network.
FetchCertificate = Callable[[str, int], str]


def normalise_fingerprint(value: str) -> str:
    """A SHA-256 fingerprint as 64 lower-case hex digits, from hex with optional colons."""
    digits = value.strip().replace(":", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digits):
        raise StageError(STAGE, f"{PIN_ENV} must be a SHA-256 fingerprint: 64 hex digits")
    return digits


@dataclass(frozen=True)
class Credentials:
    host: str
    api_key: str
    site_id: str
    cert_sha256: str | None = None  # normalised; None leaves the connection unverified

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Credentials | None:
        """None when any secret is absent, so the stage is skipped rather than failed.
        Reads the environment only; a malformed pin is an error, never a silent fallback to
        an unverified connection."""
        host, key, site = (
            env.get(n, "").strip() for n in ("UNIFI_HOST", "UNIFI_API_KEY", "UNIFI_SITE_ID")
        )
        if not (host and key and site):
            return None
        pin = env.get(PIN_ENV, "").strip()
        return cls(
            host=host,
            api_key=key,
            site_id=site,
            cert_sha256=normalise_fingerprint(pin) if pin else None,
        )


@dataclass
class Plan:
    create: dict[str, str] = field(default_factory=dict)  # hostname -> ip
    update: dict[str, tuple[str, str]] = field(default_factory=dict)  # hostname -> (id, ip)
    delete: dict[str, str] = field(default_factory=dict)  # hostname -> id
    conflicts: list[str] = field(default_factory=list)
    forget: list[str] = field(default_factory=list)  # owned hostnames already gone upstream
    # hostname -> id of a record our own unfinished create made (see ownership.pending)
    adopt: dict[str, str] = field(default_factory=dict)
    clear_pending: list[str] = field(default_factory=list)  # no record to recover, or not ours

    @property
    def empty(self) -> bool:
        return not (
            self.create
            or self.update
            or self.delete
            or self.forget
            or self.adopt
            or self.clear_pending
        )

    def describe(self) -> str:
        lines = [f"+ {h} -> {ip}" for h, ip in sorted(self.create.items())]
        lines += [f"~ {h} -> {ip}" for h, (_, ip) in sorted(self.update.items())]
        lines += [f"- {h}" for h in sorted(self.delete)]
        lines += [f"= {h} created by an unfinished pass; adopted" for h in sorted(self.adopt)]
        lines += [f"! {h} exists and is not owned; skipped" for h in sorted(self.conflicts)]
        return "\n".join(lines) or "unifi: no changes"


def _fetch_certificate(host: str, port: int) -> str:
    return ssl.get_server_certificate((host, port), timeout=FETCH_TIMEOUT_SECONDS)


def _display(fingerprint: str) -> str:
    """Colon-separated upper case, as `openssl x509 -fingerprint` prints it."""
    return ":".join(fingerprint[i : i + 2] for i in range(0, len(fingerprint), 2)).upper()


def _address(host: str) -> tuple[str, int]:
    try:
        parts = urlsplit(f"//{host}")
        name, port = parts.hostname, parts.port
    except ValueError as exc:
        raise StageError(STAGE, f"UNIFI_HOST {host!r} is not a host or host:port") from exc
    if not name:
        raise StageError(STAGE, f"UNIFI_HOST {host!r} is not a host or host:port")
    return name, port or DEFAULT_PORT


@functools.cache
def _warn_unverified() -> None:
    log.warning(
        "unifi connection is unverified: %s is not set. Pin the gateway certificate by setting "
        "it to the output of: openssl s_client -connect <host>:443 </dev/null 2>/dev/null "
        "| openssl x509 -noout -fingerprint -sha256 (the value after the =)",
        PIN_ENV,
    )


def verify_for(
    creds: Credentials, *, fetch: FetchCertificate = _fetch_certificate
) -> ssl.SSLContext | bool:
    """The `verify=` for the client. With a pin, the gateway's certificate is fetched and checked
    against it before anything is sent, and the result trusts that certificate alone, so every
    later connection must present it. Without one, verification stays off."""
    if creds.cert_sha256 is None:
        _warn_unverified()
        return False
    host, port = _address(creds.host)
    try:
        pem = fetch(host, port)
    except OSError as exc:
        raise StageError(
            STAGE, f"cannot fetch the certificate from {host}:{port}: {exc}", retriable=True
        ) from exc
    actual = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    if actual != creds.cert_sha256:
        raise StageError(
            STAGE,
            f"the certificate at {host}:{port} has SHA-256 {_display(actual)}, but {PIN_ENV} is "
            f"{_display(creds.cert_sha256)}; the API key was not sent",
        )
    # The pinned certificate is the only trust anchor, so the name on it does not matter.
    context = ssl.create_default_context(cadata=pem)
    context.check_hostname = False
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


class UnifiClient:
    def __init__(
        self,
        creds: Credentials,
        *,
        verify: ssl.SSLContext | bool = False,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        # UniFi uses a self-signed certificate: `verify` is a context that trusts only the
        # pinned one (see verify_for), or False when no pin is set.
        self._http = httpx.Client(
            base_url=f"https://{creds.host}/proxy/network/integration/v1/sites/{creds.site_id}",
            headers={"X-API-KEY": creds.api_key, "Accept": "application/json"},
            verify=verify,
            timeout=15.0,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def list_a_records(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._request(
                "GET", "/dns/policies", params={"offset": offset, "limit": PAGE_SIZE}
            )
            data = page.get("data", [])
            out += [p for p in data if p.get("type") == "A_RECORD"]
            offset += len(data)
            if not data or offset >= int(page.get("totalCount", 0)):
                return out

    def create(self, hostname: str, ip: str) -> str:
        created = self._request("POST", "/dns/policies", json=_body(hostname, ip))
        policy_id = created.get("id")
        if not isinstance(policy_id, str):
            raise StageError(STAGE, f"create {hostname}: response carried no id", retriable=True)
        return policy_id

    def update(self, policy_id: str, hostname: str, ip: str) -> None:
        self._request("PUT", f"/dns/policies/{policy_id}", json=_body(hostname, ip))

    def delete(self, policy_id: str) -> None:
        self._request("DELETE", f"/dns/policies/{policy_id}", missing_ok=True)

    def _request(
        self, method: str, path: str, *, missing_ok: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        return request_json(
            self._http, STAGE, method, path, sleep=self._sleep, missing_ok=missing_ok, **kwargs
        )


def _body(hostname: str, ip: str) -> dict[str, Any]:
    return {
        "type": "A_RECORD",
        "enabled": True,
        "domain": hostname,
        "ipv4Address": ip,
        "ttlSeconds": TTL_SECONDS,
    }


def wanted(state: DesiredState, proxy_ip: str) -> dict[str, str]:
    return {r.hostname: proxy_ip for r in state.routes if r.internal}


def plan(
    want: dict[str, str], existing: list[dict[str, Any]], ownership: Ownership, proxy_ip: str
) -> Plan:
    """A record sync did not create is never modified, deleted or adopted. The one exception is
    a hostname in `ownership.pending`: sync sent a create for it and never stored the id, so a
    record there with the proxy address is that create, and is taken back."""
    owned = ownership.unifi_records
    owned_ids = set(owned.values())
    by_domain: dict[str, dict[str, Any]] = {}
    for policy in existing:
        domain = str(policy.get("domain", "")).lower()
        # If a name is duplicated, the owned record is the one we manage.
        if domain not in by_domain or policy.get("id") in owned_ids:
            by_domain[domain] = policy
    by_id = {p.get("id"): p for p in existing}
    result = Plan()
    for hostname in sorted(ownership.pending):
        match = by_domain.get(hostname.lower())
        if hostname in owned:
            result.clear_pending.append(hostname)
        elif match is None:
            # Nothing was created. A wanted name is simply created again below.
            if hostname not in want:
                result.clear_pending.append(hostname)
        elif match.get("ipv4Address") == proxy_ip and match.get("id"):
            result.adopt[hostname] = str(match["id"])
        else:
            # Someone else's record took the name: it stays a conflict, never ours.
            result.clear_pending.append(hostname)
    for hostname, ip in want.items():
        match = by_domain.get(hostname.lower())
        if match is None:
            result.create[hostname] = ip
        elif hostname in result.adopt:
            if match.get("ipv4Address") != ip or not match.get("enabled", True):
                result.update[hostname] = (result.adopt[hostname], ip)
        elif match.get("id") != owned.get(hostname):
            result.conflicts.append(hostname)
        elif match.get("ipv4Address") != ip or not match.get("enabled", True):
            result.update[hostname] = (str(match["id"]), ip)
    for hostname, policy_id in [*owned.items(), *result.adopt.items()]:
        if hostname in want:
            continue
        if policy_id in by_id:
            result.delete[hostname] = policy_id
        else:
            result.forget.append(hostname)
    return result


def reconcile(
    client: UnifiClient,
    state: DesiredState,
    proxy_ip: str,
    ownership: Ownership,
    *,
    dry_run: bool = False,
) -> Plan:
    result = plan(wanted(state, proxy_ip), client.list_a_records(), ownership, proxy_ip)
    for hostname in result.conflicts:
        log.warning("unifi conflict: unowned record, skipped", extra={"hostname": hostname})
    if dry_run:
        return result
    for hostname, policy_id in result.adopt.items():
        ownership.unifi_records[hostname] = policy_id
        ownership.pending.discard(hostname)
        ownership.save()
        log.info("unifi record from an unfinished create adopted", extra={"hostname": hostname})
    if result.clear_pending:
        ownership.pending.difference_update(result.clear_pending)
        ownership.save()
    for hostname, ip in result.create.items():
        # Record the intent first: if the response or the next save is lost, the next pass
        # knows a record under this name with the proxy address is ours.
        ownership.pending.add(hostname)
        ownership.save()
        try:
            ownership.unifi_records[hostname] = client.create(hostname, ip)
        except StageError as exc:
            if not exc.retriable:
                # A definite refusal (4xx): nothing was created, so there is nothing to
                # recover, and a record made later under this name must not look like ours.
                ownership.pending.discard(hostname)
                ownership.save()
            raise
        ownership.pending.discard(hostname)
        ownership.save()
        log.info("unifi record created", extra={"hostname": hostname})
    for hostname, (policy_id, ip) in result.update.items():
        client.update(policy_id, hostname, ip)
        log.info("unifi record updated", extra={"hostname": hostname})
    for hostname, policy_id in result.delete.items():
        client.delete(policy_id)
        del ownership.unifi_records[hostname]
        ownership.save()
        log.info("unifi record deleted", extra={"hostname": hostname})
    for hostname in result.forget:
        del ownership.unifi_records[hostname]
        ownership.save()
    return result
