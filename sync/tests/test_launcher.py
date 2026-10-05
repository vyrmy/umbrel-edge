from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml

from umbrel_edge import launcher, traefik_writer
from umbrel_edge.desired import build
from umbrel_edge.discovery import discover
from umbrel_edge.health import HealthState, serve
from umbrel_edge.models import AppPolicy, DesiredState, EdgeConfig

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "umbrel-route.yml"


def _state(config: EdgeConfig, app_data: Path) -> tuple[DesiredState, dict[int, str]]:
    manifests = discover(app_data)
    state = build(manifests, config)
    return state, state.port_map({m.id: m for m in manifests})


def _map(script: str) -> dict[str, str]:
    match = re.search(r"var map = (\{.*?\});", script, re.DOTALL)
    assert match
    parsed: dict[str, str] = json.loads(match.group(1))
    return parsed


def test_script_maps_every_routed_app(config: EdgeConfig, app_data: Path) -> None:
    _, ports = _state(config, app_data)
    mapped = _map(launcher.render(ports))
    assert mapped["8096"] == "jellyfin.bebitwise.dev"
    # On the external deny list, but still internal, so still mapped.
    assert mapped["8123"] == "home-assistant.bebitwise.dev"
    assert mapped["80"] == "umbrel.bebitwise.dev"
    # Excluded and portless apps get no entry, and nothing else is in the map.
    assert set(mapped.values()) == {
        "jellyfin.bebitwise.dev",
        "home-assistant.bebitwise.dev",
        "umbrel.bebitwise.dev",
    }


def test_custom_subdomain_is_used(config: EdgeConfig, app_data: Path) -> None:
    config.apps["jellyfin"] = AppPolicy(subdomain="films")
    _, ports = _state(config, app_data)
    assert _map(launcher.render(ports))["8096"] == "films.bebitwise.dev"


def test_apps_with_no_route_are_left_out(config: EdgeConfig, app_data: Path) -> None:
    config.apps["jellyfin"] = AppPolicy(internal=False, external=False)
    _, ports = _state(config, app_data)
    assert "8096" not in _map(launcher.render(ports))


def test_empty_map_is_valid() -> None:
    assert _map(launcher.render({})) == {}


def test_rendering_is_deterministic(config: EdgeConfig, app_data: Path) -> None:
    _, ports = _state(config, app_data)
    assert launcher.render(ports) == launcher.render(dict(reversed(ports.items())))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_script_rewrites_only_mapped_ports_on_the_current_host(tmp_path: Path) -> None:
    script = tmp_path / "launcher.js"
    script.write_text(launcher.render({8123: "home-assistant.example.com"}), encoding="utf-8")
    driver = tmp_path / "driver.js"
    driver.write_text(
        """
const opened = [];
global.window = { location: { hostname: "umbrel.example.com", href: "https://umbrel.example.com/" },
  open: (...args) => { opened.push(args[0]); } };
global.document = { addEventListener: () => {} };
require(process.argv[2]);
for (const u of [
  "https://umbrel.example.com:8123/lovelace?x=1",
  "https://umbrel.example.com:9999/",
  "https://other.example.com:8123/",
  "https://umbrel.example.com/settings",
]) window.open(u, "_blank");
console.log(JSON.stringify(opened));
""",
        encoding="utf-8",
    )
    out = subprocess.run(
        ["node", str(driver), str(script)], capture_output=True, text=True, check=True
    )
    assert json.loads(out.stdout) == [
        "https://home-assistant.example.com/lovelace?x=1",
        "https://umbrel.example.com:9999/",
        "https://other.example.com:8123/",
        "https://umbrel.example.com/settings",
    ]


def test_middleware_golden(config: EdgeConfig, app_data: Path) -> None:
    state, _ = _state(config, app_data)
    doc = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]
    generated = {
        "routers": {
            k: doc["routers"][k] for k in ("app-umbrel", "edge-assets", "edge-umbrel-fallback")
        },
        "services": {"edge-sync": doc["services"]["edge-sync"]},
        "middlewares": {"launcher-inject": doc["middlewares"]["launcher-inject"]},
    }
    assert generated == yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))


def test_other_routes_have_no_middleware(config: EdgeConfig, app_data: Path) -> None:
    state, _ = _state(config, app_data)
    routers = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]["routers"]
    assert "middlewares" not in routers["app-jellyfin"]
    assert "middlewares" not in routers["app-home-assistant"]


def test_no_dashboard_route_means_no_launcher_config(config: EdgeConfig, app_data: Path) -> None:
    state = build(discover(app_data, include_dashboard=False), config)
    doc = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]
    assert "edge-assets" not in doc["routers"]
    assert "launcher-inject" not in doc["middlewares"]


def test_server_serves_script_uncached() -> None:
    health = HealthState()
    health.record(routes=1, errors=[], launcher_js=launcher.render({8096: "jellyfin.example.com"}))
    server = serve(health, 0, host="127.0.0.1")
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__edge/launcher.js") as resp:
            assert resp.status == 200
            assert resp.headers["Cache-Control"] == "no-cache"
            assert resp.headers["Content-Type"].startswith("text/javascript")
            assert _map(resp.read().decode())["8096"] == "jellyfin.example.com"
        health.record(routes=1, errors=[], launcher_js=launcher.render({}))
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__edge/launcher.js") as resp:
            assert _map(resp.read().decode()) == {}
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_still_served_if_the_plugin_fails(config: EdgeConfig, app_data: Path) -> None:
    # Traefik drops a router whose middleware cannot be built. The fallback has the same rule
    # and no middleware, and loses to app-umbrel (priority = rule length) while that works.
    state, _ = _state(config, app_data)
    routers = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]["routers"]
    fallback = routers["edge-umbrel-fallback"]
    assert fallback["rule"] == routers["app-umbrel"]["rule"]
    assert fallback["service"] == "app-umbrel"
    assert "middlewares" not in fallback
    assert fallback["priority"] < len(routers["app-umbrel"]["rule"])


def test_no_dashboard_route_means_no_fallback(config: EdgeConfig, app_data: Path) -> None:
    state = build(discover(app_data, include_dashboard=False), config)
    routers = yaml.safe_load(traefik_writer.render(state, config.domain))["http"]["routers"]
    assert "edge-umbrel-fallback" not in routers
