"""Instagram downloader (reels, photo/video posts, carousels) via yt-dlp."""
from __future__ import annotations

import logging
from pathlib import Path

import yt_dlp

from . import base
from .base import DownloadError, DownloadResult, JobDir, MediaItem, Progress

log = logging.getLogger("appbot.downloader.instagram")

PLATFORM = "instagram"
MAX_CAROUSEL_ITEMS = 10


async def download(settings, url: str, *, progress: Progress | None = None) -> DownloadResult:
    with JobDir(settings.temp_dir, "ig") as job:
        extra = {
            "noplaylist": False,
            "playlistend": MAX_CAROUSEL_ITEMS,
            "http_headers": {
                "User-Agent": base.USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        opts = base.base_opts(settings, PLATFORM, job.path, progress, extra)
        try:
            info = await base.adownload(url, opts)
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadError(_friendly(exc, settings)) from exc
        except Exception as exc:  # noqa: BLE001
            raise DownloadError(base.map_ytdlp_error(exc, "Instagram")) from exc

        if not info:
            raise DownloadError("Tidak ada data yang bisa diambil dari link Instagram ini.")

        items = base.items_from_info(info, "video")
        if not items:
            raise DownloadError("Tidak ada media yang berhasil diunduh dari postingan ini.")

        items = await base.normalize_videos(items, settings)

        dest_dir = settings.download_dir / "instagram"
        dest_dir.mkdir(parents=True, exist_ok=True)
        final: list[MediaItem] = []
        for item in items:
            target = _unique(dest_dir / item.path.name)
            try:
                item.path.replace(target)
            except OSError:
                target = item.path
            final.append(MediaItem(path=target, kind=item.kind, title=item.title,
                                   duration=item.duration, width=item.width, height=item.height))

        entry = info
        if info.get("entries"):
            entries = [e for e in info["entries"] if e]
            if entries:
                entry = entries[0]

        notes: list[str] = []
        if len(final) > 1:
            notes.append(f"Postingan berisi {len(final)} media (carousel).")
        if not settings.cookies_for("instagram") and not settings.cookies_from_browser:
            notes.append(
                "Tip: untuk konten privat/beranda, set COOKIES_INSTAGRAM di .env."
            )

        return DownloadResult(
            platform=PLATFORM,
            source_url=info.get("webpage_url") or url,
            items=final,
            title=entry.get("title") or entry.get("description") or "Instagram",
            uploader=entry.get("uploader") or entry.get("channel") or "",
            duration=int(entry["duration"]) if entry.get("duration") else None,
            notes=notes,
        )


def _friendly(exc: Exception, settings) -> str:
    text = str(exc).lower()
    if "login" in text or "empty media response" in text or "rate-limit" in text:
        return (
            "Instagram menolak permintaan tanpa login (konten privat atau pembatasan rate).\n"
            "Solusi: ekspor cookies Instagram ke <code>cookies/instagram.txt</code> lalu set "
            "<code>COOKIES_INSTAGRAM=cookies/instagram.txt</code> di .env, "
            "atau pakai <code>COOKIES_FROM_BROWSER=chrome</code>."
        )
    return base.map_ytdlp_error(exc, "Instagram")


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1
