"""Shared logging utilities for last30days skill."""

import os
import sys

def is_debug() -> bool:
    """Return True when LAST30DAYS_DEBUG is set in os.environ.

    Re-read on every call so a runtime bridge from .env -> os.environ takes
    effect. last30days.py main() bridges config['LAST30DAYS_DEBUG'] ->
    os.environ after env.get_config() loads .env; a module-level constant
    would freeze before that bridge runs.

    Use this from any module that needs debug gating (log, http, xai_x, ...).
    """
    return os.environ.get("LAST30DAYS_DEBUG", "").lower() in ("1", "true", "yes")


def __getattr__(name: str):
    # Backwards-compat: anything still importing `from log import DEBUG`
    # gets a fresh value rather than a stale module-load snapshot.
    if name == "DEBUG":
        return is_debug()
    raise AttributeError(f"module 'log' has no attribute {name!r}")


def debug(msg: str) -> None:
    """Log debug message to stderr (only when LAST30DAYS_DEBUG is set)."""
    if is_debug():
        sys.stderr.write(f"[DEBUG] {msg}\n")
        sys.stderr.flush()


def source_log(prefix: str, msg: str, *, tty_only: bool = True) -> None:
    """Log a source module message to stderr.

    Args:
        prefix: Source label (e.g. "Reddit", "Bird").
        msg: Message text.
        tty_only: If True, only log when stderr is a TTY (avoids cluttering
                  non-interactive output like Claude Code).
    """
    if tty_only and not sys.stderr.isatty():
        return
    sys.stderr.write(f"[{prefix}] {msg}\n")
    sys.stderr.flush()
