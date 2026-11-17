#!/usr/bin/env python3
"""Entry point bot Telegram: info sistem + downloader YouTube/TikTok/Instagram.

Pemakaian:
    python bot.py            # jalankan bot (long polling)
    python bot.py --check    # validasi token & dependency lalu keluar
    python bot.py --sysinfo  # cetak laporan info sistem ke terminal
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import time

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from appbot import bot_profile, handlers, health, jobs, system_info
from appbot.logging_setup import setup_logging
from config import BASE_DIR, Settings, load_settings

log = logging.getLogger("appbot.main")


def build_application(settings: Settings) -> Application:
    builder = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .rate_limiter(AIORateLimiter())
        .concurrent_updates(True)
        .connect_timeout(30.0)
        .read_timeout(60.0)
        .write_timeout(120.0)
        .media_write_timeout(600.0)
        .pool_timeout(30.0)
    )
    if settings.local_api_url:
        base = settings.local_api_url.rstrip("/")
        builder = builder.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot")

    application = builder.build()

    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    application.bot_data["settings"] = settings
    application.bot_data["started_at"] = time.time()
    application.bot_data["semaphore"] = asyncio.Semaphore(settings.max_concurrent)
    application.bot_data["jobs"] = jobs.JobRegistry()

    application.add_handler(CommandHandler("start", handlers.cmd_start))
    application.add_handler(CommandHandler("help", handlers.cmd_help))
    application.add_handler(CommandHandler("id", handlers.cmd_id))
    application.add_handler(CommandHandler("sysinfo", handlers.cmd_sysinfo))
    application.add_handler(
        CommandHandler(["cleanup", "bersihkan", "clean"], handlers.cmd_cleanup)
    )
    application.add_handler(CommandHandler(["yt", "youtube", "ytdl"], handlers.cmd_youtube))
    application.add_handler(CommandHandler(["tt", "tiktok", "ttdl"], handlers.cmd_tiktok))
    application.add_handler(CommandHandler(["ig", "instagram", "igdl"], handlers.cmd_instagram))
    application.add_handler(CallbackQueryHandler(handlers.cb_sysinfo, pattern=r"^sysinfo:refresh$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.cmd_auto))
    application.add_error_handler(handlers.error_handler)

    if application.job_queue is not None:
        application.job_queue.run_repeating(handlers.janitor, interval=900, first=120)
        application.job_queue.run_repeating(handlers.heartbeat, interval=60, first=5)
    return application


async def _post_init(application: Application) -> None:
    try:
        me = await application.bot.get_me()
        log.info("Bot aktif sebagai @%s (id %s)", me.username, me.id)
    except TelegramError as exc:
        log.error("Gagal memanggil getMe: %s", exc)
        await application.stop()
        return

    with contextlib.suppress(OSError):
        health.touch(application.bot_data["settings"])

    # Daftarkan menu "/" (default, DM, grup) + deskripsi bot ke Telegram.
    report = await bot_profile.push_bot_profile(application.bot, application)
    if report.ok:
        try:
            stored = await application.bot.get_my_commands()
            log.info("Menu perintah aktif: %s", ", ".join(f"/{c.command}" for c in stored))
        except TelegramError as exc:
            log.debug("Tidak bisa membaca ulang daftar perintah: %s", exc)


async def verify_token(token: str) -> str | None:
    """Kembalikan pesan masalah bila token tidak bisa dipakai, atau None bila OK."""
    from telegram import Bot
    from telegram.error import InvalidToken, TelegramError

    try:
        await Bot(token).get_me()
    except InvalidToken:
        return "Token tidak valid. Ambil ulang token dari @BotFather lalu perbarui .env."
    except TelegramError as exc:
        return f"Tidak bisa menghubungi Telegram API: {exc}"
    return None


def run_bot(settings: Settings) -> None:
    application = build_application(settings)
    application.post_init = _post_init
    log.info(
        "Mulai polling. Batas upload %.0f MB | maks %sp | paralel %d | ffmpeg: %s",
        settings.max_upload_mb, settings.max_video_height, settings.max_concurrent,
        settings.ffmpeg or "TIDAK ADA",
    )
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
    )


async def check_token(settings: Settings) -> int:
    from telegram import Bot

    bot = Bot(settings.bot_token)
    try:
        me = await bot.get_me()
    except TelegramError as exc:
        log.error("Token tidak valid atau jaringan bermasalah: %s", exc)
        return 2
    print(f"✅ Token valid: @{me.username} (id {me.id})")
    print(f"   Nama bot       : {me.first_name}")
    print(f"   ffmpeg         : {settings.ffmpeg or 'TIDAK DITEMUKAN'}")
    print(f"   Batas upload   : {settings.max_upload_mb:.0f} MB")
    print(f"   Resolusi maks  : {settings.max_video_height}p")
    print(f"   Download dir   : {settings.download_dir}")
    try:
        import yt_dlp

        print(f"   yt-dlp         : {yt_dlp.version.__version__}")
    except Exception:  # noqa: BLE001
        print("   yt-dlp         : TIDAK TERPASANG")
    return 0


async def push_commands_only(settings: Settings) -> int:
    """Dorong menu perintah ke Telegram tanpa menjalankan bot (baca ulang untuk verifikasi)."""
    from telegram import Bot

    problem = await verify_token(settings.bot_token)
    if problem:
        log.error("%s", problem)
        return 2

    bot = Bot(settings.bot_token)
    application = build_application(settings)
    report = await bot_profile.push_bot_profile(bot, application)

    print("\n📋 Hasil pendorongan ke Telegram")
    for item in report.applied:
        print(f"   ✅ {item}")
    for item in report.failed:
        print(f"   ❌ {item}")
    if report.unexpected:
        print(f"   ⚠️  perintah tanpa deskripsi di MENU_COMMANDS: {', '.join(sorted(report.unexpected))}")

    if report.ok:
        try:
            stored = await bot.get_my_commands()
        except TelegramError as exc:
            print(f"   ⚠️  gagal membaca ulang daftar perintah: {exc}")
        else:
            print(f"\n🔎 Daftar perintah yang tersimpan di Telegram ({len(stored)}):")
            for cmd in stored:
                print(f"   /{cmd.command} — {cmd.description}")
    return 0 if report.ok else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Bot Telegram: info sistem + downloader media")
    parser.add_argument("--check", action="store_true", help="validasi token dan dependency lalu keluar")
    parser.add_argument("--sysinfo", action="store_true", help="cetak info sistem lalu keluar")
    parser.add_argument(
        "--push-commands",
        action="store_true",
        help="dorong daftar perintah & deskripsi bot ke Telegram lalu keluar",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="cek heartbeat (exit 0 sehat, 1 tidak) — dipakai healthcheck Docker/Podman",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="log level DEBUG")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.verbose else logging.INFO)

    if args.sysinfo:
        settings = load_settings()
        data = system_info.collect(settings.ffmpeg, None)
        import html
        import re

        text = system_info.format_report(data)
        print(re.sub(r"<[^>]+>", "", html.unescape(text)))
        return 0

    settings = load_settings()

    if args.health:
        print(health.describe(settings))
        return 0 if health.is_healthy(settings) else 1

    if not settings.bot_token:
        log.error(
            "BOT_TOKEN belum diisi. Salin .env.example menjadi .env lalu isi token dari @BotFather."
        )
        return 1

    if args.check:
        return asyncio.run(check_token(settings))

    if args.push_commands:
        return asyncio.run(push_commands_only(settings))

    problem = asyncio.run(verify_token(settings.bot_token))
    if problem:
        log.error("%s", problem)
        return 2

    try:
        run_bot(settings)
    except KeyboardInterrupt:
        log.info("Dihentikan oleh pengguna.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
