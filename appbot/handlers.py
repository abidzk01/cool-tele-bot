"""Command and message handlers for the bot."""
from __future__ import annotations

import asyncio
import contextlib
import functools
import html
import logging
import time
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from . import health, jobs, system_info
from . import telegram_io
from .downloaders import (
    DownloadError,
    PLATFORM_LABELS,
    Progress,
    cleanup_old_files,
    cleanup_old_jobs,
    describe_progress,
    download_media,
    find_supported_url,
    human_bytes,
    purge_directory,
)
from .telegram_io import send_result

log = logging.getLogger("appbot.handlers")

HELP_TEXT = """<b>🤖 Bot Downloader + Info Sistem</b>

<b>Perintah</b>
• /yt <i>&lt;link|kata kunci&gt;</i> — unduh video YouTube (maks {h}p)
• /yt audio <i>&lt;link&gt;</i> — ambil audio saja (MP3)
• /tt <i>&lt;link&gt;</i> — unduh TikTok <b>tanpa watermark</b>
• /ig <i>&lt;link&gt;</i> — unduh Instagram (reel / post / carousel)
• /sysinfo — informasi CPU, RAM, disk, dan status bot
• /cleanup — batalkan unduhan berjalan &amp; hapus file sisa
• /cleanup all — (admin) bersihkan semua pengguna
• /id — lihat ID chat &amp; user ID kamu
• /help — tampilkan bantuan ini

<b>Singkat</b>
Kirim saja link YouTube, TikTok, atau Instagram langsung ke chat ini,
bot akan otomatis mengenali platformnya.

<b>Batas</b>
• Ukuran kirim maksimum {size}
• Durasi maksimum {dur}
"""


def restricted(func: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
    """Restrict access based on ALLOWED_USER_IDS."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        settings = context.application.bot_data.get("settings")
        user = update.effective_user
        if settings is not None and not settings.is_allowed(user.id if user else None):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "⛔ Maaf, bot ini dibatasi untuk pengguna tertentu."
                )
            log.info("Access denied for user %s", getattr(user, "id", None))
            return
        await func(update, context)

    return wrapper


class LiveStatus:
    """Refresh the status message periodically while work is in progress."""

    def __init__(self, message, progress: Progress, header: str, interval: float = 3.0) -> None:
        self.message = message
        self.progress = progress
        self.header = header
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._last = ""

    def render(self) -> str:
        return f"{self.header}\n\n{describe_progress(self.progress)}"

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            text = self.render()
            if text == self._last:
                continue
            self._last = text
            with contextlib.suppress(BadRequest, Exception):
                await self.message.edit_text(text, parse_mode=ParseMode.HTML)

    async def __aenter__(self) -> "LiveStatus":
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task


def _limits_text(settings) -> tuple[str, str]:
    size = f"{settings.max_upload_mb:.0f} MB"
    dur = "tanpa batas" if not settings.max_duration_seconds else f"{settings.max_duration_seconds // 60} menit"
    return size, dur


# --------------------------------------------------------------------------- #
# Basic commands
# --------------------------------------------------------------------------- #
@restricted
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    size, dur = _limits_text(settings)
    text = (
        "👋 <b>Selamat datang!</b>\n\n"
        "Saya bisa mengunduh media dan melaporkan kondisi server.\n\n"
        f"{HELP_TEXT.format(h=settings.max_video_height, size=size, dur=dur)}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@restricted
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    size, dur = _limits_text(settings)
    await update.effective_message.reply_text(
        HELP_TEXT.format(h=settings.max_video_height, size=size, dur=dur),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


@restricted
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    await update.effective_message.reply_text(
        "🆔 <b>Identitas</b>\n"
        f"• User ID : <code>{getattr(user, 'id', '-')}</code>\n"
        f"• Nama    : {html.escape(getattr(user, 'full_name', '') or '-')}\n"
        f"• Username: @{html.escape(user.username) if user and user.username else '-'}\n"
        f"• Chat ID : <code>{getattr(chat, 'id', '-')}</code>\n"
        f"• Tipe    : {getattr(chat, 'type', '-')}",
        parse_mode=ParseMode.HTML,
    )


@restricted
async def cmd_sysinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    started_at = context.application.bot_data.get("started_at")
    message = update.effective_message
    data = system_info.collect(settings.ffmpeg, started_at)
    if len(data.get("distro", "")) > 60:
        data["distro"] = data["distro"][:60]
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Perbarui", callback_data="sysinfo:refresh")]]
    )
    await message.reply_text(
        system_info.format_report(data), parse_mode=ParseMode.HTML,
        reply_markup=keyboard, disable_web_page_preview=True,
    )


# --------------------------------------------------------------------------- #
# Cleanup
# --------------------------------------------------------------------------- #
@restricted
async def cmd_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel running downloads, then remove leftover files.

    /cleanup        -> own jobs; full purge when no other job is running
    /cleanup all    -> (admin) cancel every job & purge the whole folder
    """
    settings = context.application.bot_data["settings"]
    registry: jobs.JobRegistry = context.application.bot_data["jobs"]
    message = update.effective_message
    user_id = getattr(update.effective_user, "id", None)
    args = [a.lower() for a in (context.args or [])]
    scope_all = any(a in {"all", "semua", "*"} for a in args)

    if scope_all and not settings.is_admin(user_id):
        await message.reply_text(
            "⛔ <code>/cleanup all</code> hanya untuk admin (<code>ADMIN_USER_IDS</code>).\n"
            "Pakai <code>/cleanup</code> untuk membersihkan unduhanmu sendiri.",
            parse_mode=ParseMode.HTML,
        )
        return

    started = time.time()
    targets = registry.all() if scope_all else registry.by_user(user_id)
    stopped = await registry.cancel_many(list(targets))

    others_running = len(registry) > 0
    full_purge = scope_all or not others_running
    if full_purge:
        tmp_files, tmp_bytes = purge_directory(settings.temp_dir)
        dl_files, dl_bytes = purge_directory(settings.download_dir)
        partial_note = ""
    else:
        # another user has a job running: do not delete files it may still be using
        tmp_files = cleanup_old_jobs(settings.temp_dir, ttl_minutes=1)
        dl_files = cleanup_old_files(settings.download_dir, ttl_minutes=1)
        tmp_bytes = dl_bytes = 0
        partial_note = (
            "\n<i>Ada unduhan pengguna lain yang masih berjalan, jadi hanya file "
            "berumur &gt;1 menit yang dihapus.</i>"
        )

    total_files = tmp_files + dl_files
    total_bytes = tmp_bytes + dl_bytes
    scope_label = "semua pengguna" if scope_all else "milikmu"

    # the purge also removes the heartbeat marker -> rewrite it so the healthcheck stays green
    with contextlib.suppress(OSError):
        health.touch(settings)

    if not stopped and not total_files:
        await message.reply_text(
            "🧹 <b>Tidak ada yang perlu dibersihkan</b>\n"
            f"• Job aktif: {len(registry)}\n"
            "• File sisa: 0",
            parse_mode=ParseMode.HTML,
        )
        return

    size_line = f" ({human_bytes(total_bytes)})" if full_purge else ""
    await message.reply_text(
        "🧹 <b>Pembersihan selesai</b>\n"
        f"• Job dibatalkan  : <b>{stopped}</b> ({scope_label})\n"
        f"• Job masih aktif : {len(registry)}\n"
        f"• File dihapus    : {total_files}{size_line}\n"
        f"   – tmp: {tmp_files}, downloads: {dl_files}\n"
        f"• Durasi          : {time.time() - started:.1f}s"
        + partial_note,
        parse_mode=ParseMode.HTML,
    )


async def cb_sysinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Memperbarui…")
    settings = context.application.bot_data["settings"]
    started_at = context.application.bot_data.get("started_at")
    data = system_info.collect(settings.ffmpeg, started_at)
    if len(data.get("distro", "")) > 60:
        data["distro"] = data["distro"][:60]
    with contextlib.suppress(BadRequest):
        await query.edit_message_text(
            system_info.format_report(data), parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


# --------------------------------------------------------------------------- #
# Download commands
# --------------------------------------------------------------------------- #
async def _handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                           platform: str | None, allow_search: bool = False) -> None:
    settings = context.application.bot_data["settings"]
    semaphore: asyncio.Semaphore = context.application.bot_data["semaphore"]
    message = update.effective_message
    args = list(context.args or [])
    if not args:
        label = PLATFORM_LABELS.get(platform or "", "media")
        await message.reply_text(
            f"Kirim link {label} setelah perintah.\nContoh: <code>/{platform or 'dl'} "
            f"https://...</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    audio_only = False
    if args and args[0].lower() in {"audio", "mp3", "mp4a"}:
        audio_only = True
        args = args[1:]
        if not args:
            await message.reply_text("Contoh: <code>/yt audio https://youtu.be/...</code>",
                                     parse_mode=ParseMode.HTML)
            return

    raw = " ".join(args).strip()
    url, detected = _resolve_target(raw, platform, allow_search)
    if url is None:
        await message.reply_text(
            "Link tidak dikenali. Platform yang didukung: YouTube, TikTok, Instagram.",
        )
        return

    platform = platform or detected or "youtube"
    label = PLATFORM_LABELS.get(platform, platform)
    target = url if url.startswith("http") else f'pencarian "{raw}"'

    status = await message.reply_text(
        f"🔎 <b>Memproses {label}</b>\n<code>{html.escape(target[:200])}</code>",
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )

    if semaphore.locked():
        with contextlib.suppress(BadRequest):
            await status.edit_text(
                f"⏳ <b>Menunggu antrean…</b>\nServer sedang memproses unduhan lain.\n"
                f"<code>{html.escape(target[:200])}</code>",
                parse_mode=ParseMode.HTML,
            )

    progress = Progress()
    header = f"⬇️ <b>{label}</b>"
    registry: jobs.JobRegistry = context.application.bot_data["jobs"]
    record = registry.add(
        user_id=getattr(update.effective_user, "id", None),
        chat_id=getattr(update.effective_chat, "id", None),
        url=url,
        platform=platform,
        progress=progress,
    )

    async with semaphore:
        if record.cancelled:
            registry.remove(record.id)
            await _edit(status, "🛑 <b>Unduhan dibatalkan</b> sebelum mulai.")
            return

        task = asyncio.create_task(
            download_media(
                settings, url, platform=platform, audio_only=audio_only, progress=progress,
            ),
            name=f"download-{record.id}",
        )
        record.task = task
        try:
            async with LiveStatus(status, progress, header):
                result = await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise  # the cancellation came from this handler itself, not from /cleanup
            log.info("Job %s cancelled.", record.id)
            await _edit(status, "🛑 <b>Unduhan dibatalkan</b> oleh /cleanup.")
            return
        except DownloadError as exc:
            if record.cancelled:
                await _edit(status, "🛑 <b>Unduhan dibatalkan</b> oleh /cleanup.")
                return
            await _edit(status, f"❌ <b>Gagal</b>\n\n{html.escape(str(exc))}")
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error while downloading %s", url)
            await _edit(status, f"❌ <b>Kesalahan tak terduga</b>\n<code>{html.escape(str(exc)[:200])}</code>")
            return
        finally:
            record.task = None
            registry.remove(record.id)

        if not result.items:
            await _edit(status, "❌ Tidak ada media yang berhasil diunduh.")
            return

        await _edit(
            status,
            f"📤 <b>Mengunggah {len(result.items)} file…</b>\n"
            f"<b>{html.escape((result.title or '')[:120])}</b>",
        )
        notes = []
        try:
            notes = await send_result(
                context.bot, update.effective_chat.id, result, settings,
                reply_to=message.message_id,
            )
        finally:
            # safety net: whatever happens, downloaded files are never left behind
            removed = telegram_io.cleanup_items(result.items)
            if removed:
                log.debug("Removed %d downloaded files after sending.", removed)

        for note in result.notes:
            notes.append(f"ℹ️ {note}")
        if notes:
            await message.reply_text(
                "\n".join(notes)[:3800], parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        with contextlib.suppress(BadRequest):
            await status.delete()


def _resolve_target(raw: str, platform: str | None, allow_search: bool) -> tuple[str | None, str | None]:
    found = find_supported_url(raw)
    if found:
        return found[0], found[1]
    if raw.startswith("http://") or raw.startswith("https://"):
        return None, None
    if allow_search and (platform in (None, "youtube")) and raw:
        return f"ytsearch1:{raw}", "youtube"
    return None, None


async def _edit(message, text: str) -> None:
    with contextlib.suppress(BadRequest, Exception):
        await message.edit_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@restricted
async def cmd_youtube(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_download(update, context, platform="youtube", allow_search=True)


@restricted
async def cmd_tiktok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_download(update, context, platform="tiktok")


@restricted
async def cmd_instagram(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_download(update, context, platform="instagram")


@restricted
async def cmd_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain text message that contains a supported link."""
    message = update.effective_message
    text = message.text or message.caption or ""
    found = find_supported_url(text)
    if not found:
        chat = update.effective_chat
        # in groups: ignore ordinary chatter; in DMs: give a hint
        if getattr(chat, "type", "private") == "private":
            await message.reply_text(
                "Link tidak dikenali. Kirim link YouTube, TikTok, atau Instagram "
                "atau lihat /help.",
            )
        return
    url, platform = found
    context.args = [url]
    await _handle_download(update, context, platform=platform)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling update: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                "❌ Terjadi kesalahan internal. Silakan coba lagi."
            )


async def heartbeat(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Write the liveness marker so the container healthcheck knows the event loop is alive."""
    health.touch(context.application.bot_data["settings"])


async def janitor(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clean up temporary files & leftover downloads."""
    from .downloaders import cleanup_old_files, cleanup_old_jobs

    settings = context.application.bot_data["settings"]
    removed_tmp = cleanup_old_jobs(settings.temp_dir, settings.temp_ttl_minutes)
    removed_dl = cleanup_old_files(settings.download_dir, settings.temp_ttl_minutes)
    if removed_tmp or removed_dl:
        log.info(
            "Janitor removed %d temporary items and %d old download files.",
            removed_tmp, removed_dl,
        )
