"""Dorong daftar perintah & profil bot ke Telegram agar menu "/" otomatis muncul.

Dijalankan otomatis saat bot start (lihat bot.py --push-commands untuk menjalankan manual).
Daftar menu diturunkan dari CommandHandler yang benar-benar terdaftar, sehingga tidak
mungkin melenceng dari kode.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from telegram import (
    Bot,
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    MenuButtonCommands,
)
from telegram.error import TelegramError

log = logging.getLogger("appbot.profile")

# Perintah yang ditampilkan di menu "/" (urutannya mengikuti daftar ini).
MENU_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Mulai bot & ringkasan fitur"),
    ("help", "Bantuan & daftar perintah"),
    ("yt", "Unduh video/audio YouTube"),
    ("tt", "Unduh TikTok tanpa watermark"),
    ("ig", "Unduh Instagram (reel & post)"),
    ("sysinfo", "Info CPU, RAM, disk & status bot"),
    ("cleanup", "Batalkan unduhan & bersihkan file sisa"),
    ("id", "Lihat user ID & chat ID"),
)

# Nama alternatif yang sengaja tidak ditampilkan di menu (tetap berfungsi bila diketik).
ALIASES: dict[str, str] = {
    "youtube": "yt",
    "ytdl": "yt",
    "tiktok": "tt",
    "ttdl": "tt",
    "instagram": "ig",
    "igdl": "ig",
    "bersihkan": "cleanup",
    "clean": "cleanup",
}

SHORT_DESCRIPTION = (
    "Bot info sistem + downloader YouTube, TikTok tanpa watermark, dan Instagram."
)

DESCRIPTION = (
    "Bot Telegram untuk memantau server dan mengunduh media.\n\n"
    "• /sysinfo — CPU, RAM, disk, uptime, dan status bot\n"
    "• /yt — video atau audio YouTube (mendukung Shorts & playlist)\n"
    "• /tt — video TikTok tanpa watermark, termasuk slideshow foto\n"
    "• /ig — reel, post, dan carousel Instagram\n"
    "• /cleanup — batalkan unduhan berjalan & hapus file sisa\n\n"
    "Kirim saja link-nya langsung ke chat ini dan bot akan mengenali platformnya.\n"
    "Ketik / untuk melihat daftar perintah."
)


@dataclass
class PushReport:
    """Hasil pendorongan konfigurasi ke Telegram."""

    applied: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    menu_size: int = 0
    unexpected: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        parts = [f"{len(self.applied)} pengaturan diterapkan"]
        if self.menu_size:
            parts.append(f"{self.menu_size} perintah di menu")
        if self.unexpected:
            parts.append("perintah tanpa deskripsi: " + ", ".join(sorted(self.unexpected)))
        if self.failed:
            parts.append("gagal: " + "; ".join(self.failed))
        return " | ".join(parts)


def registered_commands(application) -> set[str]:
    """Semua nama perintah yang benar-benar terdaftar di application."""
    found: set[str] = set()
    for group in application.handlers.values():
        for handler in group:
            for name in getattr(handler, "commands", None) or ():
                found.add(str(name))
    return found


def collect_menu(application=None) -> list[BotCommand]:
    """Bangun daftar menu dari perintah terdaftar (semua bila application=None)."""
    registered = registered_commands(application) if application is not None else None
    menu: list[BotCommand] = []
    for name, description in MENU_COMMANDS:
        if registered is None or name in registered:
            menu.append(BotCommand(name, description))
    return menu


def unexpected_commands(application) -> set[str]:
    """Perintah terdaftar yang bukan menu resmi maupun alias yang diketahui."""
    known = {name for name, _ in MENU_COMMANDS} | set(ALIASES)
    return registered_commands(application) - known


async def push_bot_profile(bot: Bot, application=None) -> PushReport:
    """Dorong daftar perintah (default/DM/grup), deskripsi, dan menu button.

    Setiap panggilan dibungkus try/except agar satu kegagalan (mis. deskripsi terlalu
    panjang atau fitur tidak didukung) tidak membatalkan pengaturan lainnya.
    """
    report = PushReport()
    menu = collect_menu(application)
    report.menu_size = len(menu)
    if application is not None:
        report.unexpected = unexpected_commands(application)

    scopes = (
        ("default", BotCommandScopeDefault()),
        ("chat pribadi", BotCommandScopeAllPrivateChats()),
        ("grup", BotCommandScopeAllGroupChats()),
        ("admin grup", BotCommandScopeAllChatAdministrators()),
    )

    tasks: list[tuple[str, object]] = []
    for label, scope in scopes:
        tasks.append((f"perintah ({label})", lambda s=scope: bot.set_my_commands(menu, scope=s)))
    tasks.append(("deskripsi singkat", lambda: bot.set_my_short_description(SHORT_DESCRIPTION)))
    tasks.append(("deskripsi", lambda: bot.set_my_description(DESCRIPTION)))
    tasks.append(("menu button", lambda: bot.set_chat_menu_button(menu_button=MenuButtonCommands())))

    for label, factory in tasks:
        try:
            await factory()
        except TelegramError as exc:
            report.failed.append(f"{label}: {str(exc)[:120]}")
            log.warning("Gagal menerapkan %s: %s", label, exc)
        except Exception as exc:  # noqa: BLE001
            report.failed.append(f"{label}: {type(exc).__name__}")
            log.warning("Gagal menerapkan %s: %s", label, exc)
        else:
            report.applied.append(label)

    if report.ok:
        log.info("Profil bot diperbarui: %s", report.summary())
    else:
        log.warning("Profil bot sebagian gagal: %s", report.summary())
    return report
