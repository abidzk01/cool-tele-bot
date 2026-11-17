#!/usr/bin/env python3
"""Real-network download test (internet required). Proves the pipeline works end-to-end.

Examples:
    .venv/bin/python scripts/livetest.py --video-url "https://www.youtube.com/watch?v=..."
    .venv/bin/python scripts/livetest.py --tiktok-url "https://www.tiktok.com/@user/video/123"
    .venv/bin/python scripts/livetest.py --instagram-url "https://www.instagram.com/reel/xxx/"
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from appbot.downloaders import Progress, cleanup_old_jobs, download_media, human_bytes
from appbot.logging_setup import setup_logging
from config import load_settings


def probe(path: Path, ffmpeg: str | None) -> str:
    ffprobe = str(Path(ffmpeg).with_name("ffprobe")) if ffmpeg else None
    if not ffprobe or not Path(ffprobe).is_file():
        return "ffprobe not available"
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "format=duration,size,format_name:stream=codec_type,codec_name,width,height",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True,
    )
    return " ".join((out.stdout or out.stderr).split())[:220]


async def run_case(name: str, url: str, settings, audio_only: bool = False) -> bool:
    print(f"\n=== {name} ===")
    print(f"URL   : {url}")
    progress = Progress()
    try:
        result = await download_media(settings, url, audio_only=audio_only, progress=progress)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ FAILED: {type(exc).__name__}: {exc}")
        return False

    print(f"✅ title   : {result.title[:90]}")
    print(f"   channel : {result.uploader}")
    print(f"   duration: {result.duration if result.duration else '-'}")
    print(f"   notes   : {result.notes}")
    ok = True
    for item in result.items:
        exists = item.path.is_file()
        ok = ok and exists and item.filesize > 1024
        print(f"   - {item.kind:5s} {item.path.name[:60]:60s} {human_bytes(item.filesize)} "
              f"{'OK' if exists else 'MISSING'}")
        if exists:
            print(f"     probe: {probe(item.path, settings.ffmpeg)}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-url", action="append", default=[])
    parser.add_argument("--tiktok-url", action="append", default=[])
    parser.add_argument("--instagram-url", action="append", default=[])
    parser.add_argument("--audio", action="store_true", help="download audio only (YouTube)")
    args = parser.parse_args()

    setup_logging(logging.INFO)
    settings = load_settings()
    settings.download_dir.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    cleanup_old_jobs(settings.temp_dir, 0)

    cases: list[tuple[str, str, bool]] = []
    cases += [(f"YouTube[{Path(u).name or i}]", u, args.audio) for i, u in enumerate(args.video_url)]
    cases += [(f"TikTok[{i}]", u, False) for i, u in enumerate(args.tiktok_url)]
    cases += [(f"Instagram[{i}]", u, False) for i, u in enumerate(args.instagram_url)]

    if not cases:
        print("No URLs provided. See --help.")
        return 1

    results = []
    for name, url, audio in cases:
        results.append(asyncio.run(run_case(name, url, settings, audio)))

    print("\n=== SUMMARY ===")
    for (name, url, _), ok in zip(cases, results):
        print(f"{'✅' if ok else '❌'} {name}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
