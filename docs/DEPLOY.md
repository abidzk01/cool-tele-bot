# 🚀 Deployment Guide

The bot can be deployed in five ways. All of them use the same code and `.env`; only the
process/isolation layer differs.

| Method | Best for | Auto-restart | Healthcheck | Isolation |
|---|---|---|---|---|
| [`run.sh`](#a-runsh-simplest) | trying it out, debugging, a personal server | ❌ (manual/tmux) | manual | – |
| [systemd](#b-systemd-recommended-for-vps) | Linux VPS without Docker | ✅ | `systemctl status` | light |
| [PM2](#c-pm2-nodejs-process-manager) | already running PM2 for other apps | ✅ | `pm2 status` | light |
| [Docker](#d-docker--docker-compose) | Docker hosts, reproducible builds | ✅ | ✅ healthcheck | full |
| [Podman](#e-podman-rootless) | rootless, daemonless | ✅ | ✅ healthcheck | full |

---

## 0. Common prerequisites

1. A **bot token** from [@BotFather](https://t.me/BotFather).
2. **Python 3.10+** (only for the non-container methods) and **ffmpeg**.
3. Copy the configuration:

```bash
cd /opt/app_bot_telegram        # adjust to your checkout location
cp .env.example .env
nano .env                       # set BOT_TOKEN (required), ALLOWED_USER_IDS (optional)
```

4. Create the runtime and optional cookie folders:

```bash
mkdir -p downloads tmp cookies logs
```

5. Sanity check before starting a service:

```bash
./run.sh check     # valid token? ffmpeg detected?
```

`ffmpeg` is mandatory: without it video+audio cannot be merged, automatic compression is
skipped, and H.264 transcoding is bypassed (HEVC/VP9 videos may then arrive as documents in
Telegram).

---

## A. `run.sh` (simplest)

`run.sh` creates the virtualenv, installs dependencies, copies `.env`, and runs the bot in the
foreground. Good for testing or for running inside `tmux`/`screen`.

```bash
./run.sh            # start the bot
./run.sh check      # validate token + dependencies
./run.sh push       # push the "/" menu to Telegram
./run.sh health     # exit 0 when healthy
./run.sh sysinfo    # print CPU/RAM/disk info
./run.sh test       # run the full suite
./run.sh update     # upgrade yt-dlp
```

The script uses `uv` when available and falls back to `python3 -m venv` + `pip`, so it also
works on minimal servers.

**Keeping it running without systemd:**

```bash
# option 1: tmux
tmux new -s appbot './run.sh'
# detach: Ctrl-b then d, reattach: tmux attach -t appbot

# option 2: nohup
nohup ./run.sh > logs/out.log 2>&1 &
echo $! > appbot.pid
# stop: kill "$(cat appbot.pid)"
```

This does not restart after a reboot. For production pick B–E.

---

## B. systemd (recommended for VPS)

A template unit ships in `deploy/appbot-telegram@.service` (the `@` is required because the
unit uses `User=%i`).

```bash
# 1. Put the project in place
sudo mkdir -p /opt/app_bot_telegram
sudo rsync -a --exclude .venv --exclude downloads --exclude tmp ./ /opt/app_bot_telegram/
sudo chown -R "$USER":"$USER" /opt/app_bot_telegram

# 2. Prepare the venv and dependencies as the owning user
cd /opt/app_bot_telegram
./run.sh check          # creates .venv, installs dependencies, validates the token

# 3. Install the unit (template, runs as $USER)
sudo cp deploy/appbot-telegram@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now appbot-telegram@"$USER"

# 4. Observe
systemctl status appbot-telegram@"$USER"
journalctl -u appbot-telegram@"$USER" -f
```

Day-to-day commands:

```bash
sudo systemctl restart appbot-telegram@"$USER"    # after editing .env
sudo systemctl stop appbot-telegram@"$USER"
journalctl -u appbot-telegram@"$USER" --since "1 hour ago"
```

Unit notes:
- `EnvironmentFile` reads `/opt/app_bot_telegram/.env` (not `.env.example`).
- `KillSignal=SIGINT` + `TimeoutStopSec=30` give running downloads time to stop cleanly.
- `ProtectSystem=full` + `NoNewPrivileges` restrict writes to the project folder.
- To run as another user, change the instance name:
  `systemctl enable --now appbot-telegram@botuser`.

---

## C. PM2 (Node.js process manager)

PM2 can supervise a non-Node process with `interpreter: "none"`.

```bash
# 1. Install Node + PM2 (once)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g pm2

# 2. Prepare the project and dependencies
cd /opt/app_bot_telegram
./run.sh check          # creates .venv + .env
mkdir -p logs

# 3. Start through the ecosystem file
pm2 start deploy/ecosystem.config.js
pm2 status
pm2 logs appbot-telegram --lines 100

# 4. Autostart after reboot
pm2 save
pm2 startup systemd -u "$USER" --hp "$HOME"    # run the command it prints
```

Day-to-day commands:

```bash
pm2 restart appbot-telegram       # after editing .env
pm2 reload appbot-telegram        # graceful restart
pm2 stop appbot-telegram
pm2 delete appbot-telegram
pm2 monit                          # live CPU/RAM
```

Notes:
- PM2 does **not** read `.env`; the bot loads it itself through `python-dotenv`. Make sure
  `.env` exists inside `APPBOT_ROOT`.
- Different project location? `APPBOT_ROOT=/srv/appbot pm2 start deploy/ecosystem.config.js`.
- Default logs: `/opt/app_bot_telegram/logs/{out,error}.log`.
- `kill_timeout: 20000` gives downloads time to stop before the process is killed.

---

## D. Docker / Docker Compose

### D.1 Compose (primary path)

```bash
cd /opt/app_bot_telegram
cp .env.example .env && nano .env     # set BOT_TOKEN
mkdir -p cookies

docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100
```

What `docker-compose.yml` sets up:

| Part | Value |
|---|---|
| image | `appbot-telegram:latest` (built locally from `Dockerfile`) |
| user | non-root `appbot` (uid 1000) inside the image |
| volumes | `appbot_data` → `/app/data` (downloads + tmp + heartbeat), `./cookies` → `/app/cookies:ro` |
| restart | `unless-stopped` |
| healthcheck | from the Dockerfile: `python bot.py --health` |
| logging | json-file, 10 MB × 3 rotation |
| stop | `stop_grace_period: 30s` |

Day-to-day commands:

```bash
docker compose restart
docker compose logs -f
docker compose exec bot python bot.py --check     # validate the token inside the container
docker compose exec bot python bot.py --health
docker compose exec bot sh                        # open a shell
docker compose down                               # stop (data stays in the volume)
docker compose up -d --build                      # rebuild after code changes
```

`DATA_DIR=/app/data` keeps temporary files on the volume instead of the image layer.
Downloaded files are still deleted automatically once they are sent to Telegram.

### D.2 Without Compose

```bash
docker build -t appbot-telegram:latest .
docker run -d --name appbot-telegram \
  --restart unless-stopped \
  --env-file .env \
  -e DATA_DIR=/app/data \
  -v appbot_data:/app/data \
  -v "$PWD/cookies:/app/cookies:ro" \
  --log-opt max-size=10m --log-opt max-file=3 \
  appbot-telegram:latest

docker logs -f appbot-telegram
docker inspect --format '{{.State.Health.Status}}' appbot-telegram
```

### D.3 Healthcheck

```bash
docker compose ps        # STATUS column: healthy / unhealthy / starting
docker inspect --format '{{json .State.Health}}' appbot-telegram | head -c 400
```

`unhealthy` means the job queue has not written a heartbeat for over 5 minutes: the bot is
stuck, the token was revoked, or the event loop is blocked. Check the logs, then
`docker compose restart`.

---

## E. Podman (rootless)

### E.1 Build and run

```bash
cd /opt/app_bot_telegram
podman build -t localhost/appbot-telegram:latest .

mkdir -p downloads tmp cookies
chmod 700 cookies

podman run -d --name appbot-telegram \
  --restart=unless-stopped \
  --env-file .env \
  -e DATA_DIR=/app/data \
  --userns=keep-id:uid=1000,gid=1000 \
  -v "$PWD/downloads:/app/data/downloads:Z" \
  -v "$PWD/tmp:/app/data/tmp:Z" \
  -v "$PWD/cookies:/app/cookies:ro,Z" \
  localhost/appbot-telegram:latest

podman logs -f appbot-telegram
podman healthcheck run appbot-telegram
```

`--userns=keep-id:uid=1000,gid=1000` maps the host user to uid 1000 inside the container, so
folders owned by that user remain writable by the non-root process in the image. The `:Z`
label forces an SELinux relabel (required on Fedora/RHEL).

### E.2 podman-compose

```bash
pip install --user podman-compose     # or: sudo dnf install podman-compose

podman-compose up -d --build
podman-compose ps
podman-compose logs -f
podman-compose down
```

`docker-compose.yml` works as-is because Podman 4+ implements the Compose spec, but named
volumes are managed by Podman instead of Docker.

### E.3 Quadlet (systemd user, modern approach)

`deploy/appbot-telegram.container` is a ready-to-use Quadlet unit (Podman ≥ 4.4):

```bash
mkdir -p ~/.config/containers/systemd
cp deploy/appbot-telegram.container ~/.config/containers/systemd/

# make sure the image and folders exist (see E.1)
systemctl --user daemon-reload
systemctl --user enable --now appbot-telegram.service

systemctl --user status appbot-telegram
journalctl --user -u appbot-telegram -f
```

To keep it running after logout and across reboots:

```bash
sudo loginctl enable-linger "$USER"
```

Updating the image, then restarting:

```bash
podman build -t localhost/appbot-telegram:latest .
systemctl --user restart appbot-telegram
```

---

## F. Optional: local Bot API server (uploads up to ~2 GB)

The official Bot API caps bot uploads at 50 MB. With your own Bot API server the limit rises
to roughly 2 GB (`MAX_UPLOAD_MB=1900`).

1. Get `api_id` and `api_hash` from <https://my.telegram.org/apps>.
2. Add them to `.env`:

```ini
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
MAX_UPLOAD_MB=1900
```

3. Start with the Compose override:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.localapi.yml up -d --build
docker compose ps         # a bot-api service appears
```

The bot automatically uses `LOCAL_API_URL=http://bot-api:8081` and waits until the server is
ready (the restart policy covers the first attempts).

---

## G. Updates and maintenance

| Task | Command |
|---|---|
| Update yt-dlp (sites change often) | `./run.sh update`, then restart |
| Update the code and restart | `git pull`, then restart with your method |
| Rebuild the image | `docker compose up -d --build` / `podman build -t …` + restart |
| Check disk usage | `du -sh downloads tmp /var/lib/docker/volumes/appbot*` |
| Clean up from inside the bot | `/cleanup` or `/cleanup all` (admin) |
| Inspect running jobs | `/cleanup` reports the number of active jobs |

Schedule a weekly yt-dlp upgrade; most "failed to process link" errors disappear after it.

---

## H. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `Invalid token` on start | `.env` not filled in or token wrong. Run `./run.sh check` |
| `BOT_TOKEN is not set` | `EnvironmentFile`/`env_file` cannot find `.env`; check the path |
| Container `unhealthy` but logs look fine | Heartbeat older than 5 minutes: bot stuck or job queue dead. Restart, check `bot.py --health` |
| `/sysinfo` says ffmpeg not found | On the host: `apt install ffmpeg`. In the image ffmpeg is present; do not override `FFMPEG_LOCATION` |
| `Permission denied` writing `downloads/` | Docker: use named volumes. Podman: add `--userns=keep-id` and `:Z` |
| Video arrives as a document | `TRANSCODE_TO_H264=1` and ffmpeg available; some clients cannot play HEVC/VP9 |
| `pm2 status` keeps restarting | Check `logs/error.log`; usually `BOT_TOKEN` is empty or `.venv` is missing |
| systemd reports `status=2` | Token/network failure; `journalctl -u appbot-telegram@$USER -n 50` |
| Uploads over 50 MB are rejected | Enable the local Bot API server (section F) or lower `MAX_VIDEO_HEIGHT` |
| Queue is long | Raise `MAX_CONCURRENT_DOWNLOADS`, but watch CPU/RAM |
| The `/` menu does not appear | `./run.sh push`, then restart the Telegram app |

---

## I. Moving to another server

Carry the code (or image), `.env`, and `cookies/`. The `downloads/` and `tmp/` folders are not
needed — they only contain temporary files that are deleted automatically.

```bash
# on the old server
tar czf appbot-config.tgz .env cookies/
# on the new server
tar xzf appbot-config.tgz -C /opt/app_bot_telegram
./run.sh check
```