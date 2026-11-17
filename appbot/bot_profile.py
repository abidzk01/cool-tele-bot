"""Push the command list and bot profile to Telegram so the "/" menu appears.

Runs automatically when the bot starts (see bot.py --push-commands to run it manually).
The menu list is derived from the CommandHandlers that are actually registered, so it
cannot drift away from the code.
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

# Commands shown in the "/" menu (the order follows this list).
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

# Alternative names deliberately hidden from the menu (still work when typed).
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
    """Result of pushing the configuration to Telegram."""

    applied: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    menu_size: int = 0
    unexpected: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        parts = [f"{len(self.applied)} settings applied"]
        if self.menu_size:
            parts.append(f"{self.menu_size} commands in the menu")
        if self.unexpected:
            parts.append("commands without a description: " + ", ".join(sorted(self.unexpected)))
        if self.failed:
            parts.append("failed: " + "; ".join(self.failed))
        return " | ".join(parts)


def registered_commands(application) -> set[str]:
    """Every command name that is actually registered in application."""
    found: set[str] = set()
    for group in application.handlers.values():
        for handler in group:
            for name in getattr(handler, "commands", None) or ():
                found.add(str(name))
    return found


def collect_menu(application=None) -> list[BotCommand]:
    """Build the menu from registered commands (all of them when application=None)."""
    registered = registered_commands(application) if application is not None else None
    menu: list[BotCommand] = []
    for name, description in MENU_COMMANDS:
        if registered is None or name in registered:
            menu.append(BotCommand(name, description))
    return menu


def unexpected_commands(application) -> set[str]:
    """Registered commands that are neither an official menu entry nor a known alias."""
    known = {name for name, _ in MENU_COMMANDS} | set(ALIASES)
    return registered_commands(application) - known


async def push_bot_profile(bot: Bot, application=None) -> PushReport:
    """Push the command list (default/private/group), description, and menu button.

    Every call is wrapped in try/except so that a single failure (e.g. a description that is
    too long or an unsupported feature) does not cancel the remaining settings.
    """
    report = PushReport()
    menu = collect_menu(application)
    report.menu_size = len(menu)
    if application is not None:
        report.unexpected = unexpected_commands(application)

    scopes = (
        ("default", BotCommandScopeDefault()),
        ("private chats", BotCommandScopeAllPrivateChats()),
        ("groups", BotCommandScopeAllGroupChats()),
        ("group admins", BotCommandScopeAllChatAdministrators()),
    )

    tasks: list[tuple[str, object]] = []
    for label, scope in scopes:
        tasks.append((f"commands ({label})", lambda s=scope: bot.set_my_commands(menu, scope=s)))
    tasks.append(("short description", lambda: bot.set_my_short_description(SHORT_DESCRIPTION)))
    tasks.append(("description", lambda: bot.set_my_description(DESCRIPTION)))
    tasks.append(("menu button", lambda: bot.set_chat_menu_button(menu_button=MenuButtonCommands())))

    for label, factory in tasks:
        try:
            await factory()
        except TelegramError as exc:
            report.failed.append(f"{label}: {str(exc)[:120]}")
            log.warning("Failed to apply %s: %s", label, exc)
        except Exception as exc:  # noqa: BLE001
            report.failed.append(f"{label}: {type(exc).__name__}")
            log.warning("Failed to apply %s: %s", label, exc)
        else:
            report.applied.append(label)

    if report.ok:
        log.info("Bot profile updated: %s", report.summary())
    else:
        log.warning("Bot profile partially failed: %s", report.summary())
    return report
