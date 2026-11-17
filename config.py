"""Konfigurasi terpusat, dibaca dari environment / file .env."""
from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env_str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_str(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _detect_ffmpeg() -> str | None:
    explicit = _env_str("FFMPEG_LOCATION")
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir():
            p = p / "ffmpeg"
        if p.is_file():
            return str(p)
    found = shutil.which("ffmpeg")
    if found:
        return found
    patterns = [
        "/home/*/.hermes/hermes-agent/tools/ffmpeg-*/bin/ffmpeg",
        "/root/.hermes/hermes-agent/tools/ffmpeg-*/bin/ffmpeg",
        "/opt/*/bin/ffmpeg",
    ]
    hits: list[str] = []
    for pattern in patterns:
        hits.extend(glob.glob(pattern))
    hits = sorted({h for h in hits if os.access(h, os.X_OK)})
    return hits[0] if hits else None


def _cookie_path(name: str) -> str | None:
    value = _env_str(name)
    if not value:
        return None
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = BASE_DIR / p
    return str(p) if p.is_file() else None


@dataclass(frozen=True)
class Settings:
    bot_token: str
    local_api_url: str | None
    allowed_user_ids: frozenset[int]
    admin_user_ids: frozenset[int]
    max_upload_bytes: int
    max_video_height: int
    max_duration_seconds: int
    ffmpeg: str | None
    max_concurrent: int
    temp_ttl_minutes: int
    tiktok_use_tikwm: bool
    tiktok_api_url: str
    cookies: dict = field(default_factory=dict)
    cookies_from_browser: str | None = None
    proxy_url: str | None = None
    auto_compress: bool = True
    transcode_h264: bool = True
    download_dir: Path = BASE_DIR / "downloads"
    temp_dir: Path = BASE_DIR / "tmp"

    @property
    def max_upload_mb(self) -> float:
        return self.max_upload_bytes / (1024 * 1024)

    def is_allowed(self, user_id: int | None) -> bool:
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids

    def is_admin(self, user_id: int | None) -> bool:
        """Admin boleh membersihkan pekerjaan & file milik semua pengguna."""
        if user_id is None:
            return False
        if self.admin_user_ids:
            return user_id in self.admin_user_ids
        # bila ADMIN_USER_IDS kosong, whitelist dianggap admin
        return bool(self.allowed_user_ids) and user_id in self.allowed_user_ids

    def cookies_for(self, platform_key: str) -> str | None:
        return self.cookies.get(platform_key)


def load_settings() -> Settings:
    token = _env_str("BOT_TOKEN", "") or ""
    ids_raw = _env_str("ALLOWED_USER_IDS", "") or ""
    allowed = frozenset(
        int(chunk) for chunk in (p.strip() for p in ids_raw.replace(";", ",").split(",")) if chunk.isdigit()
    )
    admin_raw = _env_str("ADMIN_USER_IDS", "") or ""
    admins = frozenset(
        int(chunk) for chunk in (p.strip() for p in admin_raw.replace(";", ",").split(",")) if chunk.isdigit()
    )
    max_mb = max(1, _env_int("MAX_UPLOAD_MB", 49))
    data_root = Path(_env_str("DATA_DIR", str(BASE_DIR))).expanduser()
    settings = Settings(
        bot_token=token,
        local_api_url=_env_str("LOCAL_API_URL"),
        allowed_user_ids=allowed,
        admin_user_ids=admins,
        max_upload_bytes=max_mb * 1024 * 1024,
        max_video_height=max(144, _env_int("MAX_VIDEO_HEIGHT", 1080)),
        max_duration_seconds=max(0, _env_int("MAX_DURATION_SECONDS", 3600)),
        ffmpeg=_detect_ffmpeg(),
        max_concurrent=max(1, _env_int("MAX_CONCURRENT_DOWNLOADS", 2)),
        temp_ttl_minutes=max(0, _env_int("TEMP_TTL_MINUTES", 60)),
        tiktok_use_tikwm=_env_bool("TIKTOK_USE_TIKWM", True),
        tiktok_api_url=_env_str("TIKTOK_API_URL", "https://www.tikwm.com/api/") or "https://www.tikwm.com/api/",
        cookies={
            "youtube": _cookie_path("COOKIES_YOUTUBE"),
            "instagram": _cookie_path("COOKIES_INSTAGRAM"),
            "tiktok": _cookie_path("COOKIES_TIKTOK"),
        },
        cookies_from_browser=_env_str("COOKIES_FROM_BROWSER"),
        proxy_url=_env_str("PROXY_URL"),
        auto_compress=_env_bool("AUTO_COMPRESS", True),
        transcode_h264=_env_bool("TRANSCODE_TO_H264", True),
        download_dir=data_root / "downloads",
        temp_dir=data_root / "tmp",
    )
    return settings
