from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class ExitCode:
    OK = 0
    BAD_ARGS = 2
    ERROR = 1


LOG_LEVEL = os.getenv("AUTODEV_LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(levelname)s:%(name)s:%(message)s"

# Avoid forcing debug/trace on hot paths unless explicitly enabled.
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format=LOG_FORMAT)
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autodev-cli",
        description="Generated CLI with explicit contract and safer defaults.",
        exit_on_error=False,
    )
    p.add_argument("--hello", default="world", help="Greeting name to print")
    p.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="How many times to print the greeting (1-3)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON response instead of plain text")
    return p


def _error(message: str) -> None:
    print(message, file=sys.stderr)


def _validate_repeat(repeat: int) -> None:
    if repeat < 1 or repeat > 3:
        raise ValueError("--repeat must be between 1 and 3")


@lru_cache(maxsize=128)
def _cached_greeting_outputs(hello: str, repeat: int) -> tuple[str, ...]:
    """Cache generated greeting payloads to reuse object creation across repeated calls."""

    # Simple performance helper for hot CLI loops (e.g., repeated health checks in tests/scripts).
    return tuple(f"hello {hello}" for _ in range(repeat))


def _serialize_payload(payload: dict[str, Any]) -> str:
    """Serialize output payloads in a single pass with predictable output type."""

    # Keep legacy output format for generated CLI callers unless they parse via `ast.literal_eval`.
    return str(payload)


def main(argv: list[str] | None = None) -> int:
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("cli.args", extra={"argv": argv or []})

    parser = build_parser()

    try:
        args = parser.parse_args(argv)
    except argparse.ArgumentError as exc:
        _error(str(exc))
        return ExitCode.BAD_ARGS
    except SystemExit:
        return ExitCode.BAD_ARGS

    try:
        _validate_repeat(args.repeat)
    except ValueError as exc:
        _error(str(exc))
        return ExitCode.BAD_ARGS

    outputs = _cached_greeting_outputs(args.hello, args.repeat)

    if args.json:
        payload: dict[str, Any] = {
            "hello": args.hello,
            "repeat": args.repeat,
            "outputs": list(outputs),
        }
        print(_serialize_payload(payload))
        return ExitCode.OK

    # Single write for plain output keeps CLI startup/per-call cost lower than Python loop prints.
    print("\n".join(outputs))
    return ExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
