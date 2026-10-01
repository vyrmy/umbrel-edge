"""Last-pass state and the /healthz endpoint (stdlib only)."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STALE_AFTER = timedelta(minutes=5)


@dataclass
class HealthState:
    last_success: datetime | None = None
    routes: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, routes: int, errors: list[dict[str, str]]) -> None:
        with self._lock:
            self.errors = errors
            self.routes = routes
            if not errors:
                self.last_success = datetime.now(UTC)

    def snapshot(self, now: datetime | None = None) -> tuple[int, dict[str, object]]:
        now = now or datetime.now(UTC)
        with self._lock:
            last = self.last_success.isoformat() if self.last_success else None
            fresh = self.last_success is not None and now - self.last_success < STALE_AFTER
            if fresh and not self.errors:
                return 200, {"status": "ok", "last_success": last, "routes": self.routes}
            return 503, {"status": "degraded", "last_success": last, "errors": list(self.errors)}


def serve(state: HealthState, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/healthz":
                self.send_error(404)
                return
            code, body = state.snapshot()
            payload = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="healthz", daemon=True).start()
    return server
