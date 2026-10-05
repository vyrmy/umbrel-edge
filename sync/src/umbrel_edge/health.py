"""Last-pass state and the /healthz endpoint (stdlib only)."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from umbrel_edge import launcher

STALE_AFTER = timedelta(minutes=5)
LAUNCHER_PATH = "/__edge/launcher.js"


@dataclass
class HealthState:
    last_success: datetime | None = None
    routes: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    launcher_js: str = field(default_factory=lambda: launcher.render({}))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self, routes: int, errors: list[dict[str, str]], launcher_js: str | None = None
    ) -> None:
        with self._lock:
            if launcher_js is not None:
                self.launcher_js = launcher_js
            self.errors = errors
            self.routes = routes
            if not errors:
                self.last_success = datetime.now(UTC)

    def launcher(self) -> str:
        with self._lock:
            return self.launcher_js

    def snapshot(self, now: datetime | None = None) -> tuple[int, dict[str, object]]:
        now = now or datetime.now(UTC)
        with self._lock:
            last = self.last_success.isoformat() if self.last_success else None
            fresh = self.last_success is not None and now - self.last_success < STALE_AFTER
            if fresh and not self.errors:
                return 200, {"status": "ok", "last_success": last, "routes": self.routes}
            return 503, {"status": "degraded", "last_success": last, "errors": list(self.errors)}


def serve(state: HealthState, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == LAUNCHER_PATH:
                code, payload, kind = 200, state.launcher().encode(), "text/javascript"
            elif self.path == "/healthz":
                code, body = state.snapshot()
                payload, kind = json.dumps(body).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(code)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            if kind == "text/javascript":
                self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="healthz", daemon=True).start()
    return server
