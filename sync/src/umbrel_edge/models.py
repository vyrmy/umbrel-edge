"""Data model shared by every stage. See docs/architecture/ARCHITECTURE.md, Data model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

SUBDOMAIN_RE = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"


class AppManifest(BaseModel):
    """What discovery reads from app-data/<id>/umbrel-app.yml."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    port: int = Field(ge=1, le=65535)
    path: str = ""


class AppPolicy(BaseModel):
    """Per-app override in edge.yaml. None means 'use the default'."""

    model_config = ConfigDict(extra="forbid")

    subdomain: str | None = Field(default=None, pattern=SUBDOMAIN_RE)
    internal: bool | None = None
    external: bool | None = None
    access: bool | None = None
    upstream_port: int | None = Field(default=None, ge=1, le=65535)
    upstream_scheme: Literal["http", "https"] = "http"


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal: bool = True
    external: bool = True
    access: bool = True


class AccessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_emails: list[str] = Field(min_length=1)
    session_duration: str = "24h"


class EdgeConfig(BaseModel):
    """The whole of edge.yaml."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    proxy_ip: IPvAnyAddress
    defaults: Defaults = Defaults()
    external_deny: list[str] = [
        "home-assistant",
        "portainer",
        "code-server",
        "termix",
        "torbrowser",
        "wireguard",
        "tailscale",
        "vyrmy-edge",
    ]
    exclude: list[str] = ["mosquitto"]
    access: AccessSettings
    apps: dict[str, AppPolicy] = {}


class Route(BaseModel):
    """One app's resolved desired state."""

    model_config = ConfigDict(frozen=True)

    app_id: str
    hostname: str
    upstream: str
    internal: bool
    external: bool
    access: bool


class DesiredState(BaseModel):
    routes: list[Route]

    def port_map(self, manifests: dict[str, AppManifest]) -> dict[int, str]:
        """Host port to hostname, for the dashboard launcher (task 007)."""
        return {manifests[r.app_id].port: r.hostname for r in self.routes if r.app_id in manifests}


class StageError(Exception):
    """Raised by any stage. loop.py records it per stage and carries on."""

    def __init__(self, stage: str, message: str, *, retriable: bool = False) -> None:
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.message = message
        self.retriable = retriable
