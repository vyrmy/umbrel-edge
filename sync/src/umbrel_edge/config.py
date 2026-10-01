"""Runtime settings from the environment, and edge.yaml loading."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from umbrel_edge.models import EdgeConfig, StageError


@dataclass(frozen=True)
class Settings:
    config_path: Path
    app_data_root: Path
    traefik_dynamic_dir: Path
    state_dir: Path
    interval_seconds: int
    health_port: int
    self_app_id: str

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            config_path=Path(env.get("EDGE_CONFIG", "/data/edge.yaml")),
            app_data_root=Path(env.get("EDGE_APP_DATA_ROOT", "/umbrel/app-data")),
            traefik_dynamic_dir=Path(env.get("EDGE_TRAEFIK_DYNAMIC_DIR", "/data/traefik/dynamic")),
            state_dir=Path(env.get("EDGE_STATE_DIR", "/data/state")),
            interval_seconds=int(env.get("EDGE_INTERVAL_SECONDS", "60")),
            health_port=int(env.get("EDGE_HEALTH_PORT", "9000")),
            self_app_id=env.get("EDGE_SELF_APP_ID", "vyrmy-edge"),
        )


def load_edge_config(path: Path) -> EdgeConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StageError("config", f"{path} does not exist; copy edge.example.yaml") from exc
    except yaml.YAMLError as exc:
        raise StageError("config", f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise StageError("config", f"{path} must be a mapping at the top level")
    try:
        return EdgeConfig.model_validate(raw)
    except ValidationError as exc:
        raise StageError("config", f"{path} failed validation: {exc}") from exc
