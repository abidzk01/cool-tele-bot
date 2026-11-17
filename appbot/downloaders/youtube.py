"""YouTube downloader (video, audio, shorts, capped playlists)."""
from __future__ import annotations

import logging
from pathlib import Path

import yt_dlp

from . import base
from .base import DownloadError, DownloadResult, JobDir, MediaItem, Progress, human_bytes

log = logging.getLogger("appbot.downloader.youtube")

PLATFORM = "youtube"
MAX_PLAYLIST_ITEMS = 5


def _heights(max_height: int) -> list[int]:
    """Resolution ladder that never exceeds MAX_VIDEO_HEIGHT."""
    seen: list[int] = []
    for h in (max_height, 1080, 720, 480, 360, 144):
        if h <= max_height and h not in seen:
            seen.append(h)
    return seen


def guard_metadata(info: dict | None, settings) -> None:
    """Reject live streams and over-long videos before anything is downloaded."""
    if not info:
        return
    status = info.get("live_status")
    if info.get("is_live") or status in {"is_live", "is_upcoming"}:
        raise DownloadError(
            "Siaran langsung (live) tidak bisa diunduh. Tunggu sampai siaran selesai "
            "dan tersedia sebagai video biasa."
        )
    if status == "post_live":
        raise DownloadError(
            "Siaran langsung yang baru selesai belum diproses YouTube. Coba lagi beberapa menit lagi."
        )
    duration = info.get("duration")
    if settings.max_duration_seconds and duration and duration > settings.max_duration_seconds:
        raise DownloadError(
            f"Durasi video {int(duration) // 60} menit melebihi batas "
            f"{settings.max_duration_seconds // 60} menit. Ubah MAX_DURATION_SECONDS di .env bila perlu."
        )


def _selector(limit: int, height: int) -> str:
    return (
        f"bv*[filesize<{limit}][height<={height}]+ba[filesize<{limit}]/"
        f"b[filesize<{limit}][height<={height}]/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"b[height<={height}]/b"
    )


async def download(settings, url: str, *, audio_only: bool = False,
                   progress: Progress | None = None) -> DownloadResult:
    if settings.ffmpeg is None and not audio_only:
        log.warning("ffmpeg not found; merging video+audio may fail.")

    with JobDir(settings.temp_dir, "yt") as job:
        result = await _download_into(settings, url, job.path, audio_only, progress)
        # make sure the video codec is compatible with the Telegram player
        result.items = await base.normalize_videos(result.items, settings)
        # move the results into the permanent downloads/ before the job dir is cleaned up
        final_dir = settings.download_dir / "youtube"
        final_dir.mkdir(parents=True, exist_ok=True)
        moved: list[MediaItem] = []
        for item in result.items:
            target = _unique_path(final_dir / item.path.name)
            try:
                item.path.replace(target)
            except OSError:
                target = item.path
            moved.append(
                MediaItem(path=target, kind=item.kind, title=item.title,
                          duration=item.duration, width=item.width, height=item.height)
            )
        result.items = moved
        return result


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 1
    while True:
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


async def _download_into(settings, url: str, workdir: Path, audio_only: bool,
                         progress: Progress | None) -> DownloadResult:
    is_playlist = "list=" in url or "/playlist" in url
    extra: dict = {"noplaylist": not is_playlist}
    if is_playlist:
        extra["playlistend"] = MAX_PLAYLIST_ITEMS
    else:
        # check the metadata first: reject live streams / over-long videos before downloading
        if progress is not None:
            progress.status = "membaca metadata"
        probe_opts = base.base_opts(settings, PLATFORM, workdir, None, {"skip_download": True})
        try:
            probe = await base.aextract(url, probe_opts)
        except yt_dlp.utils.DownloadError as exc:
            raise DownloadError(base.map_ytdlp_error(exc, "YouTube")) from exc
        guard_metadata(probe, settings)

    if audio_only:
        extra["format"] = "bestaudio[filesize<{0}]/bestaudio/best".format(settings.max_upload_bytes)
        extra["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
        if progress is not None:
            progress.status = "mengunduh audio"
        info = await _run(settings, url, workdir, extra, progress)
        items = base.items_from_info(info, "audio")
        result = _wrap(info, url, items)
        _guard_list(result, settings)
        return result

    attempts = _heights(settings.max_video_height)[:3]
    last_error: str | None = None
    for idx, height in enumerate(attempts):
        extra_i = dict(extra)
        extra_i.update(
            {
                "format": _selector(settings.max_upload_bytes, height),
                "merge_output_format": "mp4",
                "format_sort": [f"res:{height}", "ext:mp4:m4a", "codec:h264"],
                "postprocessors": [
                    {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
                ],
            }
        )
        if progress is not None:
            progress.status = f"mengunduh (maks {height}p)"
        try:
            info = await _run(settings, url, workdir, extra_i, progress)
        except DownloadError as exc:
            last_error = str(exc)
            _clear(workdir)
            continue

        items = base.items_from_info(info, "video")
        if not items:
            last_error = "Tidak ada file hasil unduhan."
            _clear(workdir)
            continue

        biggest = max(item.filesize for item in items)
        if biggest <= settings.max_upload_bytes:
            result = _wrap(info, url, items)
            _guard_list(result, settings)
            return result

        # Too large -> try compression, and drop to a lower resolution if that fails.
        if settings.auto_compress and settings.ffmpeg:
            if progress is not None:
                progress.status = "mengompresi agar sesuai batas Telegram"
            compressed = await _try_compress(settings, items, info, workdir)
            if compressed is not None and max(i.filesize for i in compressed) <= settings.max_upload_bytes:
                result = _wrap(info, url, compressed)
                result.notes.append(
                    f"Video dikompresi otomatis menjadi "
                    f"{human_bytes(max(i.filesize for i in compressed))} agar bisa dikirim."
                )
                _guard_list(result, settings)
                return result
        _clear(workdir)
        last_error = (
            f"Hasil unduhan {human_bytes(biggest)} melebihi batas upload "
            f"{human_bytes(settings.max_upload_bytes)}."
        )

    raise DownloadError(
        (last_error or "Unduhan gagal.")
        + "\nCoba turunkan MAX_VIDEO_HEIGHT di .env, atau kirim dengan opsi audio saja "
        "(/yt audio &lt;link&gt;)."
    )


async def _run(settings, url: str, workdir: Path, extra: dict,
               progress: Progress | None) -> dict:
    opts = base.base_opts(settings, PLATFORM, workdir, progress, extra)
    try:
        info = await base.adownload(url, opts)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(base.map_ytdlp_error(exc, "YouTube")) from exc
    except Exception as exc:  # noqa: BLE001
        raise DownloadError(base.map_ytdlp_error(exc, "YouTube")) from exc
    if not info:
        raise DownloadError("Tidak ada data yang bisa diambil dari link YouTube ini.")
    if info.get("_type") == "playlist" and not info.get("entries"):
        raise DownloadError("Playlist kosong atau tidak bisa diakses.")
    return info


def _wrap(info: dict, url: str, items: list[MediaItem]) -> DownloadResult:
    entry = info
    if info.get("entries"):
        entries = [e for e in info["entries"] if e]
        entry = entries[0] if entries else info
    return DownloadResult(
        platform=PLATFORM,
        source_url=info.get("webpage_url") or url,
        items=items,
        title=entry.get("title") or "YouTube",
        uploader=entry.get("uploader") or entry.get("channel") or "",
        duration=int(entry["duration"]) if entry.get("duration") else None,
    )


def _guard_list(result: DownloadResult, settings) -> None:
    if not result.items:
        raise DownloadError("Tidak ada file yang berhasil diunduh.")
    if settings.max_duration_seconds and result.duration and result.duration > settings.max_duration_seconds:
        for item in result.items:
            item.path.unlink(missing_ok=True)
        raise DownloadError(
            f"Durasi video {result.duration // 60} menit melebihi batas "
            f"{settings.max_duration_seconds // 60} menit."
        )


def _clear(workdir: Path) -> None:
    for path in workdir.iterdir():
        try:
            path.unlink()
        except OSError:
            pass


async def _try_compress(settings, items: list[MediaItem], info: dict,
                        workdir: Path) -> list[MediaItem] | None:
    if not settings.ffmpeg:
        return None
    duration = info.get("duration") or max((i.duration or 0) for i in items)
    out: list[MediaItem] = []
    for i, item in enumerate(items):
        if item.kind not in {"video"} or item.filesize <= settings.max_upload_bytes:
            out.append(item)
            continue
        target = workdir / f"compressed-{i}.mp4"
        try:
            base.compress_to_fit(
                item.path, target, settings.max_upload_bytes, duration,
                settings.ffmpeg, max_height=min(720, settings.max_video_height),
            )
        except DownloadError as exc:
            log.warning("Compression skipped: %s", exc)
            out.append(item)
            continue
        item.path.unlink(missing_ok=True)
        out.append(MediaItem(path=target, kind="video", title=item.title,
                             duration=item.duration, width=item.width, height=item.height))
    return out
