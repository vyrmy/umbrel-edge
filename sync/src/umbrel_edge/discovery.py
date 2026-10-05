"""Reads installed apps from <app-data>/<id>/umbrel-app.yml. Read-only, never raises per app.

umbreld lists the ids of installed apps under `apps` in umbrel.yaml, beside app-data. A folder
whose id is not in that list belongs to no installed app and is skipped. If the list cannot be
read, every folder counts as installed, so a bad read never withdraws every route."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from umbrel_edge.models import AppManifest

log = logging.getLogger(__name__)

# The Umbrel dashboard itself is not an app, but it gets a route like one.
DASHBOARD = AppManifest(id="umbrel", name="Umbrel", port=80)

INSTALLED_APPS_FILE = "umbrel.yaml"

# Folders already reported as skipped, so each is logged when it starts being skipped rather
# than on every pass.
_reported_skips: set[str] = set()


def discover(app_data_root: Path, *, include_dashboard: bool = True) -> list[AppManifest]:
    manifests: list[AppManifest] = [DASHBOARD] if include_dashboard else []
    if not app_data_root.is_dir():
        log.warning("app-data root %s is missing", app_data_root)
        return manifests
    installed_path = app_data_root.parent / INSTALLED_APPS_FILE
    installed = _read_installed(installed_path)
    skipped: set[str] = set()
    for app_dir in sorted(p for p in app_data_root.iterdir() if p.is_dir()):
        if installed is not None and app_dir.name not in installed:
            if app_dir.name not in _reported_skips:
                log.info("skipping %s: not an installed app in %s", app_dir, installed_path)
            skipped.add(app_dir.name)
            continue
        manifest = _read_manifest(app_dir / "umbrel-app.yml")
        if manifest is not None:
            manifests.append(manifest)
    if installed is not None:
        _reported_skips.clear()
        _reported_skips.update(skipped)
    return manifests


def _read_installed(path: Path) -> frozenset[str] | None:
    """The app ids umbreld lists as installed, or None when the list cannot be used."""
    fallback = "treating every app-data folder as installed"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("%s is missing; %s", path, fallback)
        return None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        log.warning("%s is unreadable (%s); %s", path, exc, fallback)
        return None
    apps = raw.get("apps") if isinstance(raw, dict) else None
    if not isinstance(apps, list) or not all(isinstance(app_id, str) for app_id in apps):
        log.warning("%s has no list of app ids under apps; %s", path, fallback)
        return None
    return frozenset(apps)


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
