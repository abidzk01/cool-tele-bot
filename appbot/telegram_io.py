"""Helpers for sending media to Telegram (size limits, captions, chat actions)."""
from __future__ import annotations

import contextlib
import html
import logging
from pathlib import Path

from telegram import InputFile, ReplyParameters
from telegram.constants import ChatAction, ParseMode

from .downloaders.base import DownloadResult, MediaItem, human_bytes, human_duration

log = logging.getLogger("appbot.telegram_io")

CAPTION_LIMIT = 1024
MEDIA_TIMEOUT = 600


def build_caption(result: DownloadResult, item: MediaItem, index: int | None = None,
                  total: int = 1) -> str:
    title = (item.title or result.title or "Media").strip()
    title = html.escape(title)[:600]
    lines = [f"<b>{title}</b>"]
    meta: list[str] = []
    if result.uploader:
        meta.append(f"👤 {html.escape(str(result.uploader))}")
    if item.duration:
        meta.append(f"⏱ {human_duration(item.duration)}")
    if item.filesize:
        meta.append(f"💾 {human_bytes(item.filesize)}")
    if item.width and item.height:
        meta.append(f"📐 {item.width}x{item.height}")
    if total > 1 and index is not None:
        meta.append(f"📎 {index}/{total}")
    if meta:
        lines.append(" | ".join(meta))
    caption = "\n".join(lines)
    return caption[:CAPTION_LIMIT]


async def send_result(bot, chat_id: int, result: DownloadResult, settings,
                      reply_to: int | None = None) -> list[str]:
    """Send every media item produced by the download. Returns the list of notes/warnings."""
    notes: list[str] = []
    total = len(result.items)
    reply_parameters = ReplyParameters(message_id=reply_to) if reply_to else None

    for index, item in enumerate(result.items, start=1):
        if not item.path.is_file():
            notes.append(f"⚠️ File hilang: {item.path.name}")
            continue

        # Every path (sent, skipped, or failed) ends with the file deleted.
        try:
            size = item.filesize
            if size > settings.max_upload_bytes:
                notes.append(
                    f"⚠️ <code>{html.escape(item.path.name)}</code> ({human_bytes(size)}) "
                    f"melebihi batas upload {human_bytes(settings.max_upload_bytes)} dan tidak dikirim."
                )
                continue

            caption = build_caption(result, item, index, total)
            action = {
                "video": ChatAction.UPLOAD_VIDEO,
                "audio": ChatAction.UPLOAD_VOICE,
                "photo": ChatAction.UPLOAD_PHOTO,
            }.get(item.kind, ChatAction.UPLOAD_DOCUMENT)
            with contextlib.suppress(Exception):
                await bot.send_chat_action(chat_id=chat_id, action=action)

            try:
                with item.path.open("rb") as fh:
                    if item.kind == "photo":
                        await bot.send_photo(
                            chat_id=chat_id, photo=fh, caption=caption,
                            parse_mode=ParseMode.HTML, filename=item.path.name,
                            reply_parameters=reply_parameters,
                            read_timeout=MEDIA_TIMEOUT, write_timeout=MEDIA_TIMEOUT,
                        )
                    elif item.kind == "audio":
                        await bot.send_audio(
                            chat_id=chat_id, audio=fh, caption=caption,
                            parse_mode=ParseMode.HTML, filename=item.path.name,
                            title=item.title[:64] if item.title else None,
                            duration=item.duration,
                            reply_parameters=reply_parameters,
                            read_timeout=MEDIA_TIMEOUT, write_timeout=MEDIA_TIMEOUT,
                        )
                    elif item.kind == "video":
                        await bot.send_video(
                            chat_id=chat_id, video=fh, caption=caption,
                            parse_mode=ParseMode.HTML, filename=item.path.name,
                            width=item.width, height=item.height, duration=item.duration,
                            supports_streaming=True,
                            reply_parameters=reply_parameters,
                            read_timeout=MEDIA_TIMEOUT, write_timeout=MEDIA_TIMEOUT,
                        )
                    else:
                        await bot.send_document(
                            chat_id=chat_id, document=fh, caption=caption,
                            parse_mode=ParseMode.HTML, filename=item.path.name,
                            reply_parameters=reply_parameters,
                            read_timeout=MEDIA_TIMEOUT, write_timeout=MEDIA_TIMEOUT,
                        )
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to send %s: %s", item.path.name, exc)
                # last attempt: send it as a document
                try:
                    with item.path.open("rb") as fh:
                        await bot.send_document(
                            chat_id=chat_id, document=fh, filename=item.path.name,
                            caption=caption, parse_mode=ParseMode.HTML,
                            read_timeout=MEDIA_TIMEOUT, write_timeout=MEDIA_TIMEOUT,
                        )
                except Exception as exc2:  # noqa: BLE001
                    notes.append(
                        f"⚠️ Gagal mengirim <code>{html.escape(item.path.name)}</code>: "
                        f"{html.escape(str(exc2)[:150])}"
                    )
        finally:
            cleanup_file(item.path)
    return notes


def cleanup_items(items) -> int:
    """Delete every downloaded file (used as a safety net in the handler)."""
    removed = 0
    for item in items or ():
        path = getattr(item, "path", None)
        if path is not None:
            cleanup_file(path)
            removed += 1
    return removed


def cleanup_file(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
