"""Minimal heartbeat for container healthchecks (Docker/Podman) and monitoring.

The bot writes a small file in the tmp directory every minute from the job queue.
The healthcheck inspects the age of that file, so what gets tested is the running
event loop and job queue, not merely a process that is still alive.
"""
from __future__ import annotations

import time
from pathlib import Path

HEARTBEAT_NAME = "heartbeat"
DEFAULT_MAX_AGE = 300.0  # seconds; 5x the heartbeat interval (60s)


def heartbeat_path(settings) -> Path:
    return Path(settings.temp_dir) / HEARTBEAT_NAME


def touch(settings) -> Path:
    """Refresh the heartbeat marker (called every minute by the job queue)."""
    path = heartbeat_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(time.time())}\n", encoding="utf-8")
    return path


def age(settings) -> float | None:
    """Heartbeat age in seconds, or None when it has never been written."""
    try:
        return max(0.0, time.time() - heartbeat_path(settings).stat().st_mtime)
    except OSError:
        return None


def is_healthy(settings, max_age: float = DEFAULT_MAX_AGE) -> bool:
    value = age(settings)
    return value is not None and value <= max_age


def describe(settings, max_age: float = DEFAULT_MAX_AGE) -> str:
    value = age(settings)
    if value is None:
        return f"UNHEALTHY: no heartbeat yet at {heartbeat_path(settings)}"
    state = "OK" if value <= max_age else "UNHEALTHY"
    return f"{state}: heartbeat {value:.0f}s ago (limit {max_age:.0f}s)"