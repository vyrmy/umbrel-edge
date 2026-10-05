"""Orchestration: one pass = config, discovery, desired state, then each stage in isolation."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass

from umbrel_edge import cloudflare, desired, discovery, launcher, traefik_writer, unifi
from umbrel_edge.config import Settings, load_edge_config
from umbrel_edge.health import HealthState
from umbrel_edge.models import DesiredState, EdgeConfig, StageError
from umbrel_edge.ownership import Ownership

log = logging.getLogger(__name__)


@dataclass
class PassResult:
    state: DesiredState | None
    errors: list[StageError]
    launcher_js: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def run_pass(settings: Settings, *, dry_run: bool = False) -> PassResult:
    errors: list[StageError] = []
    try:
        config = load_edge_config(settings.config_path)
        manifests = discovery.discover(settings.app_data_root)
        state = desired.build(manifests, config)
        launcher_js = launcher.render(state.port_map({m.id: m for m in manifests}))
    except StageError as exc:
        log.error("stage failed", extra={"stage": exc.stage, "detail": exc.message})
        return PassResult(state=None, errors=[exc])

    if dry_run:
        print(traefik_writer.render(state, config.domain))
        print(json.dumps([r.model_dump() for r in state.routes], indent=2))
        print(f"/__edge/launcher.js would serve:\n{launcher_js}")
        try:
            _unifi(state, config, settings, dry_run=True)
        except StageError as exc:
            log.error("stage failed", extra={"stage": exc.stage, "detail": exc.message})
            errors.append(exc)
        errors += _cloudflare(state, config, dry_run=True)
        return PassResult(state=state, errors=errors, launcher_js=launcher_js)

    try:
        _traefik(state, config.domain, settings)
    except StageError as exc:
        log.error("stage failed", extra={"stage": exc.stage, "detail": exc.message})
        errors.append(exc)
    try:
        _unifi(state, config, settings, dry_run=False)
    except StageError as exc:
        log.error("stage failed", extra={"stage": exc.stage, "detail": exc.message})
        errors.append(exc)
    errors += _cloudflare(state, config, dry_run=False)
    return PassResult(state=state, errors=errors, launcher_js=launcher_js)


def _traefik(state: DesiredState, domain: str, settings: Settings) -> None:
    if traefik_writer.write(state, domain, settings.traefik_dynamic_dir):
        log.info("traefik routes updated", extra={"routes": len(state.routes)})


def _unifi(state: DesiredState, config: EdgeConfig, settings: Settings, *, dry_run: bool) -> None:
    creds = unifi.Credentials.from_env(os.environ)
    if creds is None:
        log.info("unifi stage skipped: UNIFI_HOST, UNIFI_API_KEY or UNIFI_SITE_ID not set")
        return
    ownership = Ownership.load(settings.state_dir)
    client = unifi.UnifiClient(creds)
    try:
        result = unifi.reconcile(client, state, str(config.proxy_ip), ownership, dry_run=dry_run)
    finally:
        client.close()
    if dry_run:
        print(result.describe())


def _cloudflare(state: DesiredState, config: EdgeConfig, *, dry_run: bool) -> list[StageError]:
    """Tunnel ingress, Access creation, DNS, then Access deletion, each in its own try block.
    Ingress goes first so a public name never points at a tunnel that does not know it. Access
    apps are created before DNS and deleted after it, so a name is never public without its app
    when access is true."""
    creds = cloudflare.Credentials.from_env(os.environ)
    if creds is None:
        log.info(
            "cloudflare stages skipped: CF_API_TOKEN, CF_ACCOUNT_ID, CF_ZONE_ID "
            "or CF_TUNNEL_ID not set"
        )
        return []
    errors: list[StageError] = []

    def fail(exc: StageError) -> None:
        log.error("stage failed", extra={"stage": exc.stage, "detail": exc.message})
        errors.append(exc)

    client = cloudflare.CloudflareClient(creds)
    try:
        tunnel_ok = True
        try:
            plan = cloudflare.reconcile_tunnel(client, state, dry_run=dry_run)
            if dry_run:
                print(plan.describe())
        except StageError as exc:
            fail(exc)
            tunnel_ok = False

        # Until the Access plan is known and applied, no access-true name may be published.
        withheld = frozenset(cloudflare.access_hostnames(state))
        access_plan: cloudflare.AccessPlan | None = None
        try:
            access_plan = cloudflare.plan_access_from_api(
                client, state, config.access.allowed_emails, config.access.session_duration
            )
            if dry_run:
                print(access_plan.describe())
            else:
                cloudflare.apply_access_changes(client, access_plan)
            withheld = frozenset()
        except StageError as exc:
            fail(exc)
            if access_plan is not None:
                # Apps that already existed are still protected; only new ones must wait.
                withheld = access_plan.missing

        try:
            dns_plan = cloudflare.reconcile_dns(
                client,
                state,
                dry_run=dry_run,
                publish=tunnel_ok,
                withheld=frozenset() if dry_run else withheld,
            )
            if dry_run:
                print(dns_plan.describe())
        except StageError as exc:
            fail(exc)

        if access_plan is not None and not dry_run:
            try:
                cloudflare.apply_access_deletions(client, access_plan)
            except StageError as exc:
                fail(exc)
    finally:
        client.close()
    return errors


def run_forever(settings: Settings, health: HealthState) -> None:
    while True:
        result = run_pass(settings)
        health.record(
            routes=len(result.state.routes) if result.state else 0,
            errors=[{"stage": e.stage, "message": e.message} for e in result.errors],
            launcher_js=result.launcher_js,
        )
        _wait(settings)


def _wait(settings: Settings) -> None:
    """Sleep until the interval passes or edge.yaml changes, whichever is first."""
    start = _mtime(settings)
    deadline = time.monotonic() + settings.interval_seconds
    while time.monotonic() < deadline:
        time.sleep(2)
        if _mtime(settings) != start:
            return


def _mtime(settings: Settings) -> float | None:
    try:
        return settings.config_path.stat().st_mtime
    except OSError:
        return None
