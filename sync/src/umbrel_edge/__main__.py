"""CLI: run the loop (default), --once, or --dry-run."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime

from umbrel_edge.config import Settings
from umbrel_edge.health import HealthState, serve
from umbrel_edge.loop import run_forever, run_pass

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        return json.dumps(entry, default=str)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="umbrel_edge")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = parser.parse_args(argv)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])

    settings = Settings.from_env()
    if args.dry_run or args.once:
        result = run_pass(settings, dry_run=args.dry_run)
        for err in result.errors:
            logging.getLogger("umbrel_edge").error(err.message, extra={"stage": err.stage})
        return 0 if result.ok else 1

    health = HealthState()
    serve(health, settings.health_port)
    run_forever(settings, health)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
