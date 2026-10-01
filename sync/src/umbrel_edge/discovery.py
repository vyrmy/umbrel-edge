"""Reads installed apps from <app-data>/<id>/umbrel-app.yml. Read-only, never raises per app."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from umbrel_edge.models import AppManifest

log = logging.getLogger(__name__)

# The Umbrel dashboard itself is not an app, but it gets a route like one.
DASHBOARD = AppManifest(id="umbrel", name="Umbrel", port=80)


def discover(app_data_root: Path, *, include_dashboard: bool = True) -> list[AppManifest]:
    manifests: list[AppManifest] = [DASHBOARD] if include_dashboard else []
    if not app_data_root.is_dir():
        log.warning("app-data root %s is missing", app_data_root)
        return manifests
    for app_dir in sorted(p for p in app_data_root.iterdir() if p.is_dir()):
        manifest = _read_manifest(app_dir / "umbrel-app.yml")
        if manifest is not None:
            manifests.append(manifest)
    return manifests


def _read_manifest(path: Path) -> AppManifest | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, yaml.YAMLError) as exc:
        log.warning("skipping %s: unreadable (%s)", path, exc)
        return None
    if not isinstance(raw, dict):
        log.warning("skipping %s: not a mapping", path)
        return None
    if "port" not in raw:
        log.warning("skipping %s: no port", path)
        return None
    try:
        return AppManifest.model_validate(raw)
    except ValidationError as exc:
        log.warning("skipping %s: %s", path, exc.errors(include_url=False))
        return None
