"""System information for the /sysinfo command."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psutil

APP_VERSION = "1.0.0"


def _human_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(num) < 1024.0:
            return f"{num:,.2f} {unit}".replace(",", ".")
        num /= 1024.0
    return f"{num:,.2f} EB"


def _human_uptime(seconds: float) -> str:
    delta = timedelta(seconds=int(seconds))
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} hari")
    if hours:
        parts.append(f"{hours} jam")
    if minutes:
        parts.append(f"{minutes} menit")
    if not parts:
        parts.append(f"{secs} detik")
    return " ".join(parts)


def _cpu_model() -> str:
    try:
        if Path("/proc/cpuinfo").is_file():
            for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
                if line.lower().startswith(("model name", "hardware", "model")):
                    if ":" in line:
                        return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _binary_version(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        text = (out.stdout or out.stderr).strip().splitlines()
        return text[0] if text else None
    except (OSError, subprocess.SubprocessError):
        return None


def _external_ip() -> str | None:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2) as sock:
            return sock.getsockname()[0]
    except OSError:
        return None


def collect(ffmpeg_path: str | None = None, started_at: float | None = None) -> dict:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    boot = psutil.boot_time()
    proc = psutil.Process(os.getpid())
    disk_path = Path(__file__).resolve().parent.parent
    try:
        disk = shutil.disk_usage(disk_path)
        disk_total, disk_free = disk.total, disk.free
    except OSError:
        disk_total = disk_free = 0
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        load1 = load5 = load15 = 0.0

    # non-blocking CPU snapshot, falling back to a short interval
    cpu_percent = psutil.cpu_percent(interval=None)
    if cpu_percent == 0.0:
        cpu_percent = psutil.cpu_percent(interval=0.3)

    try:
        users = len(psutil.users())
    except (psutil.Error, OSError):
        users = 0

    return {
        "app_version": APP_VERSION,
        "hostname": socket.gethostname(),
        "ip": _external_ip(),
        "os": f"{platform.system()} {platform.release()}",
        "distro": platform.platform(),
        "arch": platform.machine(),
        "kernel": platform.version(),
        "python": platform.python_version(),
        "cpu_model": _cpu_model(),
        "cpu_cores_physical": psutil.cpu_count(logical=False),
        "cpu_cores_logical": psutil.cpu_count(logical=True),
        "cpu_percent": cpu_percent,
        "load": (load1, load5, load15),
        "ram_used": vm.used,
        "ram_total": vm.total,
        "ram_percent": vm.percent,
        "swap_used": swap.used,
        "swap_total": swap.total,
        "swap_percent": swap.percent,
        "disk_used": disk_total - disk_free,
        "disk_total": disk_total,
        "disk_free": disk_free,
        "disk_percent": (1 - disk_free / disk_total) * 100 if disk_total else 0.0,
        "boot_time": boot,
        "uptime": time.time() - boot,
        "bot_uptime": time.time() - started_at if started_at else 0.0,
        "bot_rss": proc.memory_info().rss,
        "bot_cpu": proc.cpu_percent(interval=None),
        "bot_threads": proc.num_threads(),
        "users": users,
        "ffmpeg": ffmpeg_path,
        "ffmpeg_version": _binary_version([ffmpeg_path, "-version"]) if ffmpeg_path else None,
        "ytdlp_version": _ytdlp_version(),
        "checked_at": datetime.now(timezone.utc),
        "python_exe": sys.executable,
    }


def _ytdlp_version() -> str | None:
    try:
        import yt_dlp  # noqa: PLC0415

        return getattr(yt_dlp.version, "__version__", None)
    except Exception:  # noqa: BLE001
        return None


def _bar(percent: float, width: int = 12) -> str:
    filled = int(round(max(0.0, min(100.0, percent)) / 100 * width))
    return "█" * filled + "░" * (width - filled)


def format_report(d: dict) -> str:
    load1, load5, load15 = d["load"]
    boot_dt = datetime.fromtimestamp(d["boot_time"]).strftime("%d %b %Y %H:%M")
    lines = [
        "<b>🖥 Informasi Sistem</b>",
        "",
        "<b>Host</b>",
        f"• Nama       : <code>{d['hostname']}</code>",
        f"• IP lokal   : <code>{d['ip'] or 'n/a'}</code>",
        f"• OS         : {d['os']} ({d['arch']})",
        f"• Distro     : {d['distro']}",
        f"• Kernel     : {d['kernel']}",
        f"• Uptime     : {_human_uptime(d['uptime'])}",
        f"• Boot       : {boot_dt}",
        f"• Login user : {d['users']}",
        "",
        "<b>CPU</b>",
        f"• Model      : {d['cpu_model']}",
        f"• Core       : {d['cpu_cores_physical']} fisik / {d['cpu_cores_logical']} logis",
        f"• Beban CPU  : {d['cpu_percent']:.1f}%  {_bar(d['cpu_percent'])}",
        f"• Load avg   : {load1:.2f} / {load5:.2f} / {load15:.2f}",
        "",
        "<b>Memori</b>",
        f"• RAM        : {_human_bytes(d['ram_used'])} / {_human_bytes(d['ram_total'])} "
        f"({d['ram_percent']:.1f}%)  {_bar(d['ram_percent'])}",
        f"• Swap       : {_human_bytes(d['swap_used'])} / {_human_bytes(d['swap_total'])} "
        f"({d['swap_percent']:.1f}%)",
        "",
        "<b>Penyimpanan</b>",
        f"• Disk       : {_human_bytes(d['disk_used'])} / {_human_bytes(d['disk_total'])} "
        f"({d['disk_percent']:.1f}%)  {_bar(d['disk_percent'])}",
        f"• Sisa       : {_human_bytes(d['disk_free'])}",
        "",
        "<b>Bot</b>",
        f"• Versi      : {d['app_version']}",
        f"• Uptime bot : {_human_uptime(d['bot_uptime'])}",
        f"• Memori bot : {_human_bytes(d['bot_rss'])}",
        f"• Thread     : {d['bot_threads']}",
        f"• Python     : {d['python']}",
        f"• yt-dlp     : {d['ytdlp_version'] or 'tidak tersedia'}",
        f"• ffmpeg     : {'✅ ' + (d['ffmpeg_version'] or d['ffmpeg'] or '') if d['ffmpeg'] else '❌ tidak ditemukan'}",
        "",
        f"<i>Diperbarui {d['checked_at'].strftime('%d %b %Y %H:%M:%S')} UTC</i>",
    ]
    return "\n".join(lines)
