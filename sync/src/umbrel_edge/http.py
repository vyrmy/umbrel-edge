"""Shared HTTP retry policy: 429 and 5xx retry with backoff, other 4xx do not."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from umbrel_edge.models import StageError

ATTEMPTS = 3
# Failures before any byte of the request was sent, so repeating even a POST is safe.
SAFE_TO_REPEAT = (httpx.ConnectError, httpx.ConnectTimeout)


def request_json(
    http: httpx.Client,
    stage: str,
    method: str,
    path: str,
    *,
    sleep: Callable[[float], None],
    missing_ok: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Send one request with up to ATTEMPTS tries. Returns the JSON object, or {} for no body."""
    last = ""
    for attempt in range(ATTEMPTS):
        if attempt:
            sleep(2.0 ** (attempt - 1))
        try:
            resp = http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            last = f"{method} {path}: {exc}"
            # A POST that may have reached the server is not repeated: the next pass re-plans,
            # whereas a blind retry could create the resource twice.
            if method == "POST" and not isinstance(exc, SAFE_TO_REPEAT):
                raise StageError(stage, last, retriable=True) from exc
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            last = f"{method} {path}: HTTP {resp.status_code}"
            continue
        if resp.status_code == 404 and missing_ok:
            return {}
        if resp.status_code >= 400:
            raise StageError(stage, f"{method} {path}: HTTP {resp.status_code}")
        if not resp.content:
            return {}
        try:
            body = resp.json()
        except ValueError as exc:
            raise StageError(stage, f"{method} {path}: response is not JSON") from exc
        return body if isinstance(body, dict) else {}
    raise StageError(stage, f"{last} after {ATTEMPTS} attempts", retriable=True)
