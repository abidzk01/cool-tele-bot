"""Downloader registry: detect the platform from a URL and pick the right module."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from . import base, instagram, tiktok, youtube
from .base import (
    DownloadError,
    DownloadResult,
    JobDir,
    MediaItem,
    Progress,
    cleanup_old_files,
    cleanup_old_jobs,
    describe_progress,
    human_bytes,
    human_duration,
    purge_directory,
)

PLATFORM_LABELS = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
}

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "youtube": (
        re.compile(r"^(.+\.)?youtube\.com$"),
        re.compile(r"^youtu\.be$"),
        re.compile(r"^youtube-nocookie\.com$"),
    ),
    "tiktok": (
        re.compile(r"^(.+\.)?tiktok\.com$"),
    ),
    "instagram": (
        re.compile(r"^(.+\.)?instagram\.com$"),
        re.compile(r"^instagr\.am$"),
    ),
}

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    """Collect every URL found in a message body."""
    return [u.rstrip(").,;!\"'") for u in URL_RE.findall(text or "")]


def detect_platform(url: str) -> str | None:
    if not url:
        return None
    try:
        host = (urlparse(url).netloc or "").lower().split("@")[-1].split(":")[0]
    except ValueError:
        return None
    if not host:
        return None
    for platform, patterns in _PATTERNS.items():
        if any(p.match(host) for p in patterns):
            return platform
    return None


def find_supported_url(text: str) -> tuple[str, str] | None:
    """Return the first URL whose platform is supported."""
    for url in extract_urls(text):
        platform = detect_platform(url)
        if platform:
            return url, platform
    return None


async def download_media(settings, url: str, *, platform: str | None = None,
                         audio_only: bool = False,
                         progress: Progress | None = None) -> DownloadResult:
    platform = platform or detect_platform(url)
    if platform is None:
        raise DownloadError(
            "Link tidak dikenali. Platform yang didukung: YouTube, TikTok, Instagram."
        )
    if platform == "youtube":
        return await youtube.download(settings, url, audio_only=audio_only, progress=progress)
    if platform == "tiktok":
        return await tiktok.download(settings, url, progress=progress)
    if platform == "instagram":
        return await instagram.download(settings, url, progress=progress)
    raise DownloadError(f"Platform {platform} belum didukung.")


__all__ = [
    "DownloadError",
    "DownloadResult",
    "JobDir",
    "MediaItem",
    "Progress",
    "PLATFORM_LABELS",
    "cleanup_old_jobs",
    "cleanup_old_files",
    "purge_directory",
    "describe_progress",
    "detect_platform",
    "download_media",
    "extract_urls",
    "find_supported_url",
    "human_bytes",
    "human_duration",
]
