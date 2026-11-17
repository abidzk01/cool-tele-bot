"""Shared utilities for all downloaders (yt-dlp, ffmpeg, progress, cleanup)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import yt_dlp

log = logging.getLogger("appbot.downloader")

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts"}
AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".wav", ".aac", ".flac"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class DownloadError(RuntimeError):
    """An error that is safe to show directly to the user."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class MediaItem:
    path: Path
    kind: str = "video"  # video | audio | photo
    title: str = ""
    duration: int | None = None
    width: int | None = None
    height: int | None = None

    @property
    def filesize(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


@dataclass
class DownloadResult:
    platform: str
    source_url: str
    items: list[MediaItem] = field(default_factory=list)
    title: str = ""
    uploader: str = ""
    duration: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(i.filesize for i in self.items)


@dataclass
class Progress:
    """Progress state filled in by the yt-dlp hook / HTTP downloads."""

    status: str = "menyiapkan"
    percent: float = 0.0
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0
    eta: int | None = None
    filename: str = ""
    index: int = 1
    count: int = 1
    started_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def request_cancel(self) -> None:
        """Ask the download to stop; the yt-dlp hook then raises DownloadCancelled."""
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def hook(self, d: dict) -> None:
        # The hook runs on a yt-dlp thread: raising here stops the download
        # (asyncio.cancel alone cannot stop a thread that is already working).
        if self.cancel_event.is_set():
            raise yt_dlp.utils.DownloadCancelled("unduhan dibatalkan")
        status = d.get("status")
        if status == "downloading":
            self.status = "mengunduh"
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            self.total = int(total or 0)
            self.downloaded = int(d.get("downloaded_bytes") or 0)
            self.speed = float(d.get("speed") or 0.0)
            eta = d.get("eta")
            self.eta = int(eta) if eta else None
            self.percent = (self.downloaded / self.total * 100) if self.total else 0.0
            name = d.get("filename") or ""
            if name:
                self.filename = Path(name).name
            info = d.get("info_dict") or {}
            if info.get("playlist_index"):
                self.index = int(info["playlist_index"])
                self.count = int(info.get("n_entries") or self.count)
        elif status == "finished":
            self.status = "menggabungkan"
            self.percent = 100.0

    def set(self, *, downloaded: int | None = None, total: int | None = None,
            status: str | None = None, filename: str | None = None) -> None:
        if downloaded is not None:
            self.downloaded = downloaded
        if total is not None:
            self.total = total
        if status is not None:
            self.status = status
        if filename is not None:
            self.filename = filename
        elapsed = max(0.001, time.time() - self.started_at)
        if self.downloaded:
            self.speed = self.downloaded / elapsed
        self.percent = (self.downloaded / self.total * 100) if self.total else 0.0


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def human_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} PB"


def human_duration(seconds: float | int | None) -> str:
    if not seconds:
        return "0:00"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def progress_bar(percent: float, width: int = 10) -> str:
    filled = int(round(max(0.0, min(100.0, percent)) / 100 * width))
    return "█" * filled + "░" * (width - filled)


def describe_progress(p: Progress) -> str:
    parts = [f"<b>{p.status.capitalize()}…</b>"]
    if p.total:
        parts.append(
            f"{progress_bar(p.percent)} {p.percent:.1f}%\n"
            f"{human_bytes(p.downloaded)} / {human_bytes(p.total)}"
        )
    elif p.downloaded:
        parts.append(human_bytes(p.downloaded))
    if p.speed:
        parts.append(f"⚡ {human_bytes(p.speed)}/s")
    if p.eta:
        parts.append(f"⏳ sisa ~{human_duration(p.eta)}")
    if p.count > 1:
        parts.append(f"📚 item {p.index}/{p.count}")
    if p.filename:
        name = p.filename if len(p.filename) <= 48 else p.filename[:45] + "..."
        parts.append(f"<code>{name}</code>")
    return "\n".join(parts)


def kind_for(path: Path, default: str = "video") -> str:
    ext = path.suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    return default


# --------------------------------------------------------------------------- #
# Working directory
# --------------------------------------------------------------------------- #
class JobDir:
    """Temporary per-request directory, removed once the request is done."""

    def __init__(self, root: Path, prefix: str = "job") -> None:
        self.root = root
        self.path = root / f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "JobDir":
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def cleanup_old_jobs(root: Path, ttl_minutes: int) -> int:
    """Remove working directory entries older than ttl_minutes."""
    if ttl_minutes <= 0 or not root.is_dir():
        return 0
    cutoff = time.time() - ttl_minutes * 60
    removed = 0
    for entry in root.iterdir():
        try:
            if entry.stat().st_mtime < cutoff:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def cleanup_old_files(root: Path, ttl_minutes: int) -> int:
    """Recursively remove stale files (directories are kept) to prevent build-up."""
    if ttl_minutes <= 0 or not root.is_dir():
        return 0
    cutoff = time.time() - ttl_minutes * 60
    removed = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return removed


def purge_directory(root: Path) -> tuple[int, int]:
    """Remove the whole directory contents (files and subdirectories). Return (file count, bytes)."""
    if not root.is_dir():
        return 0, 0
    files = 0
    total = 0
    for entry in list(root.iterdir()):
        try:
            if entry.is_dir() and not entry.is_symlink():
                for path in entry.rglob("*"):
                    if path.is_file():
                        with contextlib.suppress(OSError):
                            total += path.stat().st_size
                        files += 1
                shutil.rmtree(entry, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):
                    total += entry.stat().st_size
                entry.unlink(missing_ok=True)
                files += 1
        except OSError:
            continue
    return files, total


# --------------------------------------------------------------------------- #
# yt-dlp wrapper
# --------------------------------------------------------------------------- #
def base_opts(settings, platform_key: str, workdir: Path, progress: Progress | None = None,
              extra: dict | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "outtmpl": {"default": str(workdir / "%(title).80B [%(id)s].%(ext)s")},
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "consoletitle": False,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "nocheckcertificate": True,
        "overwrites": True,
        "trim_file_name": 90,
        "ignoreerrors": False,
        "windowsfilenames": False,
        "cachedir": str(settings.temp_dir / "ytdlp-cache"),
        "http_headers": {"User-Agent": USER_AGENT},
    }
    if settings.ffmpeg:
        opts["ffmpeg_location"] = settings.ffmpeg
    if settings.proxy_url:
        opts["proxy"] = settings.proxy_url
    cookie = settings.cookies_for(platform_key)
    if cookie:
        opts["cookiefile"] = cookie
    elif settings.cookies_from_browser:
        opts["cookiesfrombrowser"] = (settings.cookies_from_browser,)
    if progress is not None:
        opts["progress_hooks"] = [progress.hook]
    if extra:
        opts.update(extra)
    return opts


def _run_sync(url: str, opts: dict) -> dict | None:
    skip_download = bool(opts.pop("_skip_download", False))
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=not skip_download)


async def aextract(url: str, opts: dict) -> dict | None:
    """Fetch metadata without downloading."""
    options = dict(opts)
    options["_skip_download"] = True
    return await asyncio.to_thread(_run_sync, url, options)


async def adownload(url: str, opts: dict) -> dict | None:
    """Download the media."""
    return await asyncio.to_thread(_run_sync, url, dict(opts))


def map_ytdlp_error(exc: Exception, platform: str) -> str:
    """Turn a yt-dlp error into a message the user can read."""
    text = str(exc)
    low = text.lower()
    if isinstance(exc, yt_dlp.utils.DownloadCancelled) or "dibatalkan" in low or "cancelled" in low:
        return "Unduhan dibatalkan."
    if "sign in to confirm" in low or "not a bot" in low or "confirm you're not a bot" in low:
        return (
            "YouTube meminta verifikasi anti-bot dari IP server ini.\n"
            "Solusi: ekspor cookies browser ke <code>cookies/youtube.txt</code> "
            "lalu set <code>COOKIES_YOUTUBE=cookies/youtube.txt</code> di file .env."
        )
    if "login required" in low or "requires authentication" in low or "rate-limit" in low and "instagram" in low:
        return (
            "Konten ini butuh login. Ekspor cookies Instagram ke "
            "<code>cookies/instagram.txt</code> lalu set "
            "<code>COOKIES_INSTAGRAM=cookies/instagram.txt</code> di .env."
        )
    if "private" in low or "not available" in low or "removed" in low or "unavailable" in low:
        return "Konten privat, sudah dihapus, atau tidak tersedia untuk publik."
    if "unsupported url" in low:
        return f"Link {platform} tidak dikenali. Pastikan URL-nya benar dan mengarah ke satu postingan."
    if "http error 429" in low or "too many requests" in low:
        return "Server sumber membatasi permintaan (HTTP 429). Coba lagi beberapa menit lagi."
    if "no video formats found" in low:
        return "Tidak ada format video yang bisa diunduh dari link ini."
    if "timed out" in low or "timeout" in low:
        return "Koneksi ke server sumber habis waktu. Coba lagi."
    if "unable to download webpage" in low or "connection" in low or "network" in low:
        return "Gagal menghubungi server sumber. Cek koneksi internet server."
    return f"Gagal memproses link {platform}: {text[:300]}"


def items_from_info(info: dict, default_kind: str = "video") -> list[MediaItem]:
    """Collect the downloaded files from the yt-dlp info dict (playlists included)."""
    entries = info.get("entries")
    if entries:
        items: list[MediaItem] = []
        for entry in entries:
            if entry:
                items.extend(items_from_info(entry, default_kind))
        return items

    candidates: list[Path] = []
    for req in info.get("requested_downloads") or []:
        fp = req.get("filepath")
        if fp:
            candidates.append(Path(fp))
    if not candidates:
        for key in ("filepath", "_filename"):
            if info.get(key):
                candidates.append(Path(info[key]))

    items = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        items.append(
            MediaItem(
                path=path,
                kind=kind_for(path, default_kind),
                title=info.get("title") or path.stem,
                duration=int(info["duration"]) if info.get("duration") else None,
                width=info.get("width"),
                height=info.get("height"),
            )
        )
    return items


# --------------------------------------------------------------------------- #
# ffmpeg helpers
# --------------------------------------------------------------------------- #
def ffprobe_path(ffmpeg: str | None) -> str | None:
    """Look for ffprobe next to ffmpeg, or fall back to PATH."""
    if ffmpeg:
        candidate = Path(ffmpeg).with_name("ffprobe")
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffprobe")


def probe_video_codec(path: Path, ffmpeg: str | None) -> str | None:
    probe = ffprobe_path(ffmpeg)
    if not probe:
        return None
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=90,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
    return lines[0] if lines else None


def ensure_h264(path: Path, ffmpeg: str | None, crf: int = 23) -> Path:
    """Transcode non-H.264 video (HEVC/VP9/AV1) so Telegram can play it inline."""
    if not ffmpeg or path.suffix.lower() not in VIDEO_EXTS:
        return path
    codec = probe_video_codec(path, ffmpeg)
    if codec in (None, "", "h264"):
        return path
    target = path.with_name(f"{path.stem}-h264.mp4")
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-i", str(path),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(target),
    ]
    log.info("Transcode %s (%s) -> H.264", path.name, codec)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Could not run the H.264 transcode: %s", exc)
        return path
    if proc.returncode != 0 or not target.is_file():
        log.warning("H.264 transcode failed (%s): %s", path.name, (proc.stderr or "").strip()[:200])
        return path
    path.unlink(missing_ok=True)
    return target


async def aensure_h264(path: Path, ffmpeg: str | None) -> Path:
    return await asyncio.to_thread(ensure_h264, path, ffmpeg)


async def normalize_videos(items: list[MediaItem], settings) -> list[MediaItem]:
    """Make sure every video uses the H.264 codec so Telegram can play it inline."""
    if not getattr(settings, "transcode_h264", False):
        return items
    normalized: list[MediaItem] = []
    for item in items:
        if item.kind != "video" or not item.path.is_file():
            normalized.append(item)
            continue
        new_path = await aensure_h264(item.path, settings.ffmpeg)
        normalized.append(replace(item, path=new_path) if new_path != item.path else item)
    return normalized


def compress_to_fit(src: Path, dst: Path, limit_bytes: int, duration: float | None,
                    ffmpeg: str, max_height: int = 720) -> Path:
    """Re-encode a video so its size lands close to the upload limit."""
    duration = duration or 0
    if duration <= 0:
        raise DownloadError("Durasi tidak diketahui, kompresi otomatis dilewati.")
    audio_kbps = 64
    total_kbps = (limit_bytes * 8 / duration / 1000) * 0.90
    video_kbps = max(120, total_kbps - audio_kbps)
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
        "-vf", f"scale='min({max_height},ih)':-2:flags=lanczos",
        "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-b:v", f"{int(video_kbps)}k",
        "-maxrate", f"{int(video_kbps * 1.5)}k",
        "-bufsize", f"{int(video_kbps * 3)}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ac", "2",
        "-movflags", "+faststart", str(dst),
    ]
    log.info("Compressing video: %s -> %s (%d kbps)", src.name, dst.name, int(video_kbps))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.is_file():
        raise DownloadError(f"Kompresi ffmpeg gagal: {(proc.stderr or '').strip()[:200]}")
    return dst


# --------------------------------------------------------------------------- #
# Direct HTTP download (used by the TikTok API)
# --------------------------------------------------------------------------- #
async def http_download(url: str, dest: Path, progress: Progress | None = None,
                        headers: dict | None = None, referer: str | None = None) -> Path:
    merged = {"User-Agent": USER_AGENT}
    if referer:
        merged["Referer"] = referer
    merged.update(headers or {})
    dest.parent.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(30.0, read=120.0)
    async with httpx.AsyncClient(follow_redirects=True, headers=merged, timeout=timeout) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            with dest.open("wb") as fh:
                async for chunk in resp.aiter_bytes(256 * 1024):
                    fh.write(chunk)
                    got += len(chunk)
                    if progress is not None:
                        progress.set(downloaded=got, total=total or progress.total)
    if not dest.is_file() or dest.stat().st_size == 0:
        raise DownloadError("Unduhan gagal: file kosong.")
    return dest
