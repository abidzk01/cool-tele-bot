"""Tes unit & integrasi ringan (tanpa jaringan, tanpa token Telegram).

Jalankan: .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import BotCommand, MenuButtonCommands  # noqa: E402
from telegram.error import TelegramError  # noqa: E402

from types import SimpleNamespace  # noqa: E402

from appbot import bot_profile, handlers, health, jobs, system_info  # noqa: E402
from appbot.downloaders import base, tiktok, youtube  # noqa: E402
from appbot.downloaders import detect_platform, extract_urls, find_supported_url  # noqa: E402
from config import load_settings  # noqa: E402


class TestPlatformDetection(unittest.TestCase):
    def test_detects_youtube_variants(self):
        for url in (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=abc",
            "https://www.youtube.com/shorts/abc123",
            "https://music.youtube.com/watch?v=abc",
        ):
            self.assertEqual(detect_platform(url), "youtube", url)

    def test_detects_tiktok_and_instagram(self):
        self.assertEqual(detect_platform("https://www.tiktok.com/@user/video/123"), "tiktok")
        self.assertEqual(detect_platform("https://vm.tiktok.com/ZMabc/"), "tiktok")
        self.assertEqual(detect_platform("https://www.instagram.com/reel/ABC/"), "instagram")
        self.assertEqual(detect_platform("https://instagr.am/p/ABC/"), "instagram")

    def test_rejects_other_hosts(self):
        for url in (
            "https://twitter.com/x/status/1",
            "https://vimeo.com/12345",
            "https://fakeyoutube.com/watch?v=1",
            "bukan url",
            "",
        ):
            self.assertIsNone(detect_platform(url), url)

    def test_extract_and_find_url(self):
        text = "coba link ini https://youtu.be/abcd1234 ya, dan ini https://example.com"
        urls = extract_urls(text)
        self.assertIn("https://youtu.be/abcd1234", urls)
        found = find_supported_url(text)
        self.assertIsNotNone(found)
        self.assertEqual(found[1], "youtube")

    def test_find_url_skips_unsupported_first(self):
        text = "https://example.com/a https://www.tiktok.com/@a/video/9"
        found = find_supported_url(text)
        self.assertEqual(found[0], "https://www.tiktok.com/@a/video/9")
        self.assertEqual(found[1], "tiktok")


class TestFormatSelection(unittest.TestCase):
    def test_tiktok_prefers_no_watermark(self):
        info = {
            "formats": [
                {"format_id": "download_addr-0", "format_note": "watermarked", "height": 720,
                 "acodec": "aac", "tbr": 900},
                {"format_id": "play_addr-0", "format_note": "no watermark", "height": 720,
                 "acodec": "aac", "tbr": 800},
            ]
        }
        chosen = tiktok._pick_format(info)
        self.assertIn("play_addr-0", chosen)
        self.assertNotIn("download_addr-0", chosen.split("/")[0])

    def test_tiktok_adds_audio_fallback_for_video_only(self):
        info = {"formats": [{"format_id": "video_only", "acodec": "none", "height": 1080}]}
        self.assertEqual(tiktok._pick_format(info), "video_only+bestaudio/video_only/best")

    def test_tiktok_empty_formats(self):
        self.assertEqual(tiktok._pick_format({}), "best")

    def test_youtube_height_ladder_never_exceeds_max(self):
        self.assertEqual(youtube._heights(1080)[:4], [1080, 720, 480, 360])
        self.assertEqual(youtube._heights(480), [480, 360, 144])
        self.assertEqual(youtube._heights(144), [144])
        for h in (1080, 720, 480):
            self.assertTrue(all(step <= h for step in youtube._heights(h)))

    def test_youtube_selector_contains_size_guard(self):
        selector = youtube._selector(50 * 1024 * 1024, 720)
        self.assertIn(f"filesize<{50 * 1024 * 1024}", selector)
        self.assertIn("height<=720", selector)
        self.assertIn("+ba", selector)


class TestItemsFromInfo(unittest.TestCase):
    def test_collects_downloaded_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"x" * 128)
            info = {
                "title": "Judul",
                "duration": 12,
                "width": 1080,
                "height": 1920,
                "requested_downloads": [{"filepath": str(video)}],
            }
            items = base.items_from_info(info)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].kind, "video")
            self.assertEqual(items[0].filesize, 128)
            self.assertEqual(items[0].duration, 12)

    def test_recurses_into_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "a.jpg"
            photo.write_bytes(b"y" * 10)
            info = {"entries": [{"requested_downloads": [{"filepath": str(photo)}], "title": "p"}]}
            items = base.items_from_info(info, "photo")
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].kind, "photo")

    def test_kind_for_extensions(self):
        self.assertEqual(base.kind_for(Path("a.MP4")), "video")
        self.assertEqual(base.kind_for(Path("a.mp3")), "audio")
        self.assertEqual(base.kind_for(Path("a.webp")), "photo")


class TestProgressAndHelpers(unittest.TestCase):
    def test_hook_updates_percent_and_speed(self):
        p = base.Progress()
        p.hook({
            "status": "downloading",
            "downloaded_bytes": 512,
            "total_bytes": 1024,
            "speed": 256.0,
            "eta": 2,
            "filename": "/tmp/video.mp4",
            "info_dict": {"playlist_index": 2, "n_entries": 5},
        })
        self.assertEqual(p.percent, 50.0)
        self.assertEqual(p.index, 2)
        self.assertEqual(p.count, 5)
        self.assertEqual(p.filename, "video.mp4")
        text = base.describe_progress(p)
        self.assertIn("50.0%", text)
        self.assertIn("item 2/5", text)

    def test_finished_status_becomes_merging(self):
        p = base.Progress()
        p.hook({"status": "finished"})
        self.assertEqual(p.status, "menggabungkan")
        self.assertEqual(p.percent, 100.0)

    def test_human_helpers(self):
        self.assertEqual(base.human_bytes(2048), "2.00 KB")
        self.assertEqual(base.human_duration(3725), "1:02:05")
        self.assertEqual(base.human_duration(None), "0:00")

    def test_error_mapping_is_friendly(self):
        msg = base.map_ytdlp_error(Exception("Sign in to confirm you're not a bot"), "YouTube")
        self.assertIn("COOKIES_YOUTUBE", msg)
        msg2 = base.map_ytdlp_error(Exception("Video unavailable"), "YouTube")
        self.assertIn("tidak tersedia", msg2)


class TestSystemInfo(unittest.TestCase):
    def test_collect_and_format(self):
        settings = load_settings()
        data = system_info.collect(settings.ffmpeg, None)
        for key in ("hostname", "os", "cpu_percent", "ram_total", "disk_total", "uptime"):
            self.assertIn(key, data)
        report = system_info.format_report(data)
        self.assertIn("Informasi Sistem", report)
        self.assertIn("RAM", report)
        self.assertIn("Disk", report)
        if settings.ffmpeg:
            self.assertIn("✅", report)  # ffmpeg terdeteksi pada laporan

    def test_bars(self):
        self.assertEqual(system_info._bar(0, 4), "░░░░")
        self.assertEqual(system_info._bar(100, 4), "████")


class TestConfig(unittest.TestCase):
    def test_defaults_without_env(self):
        env_backup = dict(os.environ)
        for key in list(os.environ):
            if key.startswith(("BOT_TOKEN", "MAX_", "ALLOWED_", "TIKTOK_", "COOKIES_", "AUTO_", "LOCAL_")):
                os.environ.pop(key, None)
        try:
            s = load_settings()
            self.assertEqual(s.max_upload_bytes, 49 * 1024 * 1024)
            self.assertEqual(s.max_concurrent, 2)
            self.assertTrue(s.is_allowed(123))  # kosong = publik
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_allowed_users_and_limits(self):
        env_backup = dict(os.environ)
        os.environ.update({
            "BOT_TOKEN": "123:abc",
            "ALLOWED_USER_IDS": "11, 22;33",
            "MAX_UPLOAD_MB": "1900",
            "MAX_VIDEO_HEIGHT": "720",
            "TIKTOK_USE_TIKWM": "0",
        })
        try:
            s = load_settings()
            self.assertEqual(s.allowed_user_ids, frozenset({11, 22, 33}))
            self.assertTrue(s.is_allowed(22))
            self.assertFalse(s.is_allowed(99))
            self.assertEqual(s.max_video_height, 720)
            self.assertFalse(s.tiktok_use_tikwm)
            self.assertEqual(s.max_upload_bytes, 1900 * 1024 * 1024)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)


class TestApplicationWiring(unittest.TestCase):
    def test_build_application_registers_handlers(self):
        import bot as bot_module

        s = load_settings()
        s = s.__class__(**{**s.__dict__, "bot_token": "123456:TESTTOKEN"})
        app = bot_module.build_application(s)
        self.assertIsNotNone(app.bot_data.get("semaphore"))
        self.assertIsNotNone(app.bot_data.get("settings"))
        handlers = app.handlers[0]
        flat = set()
        for h in handlers:
            for cmd in getattr(h, "commands", None) or ():
                flat.add(str(cmd))
        for cmd in ("start", "help", "yt", "tt", "ig", "sysinfo", "id"):
            self.assertIn(cmd, flat, f"perintah /{cmd} tidak terdaftar")


class TestFfmpegCompression(unittest.TestCase):
    def test_compress_real_video(self):
        settings = load_settings()
        if not settings.ffmpeg:
            self.skipTest("ffmpeg tidak tersedia")
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            dst = Path(tmp) / "out.mp4"
            gen = subprocess.run(
                [settings.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
                 "-i", "testsrc=size=1280x720:rate=25:duration=4",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                 "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                 "-shortest", str(src)],
                capture_output=True, text=True,
            )
            if gen.returncode != 0 or not src.is_file():
                self.skipTest(f"ffmpeg tidak bisa membuat video uji: {gen.stderr[:120]}")
            from appbot.downloaders.base import compress_to_fit

            compress_to_fit(src, dst, 60 * 1024, 4.0, settings.ffmpeg, max_height=360)
            self.assertTrue(dst.is_file())
            self.assertLess(dst.stat().st_size, src.stat().st_size * 2)


class TestCleanup(unittest.TestCase):
    def test_jobdir_and_cleanup_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = base.JobDir(root, "t")
            job.path.joinpath("f.bin").write_bytes(b"z")
            self.assertTrue(job.path.is_dir())
            job.cleanup()
            self.assertFalse(job.path.exists())

            old = root / "job-old"
            old.mkdir()
            os.utime(old, (0, 0))
            removed = base.cleanup_old_jobs(root, ttl_minutes=1)
            self.assertGreaterEqual(removed, 1)
            self.assertFalse(old.exists())

    def test_cleanup_old_files_sweeps_downloads_but_keeps_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "downloads"
            sub = root / "youtube"
            sub.mkdir(parents=True)
            stale = sub / "lama.mp4"
            stale.write_bytes(b"a")
            fresh = sub / "baru.mp4"
            fresh.write_bytes(b"b")
            os.utime(stale, (0, 0))

            removed = base.cleanup_old_files(root, ttl_minutes=1)
            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists(), "file lama harus dihapus janitor")
            self.assertTrue(fresh.exists(), "file baru tidak boleh dihapus")
            self.assertTrue(sub.is_dir(), "direktori platform dipertahankan")

    def test_cleanup_old_files_noop_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / "x.mp4"
            stale.write_bytes(b"a")
            os.utime(stale, (0, 0))
            self.assertEqual(base.cleanup_old_files(root, ttl_minutes=0), 0)
            self.assertTrue(stale.exists())


class TestTranscodeH264(unittest.TestCase):
    def _make(self, ffmpeg: str, out: Path, encoder: str) -> bool:
        import subprocess

        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
               "-i", "testsrc=size=320x240:rate=15:duration=2",
               "-c:v", encoder, "-pix_fmt", "yuv420p"]
        if "265" in encoder:
            cmd += ["-tag:v", "hvc1"]
        cmd.append(str(out))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return False
        return out.is_file()

    def test_probe_and_transcode(self):
        import subprocess

        settings = load_settings()
        if not settings.ffmpeg or not base.ffprobe_path(settings.ffmpeg):
            self.skipTest("ffmpeg/ffprobe tidak tersedia")
        encoders = subprocess.run([settings.ffmpeg, "-hide_banner", "-encoders"],
                                  capture_output=True, text=True).stdout
        if "libx265" in encoders:
            encoder, ext, codec = "libx265", ".mp4", "hevc"
        elif "libvpx-vp9" in encoders:
            encoder, ext, codec = "libvpx-vp9", ".webm", "vp9"
        else:
            self.skipTest("tidak ada encoder non-H.264 untuk diuji")

        with tempfile.TemporaryDirectory() as tmp:
            h264 = Path(tmp) / "h264.mp4"
            if not self._make(settings.ffmpeg, h264, "libx264"):
                self.skipTest("tidak bisa membuat video uji")
            self.assertEqual(base.probe_video_codec(h264, settings.ffmpeg), "h264")
            # video H.264 tidak perlu diubah
            self.assertEqual(base.ensure_h264(h264, settings.ffmpeg), h264)

            other = Path(tmp) / f"other{ext}"
            if not self._make(settings.ffmpeg, other, encoder):
                self.skipTest(f"encoder {encoder} tidak berjalan")
            self.assertEqual(base.probe_video_codec(other, settings.ffmpeg), codec)
            converted = base.ensure_h264(other, settings.ffmpeg)
            self.assertNotEqual(converted, other)
            self.assertEqual(base.probe_video_codec(converted, settings.ffmpeg), "h264")
            self.assertFalse(other.exists(), "file non-H.264 asli harus dihapus")

    def test_normalize_videos_skips_audio_and_respects_flag(self):
        from dataclasses import replace

        settings = load_settings()
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "a.mp3"
            audio.write_bytes(b"x" * 10)
            item = base.MediaItem(path=audio, kind="audio")
            same = asyncio.run(base.normalize_videos([item], settings))
            self.assertEqual(same, [item])

            off = replace(settings, transcode_h264=False)
            self.assertEqual(asyncio.run(base.normalize_videos([item], off)), [item])


class TestResolveTarget(unittest.TestCase):
    def test_youtube_search_when_not_url(self):
        self.assertEqual(
            handlers._resolve_target("lofi hip hop", "youtube", True),
            ("ytsearch1:lofi hip hop", "youtube"),
        )

    def test_rejects_plain_text_for_tiktok(self):
        self.assertEqual(handlers._resolve_target("halo semua", "tiktok", False), (None, None))

    def test_rejects_unsupported_link(self):
        self.assertEqual(handlers._resolve_target("https://vimeo.com/12345", None, False), (None, None))

    def test_accepts_supported_link(self):
        url, platform = handlers._resolve_target("cek ini https://youtu.be/abcd1234", None, False)
        self.assertEqual(url, "https://youtu.be/abcd1234")
        self.assertEqual(platform, "youtube")


class TestTelegramSend(unittest.TestCase):
    """Uji pengiriman media memakai bot tiruan (tanpa jaringan)."""

    class StubBot:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        async def send_chat_action(self, **kwargs):
            self.calls.append(("send_chat_action", kwargs))

        async def send_video(self, **kwargs):
            kwargs["_content_len"] = len(kwargs["video"].read() or b"")
            self.calls.append(("send_video", kwargs))

        async def send_audio(self, **kwargs):
            self.calls.append(("send_audio", kwargs))

        async def send_photo(self, **kwargs):
            self.calls.append(("send_photo", kwargs))

        async def send_document(self, **kwargs):
            self.calls.append(("send_document", kwargs))

        def named(self, name: str) -> list[dict]:
            return [kw for n, kw in self.calls if n == name]

    def _settings(self, limit_mb: int = 49):
        from dataclasses import replace

        return replace(load_settings(), max_upload_bytes=limit_mb * 1024 * 1024)

    def test_sends_each_kind_with_right_method(self):
        from appbot.telegram_io import send_result

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {
                "video": root / "v.mp4",
                "audio": root / "a.mp3",
                "photo": root / "p.jpg",
            }
            for path in files.values():
                path.write_bytes(b"data" * 100)
            result = base.DownloadResult(
                platform="youtube",
                source_url="https://youtu.be/x",
                title="Judul <b>uji</b>",
                uploader="Kanal",
                duration=90,
                items=[
                    base.MediaItem(path=files["video"], kind="video", title="Judul <b>uji</b>", duration=90),
                    base.MediaItem(path=files["audio"], kind="audio", title="Audio"),
                    base.MediaItem(path=files["photo"], kind="photo", title="Foto"),
                ],
            )
            bot = self.StubBot()
            notes = asyncio.run(send_result(bot, 1, result, self._settings()))
            self.assertEqual(notes, [])
            self.assertEqual(len(bot.named("send_video")), 1)
            self.assertEqual(len(bot.named("send_audio")), 1)
            self.assertEqual(len(bot.named("send_photo")), 1)
            video_kwargs = bot.named("send_video")[0]
            self.assertTrue(video_kwargs["supports_streaming"])
            self.assertEqual(video_kwargs["filename"], "v.mp4")
            self.assertIn("Judul &lt;b&gt;uji&lt;/b&gt;", video_kwargs["caption"])
            self.assertNotIn("<b>uji</b>", video_kwargs["caption"])
            # file lokal dihapus setelah dikirim
            for path in files.values():
                self.assertFalse(path.exists(), f"{path.name} seharusnya sudah dihapus")

    def test_skips_file_over_limit(self):
        from appbot.telegram_io import send_result

        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp) / "big.mp4"
            big.write_bytes(b"x" * (2 * 1024 * 1024))
            result = base.DownloadResult(
                platform="tiktok", source_url="u",
                items=[base.MediaItem(path=big, kind="video", title="Besar")],
            )
            bot = self.StubBot()
            notes = asyncio.run(send_result(bot, 1, result, self._settings(limit_mb=1)))
            self.assertEqual(bot.named("send_video"), [])
            self.assertTrue(notes and "melebihi batas upload" in notes[0])
            self.assertFalse(big.exists(), "file yang dilewati karena kebesaran harus dihapus")

    def test_deletes_file_when_both_send_attempts_fail(self):
        from appbot.telegram_io import send_result

        class BrokenBot(self.StubBot):
            async def send_video(self, **kwargs):
                raise RuntimeError("upload gagal")

            async def send_document(self, **kwargs):
                raise RuntimeError("fallback gagal")

        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            video.write_bytes(b"y" * 4096)
            result = base.DownloadResult(
                platform="youtube", source_url="u",
                items=[base.MediaItem(path=video, kind="video", title="Video")],
            )
            notes = asyncio.run(send_result(BrokenBot(), 1, result, self._settings()))
            self.assertTrue(notes and "Gagal mengirim" in notes[0])
            self.assertFalse(video.exists(), "file harus dihapus walau pengiriman gagal total")

    def test_cleanup_items_safety_net(self):
        from appbot.telegram_io import cleanup_items

        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / f"f{i}.mp4" for i in range(3)]
            for path in paths:
                path.write_bytes(b"z")
            items = [base.MediaItem(path=p, kind="video") for p in paths]
            self.assertEqual(cleanup_items(items), 3)
            self.assertTrue(all(not p.exists() for p in paths))
            # idempoten: aman dipanggil dua kali / dengan item kosong
            self.assertEqual(cleanup_items(items), 3)
            self.assertEqual(cleanup_items([]), 0)
            self.assertEqual(cleanup_items(None), 0)

    def test_photo_send_falls_back_to_document(self):
        from appbot.telegram_io import send_result

        class FailingPhotoBot(self.StubBot):
            async def send_photo(self, **kwargs):
                raise RuntimeError("photo gagal")

        with tempfile.TemporaryDirectory() as tmp:
            photo = Path(tmp) / "p.jpg"
            photo.write_bytes(b"z" * 2048)
            result = base.DownloadResult(
                platform="tiktok", source_url="u",
                items=[base.MediaItem(path=photo, kind="photo", title="Slide")],
            )
            bot = FailingPhotoBot()
            notes = asyncio.run(send_result(bot, 1, result, self._settings()))
            self.assertEqual(notes, [])
            self.assertEqual(len(bot.named("send_document")), 1)
            self.assertFalse(photo.exists())


class TestYoutubeGuards(unittest.TestCase):
    def test_rejects_live_stream(self):
        settings = load_settings()
        with self.assertRaises(base.DownloadError) as ctx:
            youtube.guard_metadata({"is_live": True, "duration": 100}, settings)
        self.assertIn("Siaran langsung", str(ctx.exception))

    def test_rejects_too_long_video(self):
        from dataclasses import replace

        settings = replace(load_settings(), max_duration_seconds=600)
        with self.assertRaises(base.DownloadError) as ctx:
            youtube.guard_metadata({"duration": 3600}, settings)
        self.assertIn("melebihi batas", str(ctx.exception))

    def test_allows_normal_video_and_no_duration(self):
        settings = load_settings()
        youtube.guard_metadata({"duration": 120}, settings)
        youtube.guard_metadata({"duration": None}, settings)
        youtube.guard_metadata(None, settings)


class TestBotProfilePush(unittest.TestCase):
    """Uji pendorongan menu perintah ke Telegram memakai bot tiruan."""

    class StubBot:
        def __init__(self, fail: tuple[str, ...] = ()):
            self.calls: list[tuple] = []
            self.fail = set(fail)

        async def set_my_commands(self, commands, scope=None):
            if "set_my_commands" in self.fail:
                raise TelegramError("boom")
            self.calls.append(("set_my_commands", tuple(commands), type(scope).__name__))

        async def set_my_short_description(self, short_description):
            if "short" in self.fail:
                raise TelegramError("short terlalu panjang")
            self.calls.append(("set_my_short_description", short_description))

        async def set_my_description(self, description):
            if "description" in self.fail:
                raise TelegramError("deskripsi tidak didukung")
            self.calls.append(("set_my_description", description))

        async def set_chat_menu_button(self, menu_button=None):
            self.calls.append(("set_chat_menu_button", menu_button))

        def named(self, name: str) -> list[tuple]:
            return [c for c in self.calls if c[0] == name]

    def test_menu_catalog_matches_telegram_rules(self):
        import re

        names = [n for n, _ in bot_profile.MENU_COMMANDS]
        self.assertEqual(len(names), len(set(names)), "nama perintah harus unik")
        for name, description in bot_profile.MENU_COMMANDS:
            self.assertRegex(name, r"^[a-z0-9_]{1,32}$")
            self.assertTrue(3 <= len(description) <= 256, f"deskripsi /{name} di luar 3-256 karakter")
        self.assertLessEqual(len(bot_profile.SHORT_DESCRIPTION), 120)
        self.assertLessEqual(len(bot_profile.DESCRIPTION), 512)

    def test_alias_targets_exist_in_menu(self):
        names = {n for n, _ in bot_profile.MENU_COMMANDS}
        for alias, target in bot_profile.ALIASES.items():
            self.assertIn(target, names, f"alias /{alias} menunjuk perintah tak ada")
            self.assertNotIn(alias, names, f"alias /{alias} tidak boleh tampil di menu")

    def _app(self):
        import bot as bot_module

        s = load_settings()
        s = s.__class__(**{**s.__dict__, "bot_token": "123456:TESTTOKEN"})
        return bot_module.build_application(s)

    def test_menu_built_from_registered_handlers(self):
        app = self._app()
        menu = bot_profile.collect_menu(app)
        names = [c.command for c in menu]
        self.assertEqual(names, [n for n, _ in bot_profile.MENU_COMMANDS])
        # alias terdaftar di handler tetapi tidak muncul di menu
        registered = bot_profile.registered_commands(app)
        for alias in ("youtube", "ytdl", "tiktok", "ttdl", "instagram", "igdl"):
            self.assertIn(alias, registered)
            self.assertNotIn(alias, names)
        self.assertEqual(bot_profile.unexpected_commands(app), set())

    def test_collect_menu_without_application_returns_all(self):
        self.assertEqual(len(bot_profile.collect_menu(None)), len(bot_profile.MENU_COMMANDS))

    def test_push_sets_all_scopes_descriptions_and_menu_button(self):
        app = self._app()
        bot = self.StubBot()
        report = asyncio.run(bot_profile.push_bot_profile(bot, app))

        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.menu_size, len(bot_profile.MENU_COMMANDS))
        scopes = [c[2] for c in bot.named("set_my_commands")]
        self.assertEqual(
            scopes,
            ["BotCommandScopeDefault", "BotCommandScopeAllPrivateChats",
             "BotCommandScopeAllGroupChats", "BotCommandScopeAllChatAdministrators"],
        )
        for _, commands, _ in bot.named("set_my_commands"):
            self.assertEqual([c.command for c in commands], [n for n, _ in bot_profile.MENU_COMMANDS])
            self.assertTrue(all(isinstance(c, BotCommand) for c in commands))
        self.assertEqual(len(bot.named("set_my_short_description")), 1)
        self.assertEqual(len(bot.named("set_my_description")), 1)
        buttons = bot.named("set_chat_menu_button")
        self.assertEqual(len(buttons), 1)
        self.assertIsInstance(buttons[0][1], MenuButtonCommands)
        self.assertEqual(len(report.applied), 7)

    def test_push_survives_partial_failure(self):
        app = self._app()
        bot = self.StubBot(fail=("description", "short"))
        report = asyncio.run(bot_profile.push_bot_profile(bot, app))
        self.assertFalse(report.ok)
        self.assertEqual(len(bot.named("set_my_commands")), 4, "kegagalan lain tidak boleh membatalkan ini")
        self.assertEqual(len(bot.named("set_chat_menu_button")), 1)
        self.assertEqual(len(report.failed), 2)
        self.assertIn("gagal", report.summary())

    def test_push_reports_unknown_commands(self):
        class FakeHandler:
            commands = ("rahasia",)

        class FakeApp:
            handlers = {0: [FakeHandler()]}

        report = asyncio.run(bot_profile.push_bot_profile(self.StubBot(), FakeApp()))
        self.assertEqual(report.unexpected, {"rahasia"})
        self.assertIn("rahasia", report.summary())
        self.assertEqual(report.menu_size, 0)


class TestJobRegistry(unittest.TestCase):
    def test_add_get_by_user_and_remove(self):
        registry = jobs.JobRegistry()
        a = registry.add(user_id=1, chat_id=1, url="u1", platform="youtube")
        b = registry.add(user_id=2, chat_id=2, url="u2", platform="tiktok")
        self.assertEqual(len(registry), 2)
        self.assertIs(registry.get(a.id), a)
        self.assertEqual([j.id for j in registry.by_user(1)], [a.id])
        self.assertEqual([j.id for j in registry.by_user(2)], [b.id])
        self.assertEqual(registry.by_user(3), [])
        registry.remove(a.id)
        self.assertIsNone(registry.get(a.id))
        self.assertEqual(len(registry), 1)

    def test_cancel_pending_job_counts_as_cancelled(self):
        registry = jobs.JobRegistry()
        record = registry.add(user_id=1, chat_id=1, url="u", platform="youtube")
        self.assertTrue(registry.cancel(record))
        self.assertTrue(record.cancelled)
        self.assertIsNone(registry.get(record.id))
        self.assertFalse(registry.cancel(record), "tidak boleh dihitung dua kali")

    def test_cancel_running_task_stops_it_and_cleans_job_dir(self):
        async def scenario():
            registry = jobs.JobRegistry()
            started = asyncio.Event()
            holder: dict = {}

            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)

                async def work():
                    with base.JobDir(root, "job") as job:
                        (job.path / "part.bin").write_bytes(b"x" * 2048)
                        holder["dir"] = job.path
                        started.set()
                        await asyncio.sleep(30)

                record = registry.add(user_id=7, chat_id=7, url="u", platform="youtube")
                record.task = asyncio.create_task(work())
                await started.wait()
                self.assertTrue(holder["dir"].exists())

                stopped = await registry.cancel_many([record])
                self.assertEqual(stopped, 1)
                self.assertTrue(record.task.cancelled())
                self.assertFalse(holder["dir"].exists(), "JobDir harus dibersihkan saat dibatalkan")
                self.assertEqual(len(registry), 0)

        asyncio.run(scenario())

    def test_cancel_many_with_mixed_jobs(self):
        async def scenario():
            registry = jobs.JobRegistry()
            running = asyncio.create_task(asyncio.sleep(30))
            pending = registry.add(user_id=1, chat_id=1, url="a", platform="tiktok")
            active = registry.add(user_id=1, chat_id=1, url="b", platform="youtube")
            active.task = running
            other = registry.add(user_id=2, chat_id=2, url="c", platform="instagram")

            stopped = await registry.cancel_many([pending, active])
            self.assertEqual(stopped, 2)
            self.assertTrue(running.cancelled())
            self.assertEqual([j.id for j in registry.all()], [other.id])

        asyncio.run(scenario())


class TestPurgeDirectory(unittest.TestCase):
    def test_removes_files_and_subdirs_but_keeps_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dl"
            (root / "youtube").mkdir(parents=True)
            (root / "youtube" / "a.mp4").write_bytes(b"x" * 1024)
            (root / "b.mp3").write_bytes(b"y" * 512)

            files, total = base.purge_directory(root)
            self.assertEqual(files, 2)
            self.assertEqual(total, 1536)
            self.assertEqual(list(root.iterdir()), [])
            self.assertTrue(root.is_dir(), "direktori akar harus tetap ada")

    def test_missing_root_is_safe(self):
        self.assertEqual(base.purge_directory(Path("/tidak-ada-dir-xyz")), (0, 0))


class TestCleanupCommand(unittest.TestCase):
    """Uji /cleanup memakai update/context tiruan."""

    class Msg:
        def __init__(self):
            self.texts: list[str] = []
            self.message_id = 99

        async def reply_text(self, text, **kwargs):
            self.texts.append(text)
            return self

    class Upd:
        def __init__(self, user_id: int):
            self.effective_user = SimpleNamespace(id=user_id, full_name="U", username="u")
            self.effective_chat = SimpleNamespace(id=user_id, type="private")
            self.effective_message = TestCleanupCommand.Msg()

    class Ctx:
        def __init__(self, application, args=()):
            self.application = application
            self.args = list(args)
            self.bot = None

    def setUp(self):
        from dataclasses import replace

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.settings = replace(
            load_settings(),
            temp_dir=root / "tmp",
            download_dir=root / "dl",
            admin_user_ids=frozenset({999}),
            allowed_user_ids=frozenset(),
        )
        self.settings.temp_dir.mkdir()
        self.settings.download_dir.mkdir()
        self.root = root
        self.registry = jobs.JobRegistry()
        self.app = SimpleNamespace(
            bot_data={"settings": self.settings, "jobs": self.registry},
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_files(self):
        (self.root / "tmp" / "job-1").mkdir()
        (self.root / "tmp" / "job-1" / "part.bin").write_bytes(b"a" * 100)
        (self.root / "dl" / "youtube").mkdir()
        (self.root / "dl" / "youtube" / "v.mp4").write_bytes(b"b" * 200)
        return 2

    def test_cleanup_cancels_own_jobs_and_purges(self):
        job = self.registry.add(user_id=111, chat_id=111, url="u", platform="youtube")
        self._seed_files()
        update = self.Upd(111)
        asyncio.run(handlers.cmd_cleanup(update, self.Ctx(self.app)))

        text = update.effective_message.texts[-1]
        self.assertIn("Pembersihan selesai", text)
        self.assertIn("<b>1</b>", text)          # satu job dibatalkan
        self.assertIn("File dihapus    : 2", text)
        self.assertEqual(len(self.registry), 0)
        self.assertTrue(job.cancelled)
        self.assertEqual(list((self.root / "dl").iterdir()), [])
        # purge menghapus isi tmp, lalu heartbeat ditulis ulang agar healthcheck tetap hijau
        self.assertEqual(
            {p.name for p in (self.root / "tmp").iterdir()}, {"heartbeat"},
            "hanya heartbeat yang boleh tersisa di tmp/",
        )
        self.assertTrue(health.is_healthy(self.settings))

    def test_cleanup_does_not_touch_other_users_jobs(self):
        mine = self.registry.add(user_id=111, chat_id=111, url="a", platform="youtube")
        theirs = self.registry.add(user_id=222, chat_id=222, url="b", platform="tiktok")
        self._seed_files()

        update = self.Upd(111)
        asyncio.run(handlers.cmd_cleanup(update, self.Ctx(self.app)))

        self.assertTrue(mine.cancelled)
        self.assertFalse(theirs.cancelled)
        self.assertEqual([j.id for j in self.registry.all()], [theirs.id])
        text = update.effective_message.texts[-1]
        self.assertIn("pengguna lain", text)          # mode purge sebagian
        # file baru (< 1 menit) tidak dihapus agar tidak mengganggu unduhan lain
        self.assertTrue((self.root / "dl" / "youtube" / "v.mp4").exists())

    def test_admin_can_cleanup_everything(self):
        a = self.registry.add(user_id=111, chat_id=111, url="a", platform="youtube")
        b = self.registry.add(user_id=222, chat_id=222, url="b", platform="tiktok")
        self._seed_files()

        update = self.Upd(999)  # admin
        asyncio.run(handlers.cmd_cleanup(update, self.Ctx(self.app, args=["all"])))

        self.assertTrue(a.cancelled and b.cancelled)
        self.assertEqual(len(self.registry), 0)
        self.assertEqual(list((self.root / "dl").iterdir()), [])
        self.assertIn("semua pengguna", update.effective_message.texts[-1])

    def test_non_admin_cannot_cleanup_all(self):
        job = self.registry.add(user_id=111, chat_id=111, url="a", platform="youtube")
        update = self.Upd(111)
        asyncio.run(handlers.cmd_cleanup(update, self.Ctx(self.app, args=["all"])))

        self.assertIn("hanya untuk admin", update.effective_message.texts[-1])
        self.assertFalse(job.cancelled)
        self.assertEqual(len(self.registry), 1)

    def test_cleanup_reports_nothing_to_do(self):
        update = self.Upd(111)
        asyncio.run(handlers.cmd_cleanup(update, self.Ctx(self.app)))
        self.assertIn("Tidak ada yang perlu dibersihkan", update.effective_message.texts[-1])

    def test_cleanup_reports_old_files_in_partial_mode(self):
        self.registry.add(user_id=222, chat_id=222, url="b", platform="tiktok")
        stale = self.root / "dl" / "youtube"
        stale.mkdir()
        old = stale / "lama.mp4"
        old.write_bytes(b"c" * 50)
        os.utime(old, (0, 0))

        update = self.Upd(111)
        asyncio.run(handlers.cmd_cleanup(update, self.Ctx(self.app)))
        self.assertFalse(old.exists(), "file lama tetap dibersihkan dalam mode sebagian")


class TestAdminConfig(unittest.TestCase):
    def test_admin_falls_back_to_whitelist(self):
        from dataclasses import replace

        base_settings = load_settings()
        whitelist_only = replace(base_settings, allowed_user_ids=frozenset({5}),
                                 admin_user_ids=frozenset())
        self.assertTrue(whitelist_only.is_admin(5))
        self.assertFalse(whitelist_only.is_admin(6))

        explicit = replace(base_settings, allowed_user_ids=frozenset({5}),
                           admin_user_ids=frozenset({77}))
        self.assertFalse(explicit.is_admin(5), "ADMIN_USER_IDS eksplisit harus menang")
        self.assertTrue(explicit.is_admin(77))
        self.assertFalse(explicit.is_admin(None))

    def test_public_bot_has_no_admin(self):
        from dataclasses import replace

        public = replace(load_settings(), allowed_user_ids=frozenset(),
                         admin_user_ids=frozenset())
        self.assertFalse(public.is_admin(1))


class TestCancellationSignal(unittest.TestCase):
    def test_progress_hook_raises_when_cancel_requested(self):
        import yt_dlp

        p = base.Progress()
        p.hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2})
        self.assertEqual(p.percent, 50.0)
        p.request_cancel()
        self.assertTrue(p.cancelled)
        with self.assertRaises(yt_dlp.utils.DownloadCancelled):
            p.hook({"status": "downloading", "downloaded_bytes": 2, "total_bytes": 2})

    def test_registry_cancel_sets_progress_flag(self):
        registry = jobs.JobRegistry()
        progress = base.Progress()
        record = registry.add(user_id=1, chat_id=1, url="u", platform="youtube",
                              progress=progress)
        self.assertFalse(progress.cancelled)
        registry.cancel(record)
        self.assertTrue(progress.cancelled, "thread yt-dlp harus menerima sinyal batal")

    def test_cancel_error_maps_to_friendly_message(self):
        import yt_dlp

        msg = base.map_ytdlp_error(yt_dlp.utils.DownloadCancelled("x"), "YouTube")
        self.assertEqual(msg, "Unduhan dibatalkan.")


class TestHealth(unittest.TestCase):
    def _settings(self, root: Path):
        from dataclasses import replace

        return replace(load_settings(), temp_dir=root / "tmp", download_dir=root / "dl")

    def test_missing_heartbeat_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp))
            self.assertIsNone(health.age(settings))
            self.assertFalse(health.is_healthy(settings))
            self.assertIn("UNHEALTHY", health.describe(settings))

    def test_touch_creates_heartbeat_and_is_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp))
            path = health.touch(settings)
            self.assertTrue(path.is_file())
            self.assertLess(health.age(settings), 5)
            self.assertTrue(health.is_healthy(settings))
            self.assertIn("OK", health.describe(settings))

    def test_stale_heartbeat_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp))
            path = health.touch(settings)
            os.utime(path, (0, 0))
            self.assertGreater(health.age(settings), 1000)
            self.assertFalse(health.is_healthy(settings))
            # ambang lebih besar dari umur file -> dianggap sehat
            self.assertTrue(health.is_healthy(settings, max_age=health.age(settings) + 1))

    def test_cli_health_exit_codes(self):
        import bot as bot_module

        with tempfile.TemporaryDirectory() as tmp:
            env_backup = dict(os.environ)
            os.environ["DATA_DIR"] = tmp
            os.environ.pop("BOT_TOKEN", None)
            try:
                s = load_settings()
                self.assertEqual(s.temp_dir, Path(tmp) / "tmp")

                argv = sys.argv
                sys.argv = ["bot.py", "--health"]
                try:
                    self.assertEqual(bot_module.main(), 1)  # belum ada heartbeat
                    health.touch(load_settings())
                    self.assertEqual(bot_module.main(), 0)  # sehat
                finally:
                    sys.argv = argv
            finally:
                os.environ.clear()
                os.environ.update(env_backup)

    def test_data_dir_env_controls_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_backup = dict(os.environ)
            os.environ["DATA_DIR"] = tmp
            try:
                s = load_settings()
                self.assertEqual(s.download_dir, Path(tmp) / "downloads")
                self.assertEqual(s.temp_dir, Path(tmp) / "tmp")
            finally:
                os.environ.clear()
                os.environ.update(env_backup)


class TestDeployFiles(unittest.TestCase):
    """Validasi berkas deployment supaya tidak ada salah ketik yang lolos."""

    ROOT = Path(__file__).resolve().parent.parent

    def test_compose_files_are_valid_yaml(self):
        try:
            import yaml
        except ImportError:  # pragma: no cover - pyyaml hanya untuk pengujian
            self.skipTest("pyyaml tidak terpasang")

        main = yaml.safe_load((self.ROOT / "docker-compose.yml").read_text())
        self.assertIn("bot", main["services"])
        bot = main["services"]["bot"]
        self.assertEqual(bot["restart"], "unless-stopped")
        self.assertEqual(bot["env_file"], [".env"])
        self.assertIn("appbot_data:/app/data", bot["volumes"])
        self.assertIn("appbot_data", main["volumes"])

        override = yaml.safe_load(
            (self.ROOT / "deploy/docker-compose.localapi.yml").read_text()
        )
        self.assertIn("bot-api", override["services"])
        self.assertEqual(
            override["services"]["bot"]["environment"]["LOCAL_API_URL"],
            "http://bot-api:8081",
        )

    def test_dockerfile_uses_non_root_and_healthcheck(self):
        text = (self.ROOT / "Dockerfile").read_text()
        self.assertIn("USER appbot", text)
        self.assertIn("HEALTHCHECK", text)
        self.assertIn('"bot.py", "--health"', text)
        self.assertIn("ffmpeg", text)

    def test_systemd_unit_has_required_directives(self):
        text = (self.ROOT / "deploy/appbot-telegram@.service").read_text()
        for line in (
            "[Unit]", "[Service]", "[Install]",
            "User=%i", "Restart=always", "EnvironmentFile=",
            "ExecStart=", "WantedBy=multi-user.target", "TimeoutStopSec=",
        ):
            self.assertIn(line, text)

    def test_quadlet_container_unit(self):
        text = (self.ROOT / "deploy/appbot-telegram.container").read_text()
        for line in ("[Container]", "Image=localhost/appbot-telegram", "EnvironmentFile=",
                     "Volume=", "UserNS=keep-id", "WantedBy=default.target"):
            self.assertIn(line, text)

    def test_pm2_ecosystem_structure(self):
        text = (self.ROOT / "deploy/ecosystem.config.js").read_text()
        for token in ("module.exports", "apps:", "name: \"appbot-telegram\"",
                      "interpreter: \"none\"", "autorestart: true", "kill_timeout"):
            self.assertIn(token, text)

    def test_run_sh_commands_are_wired(self):
        text = (self.ROOT / "run.sh").read_text()
        for cmd in ("check)", "push)", "health)", "sysinfo)", "test)", "update)"):
            self.assertIn(cmd, text)
        self.assertIn("python3 -m venv", text, "harus ada fallback tanpa uv")


class TestDownloadErrorPath(unittest.TestCase):
    def test_unknown_platform_raises(self):
        from appbot.downloaders import download_media

        with self.assertRaises(base.DownloadError):
            asyncio.run(download_media(None, "https://example.com/video/1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
