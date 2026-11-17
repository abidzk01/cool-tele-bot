"""TikTok downloader without watermark (tikwm API + yt-dlp fallback)."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx
import yt_dlp

from . import base
from .base import DownloadError, DownloadResult, JobDir, MediaItem, Progress

log = logging.getLogger("appbot.downloader.tiktok")

PLATFORM = "tiktok"
TIKWM_REFERER = "https://www.tikwm.com/"


async def download(settings, url: str, *, progress: Progress | None = None) -> DownloadResult:
    errors: list[str] = []

    if settings.tiktok_use_tikwm:
        try:
            return await _download_tikwm(settings, url, progress)
        except DownloadError as exc:
            errors.append(f"tikwm: {exc}")
            log.info("Falling back to yt-dlp for TikTok (%s)", exc)

    try:
        return await _download_ytdlp(settings, url, progress)
    except DownloadError as exc:
        errors.append(f"yt-dlp: {exc}")
        raise DownloadError(
            "Gagal mengunduh dari TikTok.\n" + "\n".join(errors)
        ) from exc


# --------------------------------------------------------------------------- #
# Path 1: tikwm API (consistently watermark-free, supports photo slideshows)
# --------------------------------------------------------------------------- #
async def _tikwm_fetch(settings, url: str) -> dict:
    payload = {"url": url, "hd": "1", "count": "12", "cursor": "0"}
    headers = {"User-Agent": base.USER_AGENT, "Referer": TIKWM_REFERER}
    timeout = httpx.Timeout(30.0, read=60.0)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = await client.post(settings.tiktok_api_url, data=payload)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict) or data.get("code") not in (0, "0"):
        raise DownloadError(str(data.get("msg") if isinstance(data, dict) else data)[:200])
    result = data.get("data")
    if not isinstance(result, dict):
        raise DownloadError("respons API tidak berisi data")
    return result


def _absolute(url: str | None, base_url: str) -> str | None:
    if not url:
        return None
    if url.startswith("http"):
        return url
    host = base_url.rstrip("/")
    if url.startswith("/"):
        return host + url
    return host + "/" + url


async def _download_tikwm(settings, url: str, progress: Progress | None) -> DownloadResult:
    data = await _tikwm_fetch(settings, url)
    api_base = settings.tiktok_api_url
    title = (data.get("title") or "TikTok").strip()[:200]
    author = ((data.get("author") or {}).get("unique_id") or "").strip()
    duration = int(data.get("duration") or 0) or None

    with JobDir(settings.temp_dir, "tt") as job:
        items: list[MediaItem] = []
        images = data.get("images") or []

        if images:
            # photo slideshow -> send as a set of photos
            total = len(images)
            for idx, img in enumerate(images, start=1):
                link = _absolute(img, api_base)
                if not link:
                    continue
                if progress is not None:
                    progress.status = f"mengunduh foto {idx}/{total}"
                dest = job.path / f"slide-{idx:02d}.jpg"
                await base.http_download(link, dest, progress=None, referer=TIKWM_REFERER)
                items.append(MediaItem(path=dest, kind="photo", title=title))
            if not items:
                raise DownloadError("slideshow foto tidak bisa diunduh")
        else:
            video_url = _absolute(data.get("hdplay") or data.get("play"), api_base)
            if not video_url:
                raise DownloadError("URL video tidak tersedia")
            if progress is not None:
                progress.status = "mengunduh video (tanpa watermark)"
            dest = job.path / "tiktok.mp4"
            await base.http_download(video_url, dest, progress=progress, referer=TIKWM_REFERER)
            wm = data.get("wmplay")
            note = ""
            if wm and data.get("hdplay") and data.get("hdplay") != wm:
                note = "versi HD tanpa watermark"
            items.append(
                MediaItem(path=dest, kind="video", title=title, duration=duration)
            )
            if note and progress is not None:
                progress.status = note

        # also download the original music/audio when available
        notes: list[str] = []
        music_url = _absolute(data.get("music"), api_base)
        if music_url:
            try:
                audio_path = job.path / "tiktok-audio.mp3"
                await base.http_download(music_url, audio_path, referer=TIKWM_REFERER)
                items.append(MediaItem(path=audio_path, kind="audio", title=f"{title} (audio)"))
                notes.append("Audio asli ikut dikirim.")
            except (httpx.HTTPError, DownloadError) as exc:  # audio is optional
                log.info("Skipping TikTok audio: %s", exc)

        # make sure the video codec is compatible with Telegram players
        if progress is not None:
            progress.status = "menyiapkan video"
        items = await base.normalize_videos(items, settings)

        # move to the permanent folder before the job dir is deleted
        dest_dir = settings.download_dir / "tiktok"
        dest_dir.mkdir(parents=True, exist_ok=True)
        final: list[MediaItem] = []
        for item in items:
            target = _unique(dest_dir / item.path.name)
            try:
                item.path.replace(target)
            except OSError:
                target = item.path
            final.append(MediaItem(path=target, kind=item.kind, title=item.title, duration=item.duration))

        return DownloadResult(
            platform=PLATFORM,
            source_url=url,
            items=final,
            title=title,
            uploader=author,
            duration=duration,
            notes=notes + ["Diunduh tanpa watermark."],
        )


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


# --------------------------------------------------------------------------- #
# Path 2: yt-dlp (fallback; format chosen to stay watermark-free)
# --------------------------------------------------------------------------- #
def _is_watermarked(label: str) -> bool:
    """Return whether a format carries a watermark, honoring negation markers."""
    if any(tok in label for tok in
           ("no watermark", "no_watermark", "nowatermark", "nowm", "without watermark", "watermark-free")):
        return False
    return any(tok in label for tok in ("watermark", "wmplay", "download_addr", "watermarked"))


def _pick_format(info: dict) -> str:
    formats = info.get("formats") or []
    if not formats:
        return "best"

    def score(fmt: dict) -> tuple:
        label = (
            f"{fmt.get('format_id') or ''} {fmt.get('format_note') or ''} "
            f"{fmt.get('format') or ''} {fmt.get('url') or ''}"
        ).lower()
        has_audio = bool(fmt.get("acodec") and fmt.get("acodec") != "none")
        return (
            0 if _is_watermarked(label) else 1,
            1 if has_audio else 0,
            fmt.get("height") or 0,
            fmt.get("tbr") or 0,
        )

    best = max(formats, key=score)
    fmt_id = best.get("format_id")
    if not fmt_id:
        return "best"
    if best.get("acodec") in (None, "none"):
        return f"{fmt_id}+bestaudio/{fmt_id}/best"
    return f"{fmt_id}/best"


async def _download_ytdlp(settings, url: str, progress: Progress | None) -> DownloadResult:
    with JobDir(settings.temp_dir, "tt-yt") as job:
        probe_opts = base.base_opts(settings, PLATFORM, job.path, None, {"skip_download": True})
        try:
            probe = await base.aextract(url, probe_opts)
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadError(base.map_ytdlp_error(exc, "TikTok")) from exc
        if not probe:
            raise DownloadError("metadata TikTok tidak ditemukan")

        entry = probe
        if probe.get("entries"):
            entries = [e for e in probe["entries"] if e]
            if not entries:
                raise DownloadError("postingan TikTok tidak bisa diakses")
            entry = entries[0]

        chosen = _pick_format(entry)
        extra = {"format": chosen, "merge_output_format": "mp4"}
        if settings.ffmpeg:
            extra["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}]
        opts = base.base_opts(settings, PLATFORM, job.path, progress, extra)
        try:
            info = await base.adownload(url, opts)
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadError(base.map_ytdlp_error(exc, "TikTok")) from exc
        if not info:
            raise DownloadError("unduhan TikTok gagal")

        items = base.items_from_info(info, "video")
        if not items:
            raise DownloadError("tidak ada file hasil unduhan TikTok")

        items = await base.normalize_videos(items, settings)

        dest_dir = settings.download_dir / "tiktok"
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

        return DownloadResult(
            platform=PLATFORM,
            source_url=url,
            items=final,
            title=entry.get("title") or "TikTok",
            uploader=entry.get("uploader") or entry.get("creator") or "",
            duration=int(entry["duration"]) if entry.get("duration") else None,
            notes=["Format tanpa watermark diprioritaskan (fallback yt-dlp)."],
        )
