"""Orchestration: one pass = config, discovery, desired state, then each stage in isolation."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from umbrel_edge import desired, discovery, traefik_writer
from umbrel_edge.config import Settings, load_edge_config
from umbrel_edge.health import HealthState
from umbrel_edge.models import DesiredState, StageError

log = logging.getLogger(__name__)


@dataclass
class PassResult:
    state: DesiredState | None
    errors: list[StageError]

    @property
    def ok(self) -> bool:
        return not self.errors


def run_pass(settings: Settings, *, dry_run: bool = False) -> PassResult:
    errors: list[StageError] = []
    try:
        config = load_edge_config(settings.config_path)
        manifests = discovery.discover(settings.app_data_root)
        state = desired.build(manifests, config)
    except StageError as exc:
        log.error("stage failed", extra={"stage": exc.stage, "detail": exc.message})
        return PassResult(state=None, errors=[exc])

    if dry_run:
        print(traefik_writer.render(state, config.domain))
        print(json.dumps([r.model_dump() for r in state.routes], indent=2))
        return PassResult(state=state, errors=[])

    try:
        _traefik(state, config.domain, settings)
    except StageError as exc:
        log.error("stage failed", extra={"stage": exc.stage, "detail": exc.message})
        errors.append(exc)
    # Tasks 003 to 005 add the UniFi and Cloudflare stages here, each in its own try block.
    return PassResult(state=state, errors=errors)


def _traefik(state: DesiredState, domain: str, settings: Settings) -> None:
    if traefik_writer.write(state, domain, settings.traefik_dynamic_dir):
        log.info("traefik routes updated", extra={"routes": len(state.routes)})


def run_forever(settings: Settings, health: HealthState) -> None:
    while True:
        result = run_pass(settings)
        health.record(
            routes=len(result.state.routes) if result.state else 0,
            errors=[{"stage": e.stage, "message": e.message} for e in result.errors],
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
