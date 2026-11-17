"""Heartbeat sederhana untuk healthcheck container (Docker/Podman) dan monitoring.

Bot menulis file kecil di folder tmp setiap menit dari job queue. Healthcheck
memeriksa umur file itu, sehingga yang diuji benar-benar event loop + job queue
yang berjalan, bukan sekadar proses yang masih hidup.
"""
from __future__ import annotations

import time
from pathlib import Path

HEARTBEAT_NAME = "heartbeat"
DEFAULT_MAX_AGE = 300.0  # detik; 5x interval heartbeat (60s)


def heartbeat_path(settings) -> Path:
    return Path(settings.temp_dir) / HEARTBEAT_NAME


def touch(settings) -> Path:
    """Perbarui penanda heartbeat (dipanggil tiap menit oleh job queue)."""
    path = heartbeat_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(time.time())}\n", encoding="utf-8")
    return path


def age(settings) -> float | None:
    """Umur heartbeat dalam detik, atau None bila belum pernah ditulis."""
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
        return f"UNHEALTHY: heartbeat belum ada di {heartbeat_path(settings)}"
    state = "OK" if value <= max_age else "UNHEALTHY"
    return f"{state}: heartbeat {value:.0f}s lalu (batas {max_age:.0f}s)"