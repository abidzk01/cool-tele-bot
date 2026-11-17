# 🤖 Telegram Bot: System Info + YouTube / TikTok / Instagram Downloader

A Telegram bot built on `python-telegram-bot` v22 and `yt-dlp`, with five main capabilities:

| Feature | Command | Notes |
|---|---|---|
| 🖥 System info | `/sysinfo` | CPU, RAM, swap, disk, uptime, Python/yt-dlp/ffmpeg versions, bot process memory, refresh button |
| ▶️ YouTube | `/yt <link\|keyword>` | MP4 up to a configurable resolution; `/yt audio <link>` for MP3; supports Shorts and playlists (max 5 items) |
| 🎵 TikTok | `/tt <link>` | **No watermark** (tikwm API, yt-dlp fallback); photo slideshows and original audio supported |
| 📸 Instagram | `/ig <link>` | Reels, photo/video posts, and carousels (max 10 media) |
| 🧹 Cleanup | `/cleanup` | Cancel running downloads and purge leftover files; `/cleanup all` (admin) covers every user |

Sending a bare link (without a command) also works — the platform is detected automatically.

The bot replies in Indonesian; code, comments, and documentation are in English.
See [AGENTS.md](AGENTS.md) for the project conventions.

---

## 1. Project layout

```
app_bot_telegram/
├── bot.py                      # entry point + CLI (--check, --sysinfo, --push-commands, --health)
├── config.py                   # configuration from .env
├── run.sh                      # launcher: run/check/push/health/test/update
├── requirements.txt
├── requirements-dev.txt        # extra dependencies for tests (PyYAML)
├── .env.example
├── Dockerfile                  # non-root image + HEALTHCHECK
├── docker-compose.yml          # standard compose (data volume + log rotation)
├── AGENTS.md                   # conventions: commits, language, architecture
├── .githooks/commit-msg        # enforces Conventional Commits + English
├── appbot/
│   ├── handlers.py             # Telegram commands, queue, live status, /cleanup
│   ├── bot_profile.py          # pushes the "/" menu and bot description
│   ├── jobs.py                 # registry of running downloads (cancellable)
│   ├── health.py               # heartbeat used by the container healthcheck
│   ├── telegram_io.py          # media upload + caption building
│   ├── system_info.py          # system metrics collection and formatting
│   ├── logging_setup.py
│   └── downloaders/
│       ├── __init__.py         # platform detection + dispatch
│       ├── base.py             # yt-dlp wrapper, progress, ffmpeg, cleanup
│       ├── youtube.py          # resolution ladder, automatic compression
│       ├── tiktok.py           # tikwm (no watermark) + yt-dlp fallback
│       └── instagram.py        # reels/posts/carousels
├── scripts/
│   ├── livetest.py             # real download test, no Telegram token needed
│   └── check_commit_message.py # commit message linter
├── tests/
│   ├── test_bot.py             # unit + integration tests
│   └── test_project_rules.py   # language, commit and secret rules
├── docs/DEPLOY.md              # deployment guide: run.sh, systemd, PM2, Docker, Podman
└── deploy/
    ├── appbot-telegram@.service      # systemd unit (per-user template)
    ├── ecosystem.config.js          # PM2
    ├── appbot-telegram.container    # Podman Quadlet
    └── docker-compose.localapi.yml  # local Bot API override (~2 GB uploads)
```

## 2. Quick start

Requires Python 3.10+ and ffmpeg.

```bash
cd app_bot_telegram
./run.sh            # creates .venv, installs dependencies, starts the bot
```

Manual steps:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env      # then set BOT_TOKEN
.venv/bin/python bot.py
```

Get a token from [@BotFather](https://t.me/BotFather). Validate without starting the bot:

```bash
.venv/bin/python bot.py --check     # token + dependencies
.venv/bin/python bot.py --sysinfo   # print system info to the terminal
```

## 3. `.env` configuration

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | – | **Required.** Token from BotFather |
| `ALLOWED_USER_IDS` | empty | Empty = public. Set `123,456` to whitelist |
| `ADMIN_USER_IDS` | empty | Allowed to run `/cleanup all`. Empty = `ALLOWED_USER_IDS` acts as admin |
| `MAX_UPLOAD_MB` | `49` | Upload size limit. The official Bot API allows 50 MB |
| `MAX_VIDEO_HEIGHT` | `1080` | Maximum YouTube resolution |
| `MAX_DURATION_SECONDS` | `3600` | Reject longer videos (0 = unlimited) |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Parallel downloads |
| `TEMP_TTL_MINUTES` | `60` | Lifetime of temporary files (janitor runs every 15 minutes) |
| `DATA_DIR` | project folder | Root for `downloads/` and `tmp/` (in containers: `/app/data`) |
| `FFMPEG_LOCATION` | auto | Path to the ffmpeg binary |
| `AUTO_COMPRESS` | `1` | Re-encode with ffmpeg when the result exceeds the upload limit |
| `TRANSCODE_TO_H264` | `1` | Transcode HEVC/VP9/AV1 to H.264 so Telegram plays it inline |
| `TIKTOK_USE_TIKWM` | `1` | `0` = use yt-dlp only for TikTok |
| `COOKIES_YOUTUBE` / `COOKIES_INSTAGRAM` / `COOKIES_TIKTOK` | – | Path to Netscape cookie files |
| `COOKIES_FROM_BROWSER` | – | e.g. `chrome`, `firefox` (used when no cookie file is set) |
| `PROXY_URL` | – | Proxy for yt-dlp |
| `LOCAL_API_URL` | – | Local Bot API server, e.g. `http://127.0.0.1:8081` (raise `MAX_UPLOAD_MB` to ~2000) |

## 4. Automatic command menu ("/")

On startup, `appbot/bot_profile.py` pushes the following to Telegram, so typing `/` shows
the command list with descriptions:

- **Command list** for four scopes: default, private chats, groups, and group admins — so the
  menu appears in DMs as well as in groups.
- **Short description** (shown in chat search) and **bot description** (profile page).
- **Menu button** set to `MenuButtonCommands`, so the menu button always opens the commands.

The menu content is derived from the `CommandHandler`s actually registered in
`build_application()`, so it cannot drift from the code. Aliases (`/youtube`, `/tiktok`,
`/igdl`, …) keep working when typed but are deliberately hidden to keep the menu short.

Push it again without running the bot (e.g. after editing descriptions):

```bash
./run.sh push                    # or: .venv/bin/python bot.py --push-commands
```

That command also reads `get_my_commands()` back from Telegram and prints what is really
stored, so you can confirm the push succeeded.

## 5. How each platform is handled

**TikTok without watermark** — the primary path uses the public `tikwm.com` API, which
returns `hdplay`/`play` without a watermark; if that fails the bot falls back to yt-dlp with
explicit format selection (formats containing `play_addr` are preferred, `download_addr` is
watermarked and avoided). Photo slideshows are sent as a set of images and the original audio
is downloaded separately.

**YouTube** — picks the best format that fits the upload limit using a `filesize` + `height`
selector, walks down a resolution ladder (e.g. 1080 → 720 → 480) when needed, and compresses
the result when it is still too large.

**Instagram** — yt-dlp handles reels, posts, and carousels. For private content or when
Instagram rate-limits the request, the bot returns a message explaining how to install cookies.

**Player compatibility** — HEVC/VP9/AV1 output (common on TikTok and Instagram) is
transcoded to H.264 + AAC with `+faststart`, so videos play inline in Telegram instead of
arriving as documents.

### File cleanup

Downloaded files are **not kept** — they are deleted as soon as they are processed:

- **Send succeeds** → the file is removed once Telegram accepts the media.
- **Send fails** (including the document fallback failing) → the file is still removed and the
  user gets an error message.
- **Skipped for exceeding the upload limit** → the file is removed so it cannot pile up.
- **Bot dies mid-process** → the handler has a `finally` block that cleans every item, and a
  janitor sweeps leftovers in `downloads/` and `tmp/` every 15 minutes for files older than
  `TEMP_TTL_MINUTES`.

Set `TEMP_TTL_MINUTES=0` to disable the janitor (orphaned files then stay on disk).

### The `/cleanup` command

Cancels running downloads **and** removes leftover files:

- `/cleanup` — cancels your own downloads. If no other user has a download in flight, the whole
  `tmp/` and `downloads/` content is purged. Otherwise only files older than one minute are
  removed, so in-flight uploads of other users are not disturbed.
- `/cleanup all` — admins only (`ADMIN_USER_IDS`, or `ALLOWED_USER_IDS` when
  `ADMIN_USER_IDS` is empty): cancels **all** downloads and purges every folder.

How cancellation works: each download is registered in `appbot/jobs.py` as an `asyncio.Task`.
On cancel, the bot signals the yt-dlp progress hook, which raises `DownloadCancelled`, so the
worker thread stops too — not just the async task. The `JobDir` working directory is then
cleaned up automatically.

Sample reply (the bot answers in Indonesian):

```
🧹 Pembersihan selesai
• Job dibatalkan  : 1 (milikmu)
• Job masih aktif : 0
• File dihapus    : 3 (62.85 MB)
   – tmp: 2, downloads: 1
• Durasi          : 0.1s
```

### Cookies (optional)

1. Install the "Get cookies.txt LOCALLY" browser extension, log in to the site, export cookies.
2. Save as `cookies/youtube.txt`, `cookies/instagram.txt`, or `cookies/tiktok.txt`.
3. Point `.env` at it, e.g. `COOKIES_INSTAGRAM=cookies/instagram.txt`, then restart the bot.

Without cookies, public content usually still works. Cookies are needed when YouTube demands
anti-bot verification from the server IP, or for Instagram content that requires a login.

## 6. Deployment

The full guide for all five methods lives in **[docs/DEPLOY.md](docs/DEPLOY.md)**.

| Method | Quick start |
|---|---|
| `run.sh` | `./run.sh` (foreground; wrap in tmux/nohup if needed) |
| systemd | `sudo cp deploy/appbot-telegram@.service /etc/systemd/system/ && sudo systemctl enable --now appbot-telegram@$USER` |
| PM2 | `pm2 start deploy/ecosystem.config.js && pm2 save && pm2 startup` |
| Docker | `docker compose up -d --build`, then `docker compose ps` (status `healthy`) |
| Podman | `podman build -t localhost/appbot-telegram:latest .` + `deploy/appbot-telegram.container` (Quadlet) |

Every method uses the same `.env`. One healthcheck command works everywhere:

```bash
.venv/bin/python bot.py --health     # exit 0 healthy, 1 unhealthy
./run.sh health
```

Inside containers the check is baked into the image
(`HEALTHCHECK CMD python bot.py --health`) and inspects the heartbeat written by the job queue
every 60 seconds — not merely whether the process exists.

For uploads above 50 MB (up to ~2 GB), use a local Bot API server:
`docker compose -f docker-compose.yml -f deploy/docker-compose.localapi.yml up -d --build`.

## 7. Tests

```bash
.venv/bin/python -m unittest discover -s tests -v      # full suite, no network needed
.venv/bin/python scripts/livetest.py \
    --video-url "https://www.youtube.com/watch?v=aqz-KE-bpKQ" \
    --tiktok-url "https://www.tiktok.com/@user/video/123" \
    --instagram-url "https://www.instagram.com/reel/ABC/"
```

`livetest.py` runs the real download pipeline (no Telegram token required) and verifies the
resulting file with `ffprobe`.

`tests/test_project_rules.py` enforces the repository conventions: documentation must be in
English, commit messages must follow Conventional Commits, user-facing Indonesian strings must
stay intact, and no token patterns may be committed.

## 8. Contributing

Read [AGENTS.md](AGENTS.md) first. In short:

- Code, comments, docstrings, docs, and commit messages are **English**; bot replies stay Indonesian.
- Commit messages follow **Conventional Commits** (`feat(scope): subject`, imperative, ≤72 chars).
- Enable the local hook once per clone: `git config core.hooksPath .githooks`.
- Run `./run.sh test` before committing.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `Sign in to confirm you're not a bot` | Set `COOKIES_YOUTUBE`, or run from a residential IP |
| Video arrives as a document and will not play | Make sure `ffmpeg` is detected (`/sysinfo`) and `TRANSCODE_TO_H264=1` |
| Instagram rejects the request without login | Set `COOKIES_INSTAGRAM` |
| Files over 50 MB are not delivered | Raise `MAX_UPLOAD_MB` with a local Bot API server, or lower `MAX_VIDEO_HEIGHT` |
| TikTok fails through the API | Set `TIKTOK_USE_TIKWM=0` to use the yt-dlp path only |
| Bot does not respond | Check `journalctl`/logs; validate the token with `bot.py --check` |
| Downloads feel slow after finishing | The bot is transcoding to H.264 (expensive on ARM/weak CPUs). Set `TRANSCODE_TO_H264=0` if your Telegram client already plays HEVC/VP9 |
| `/sysinfo` reports ffmpeg not found | Set `FFMPEG_LOCATION=/path/to/ffmpeg` in `.env` |
| The `/` menu is empty | Run `./run.sh push`; if it still looks empty, restart the Telegram app (clients cache the menu) |