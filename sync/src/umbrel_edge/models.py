"""Data model shared by every stage. See docs/architecture/ARCHITECTURE.md, Data model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, model_validator

SUBDOMAIN_RE = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"

# The headers Authentik's Traefik docs pass through from the outpost to the app.
DEFAULT_AUTH_RESPONSE_HEADERS = [
    "X-authentik-username",
    "X-authentik-groups",
    "X-authentik-entitlements",
    "X-authentik-email",
    "X-authentik-name",
    "X-authentik-uid",
    "X-authentik-jwt",
    "X-authentik-meta-jwks",
    "X-authentik-meta-outpost",
    "X-authentik-meta-provider",
    "X-authentik-meta-app",
    "X-authentik-meta-version",
]


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
    forward_auth: bool | None = None


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal: bool = True
    external: bool = True
    access: bool = True
    forward_auth: bool = False


class ForwardAuthSettings(BaseModel):
    """Authentik's embedded outpost, used by Traefik's forwardAuth middleware (decision 14)."""

    model_config = ConfigDict(extra="forbid")

    address: str
    outpost_url: str
    response_headers: list[str] = Field(default_factory=lambda: list(DEFAULT_AUTH_RESPONSE_HEADERS))


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
        "arcane",
        "code-server",
        "termix",
        "denny-olivetin",
        "torbrowser",
        "denny-librewolf",
        "wireguard",
        "tailscale",
        "vyrmy-edge",
    ]
    exclude: list[str] = ["mosquitto"]
    access: AccessSettings
    forward_auth: ForwardAuthSettings | None = None
    apps: dict[str, AppPolicy] = {}

    @model_validator(mode="after")
    def _forward_auth_needs_settings(self) -> EdgeConfig:
        if self.forward_auth is not None:
            return self
        wanting = sorted(app_id for app_id, p in self.apps.items() if p.forward_auth)
        if self.defaults.forward_auth:
            wanting.insert(0, "defaults.forward_auth")
        if wanting:
            raise ValueError(
                "forward_auth is on for " + ", ".join(wanting) + " but there is no top-level "
                "forward_auth block"
            )
        return self


class Route(BaseModel):
    """One app's resolved desired state."""

    model_config = ConfigDict(frozen=True)

    app_id: str
    hostname: str
    upstream: str
    internal: bool
    external: bool
    access: bool
    forward_auth: bool = False


class DesiredState(BaseModel):
    routes: list[Route]
    # Set only when at least one route is protected.
    forward_auth: ForwardAuthSettings | None = None

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
