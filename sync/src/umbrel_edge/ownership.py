"""ownership.json: the UniFi DNS policy ids this service created, keyed by hostname."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from umbrel_edge.models import StageError

FILE_NAME = "ownership.json"
VERSION = 1


class Ownership:
    def __init__(self, path: Path, records: dict[str, str] | None = None) -> None:
        self.path = path
        self.unifi_records: dict[str, str] = dict(records or {})

    @classmethod
    def load(cls, state_dir: Path) -> Ownership:
        path = state_dir / FILE_NAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(path)
        except (OSError, ValueError) as exc:
            # Never start again from empty: that would orphan every record we created.
            raise StageError("unifi", f"cannot read {path}: {exc}") from exc
        records = raw.get("unifi_records") if isinstance(raw, dict) else None
        if not isinstance(records, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in records.items()
        ):
            raise StageError("unifi", f"{path} is not a valid ownership file")
        return cls(path, records)

    def save(self) -> None:
        """Atomic write: temp file, fsync, rename."""
        content = json.dumps(
            {"version": VERSION, "unifi_records": self.unifi_records}, indent=2, sort_keys=True
        )
        directory = self.path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".ownership.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(content + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise StageError("unifi", f"cannot write {self.path}: {exc}", retriable=True) from exc
