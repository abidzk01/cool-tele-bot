# 🚀 Panduan Deployment

Bot ini bisa dijalankan dengan lima cara. Semua memakai kode dan `.env` yang sama,
hanya lapisan proses/isolasi yang berbeda.

| Metode | Cocok untuk | Auto-restart | Healthcheck | Isolasi |
|---|---|---|---|---|
| [`run.sh`](#a-runsh-paling-sederhana) | mencoba, debug, server pribadi | ❌ (manual/tmux) | manual | – |
| [systemd](#b-systemd-direkomendasikan-untuk-vps) | VPS Linux tanpa Docker | ✅ | `systemctl status` | ringan |
| [PM2](#c-pm2-nodejs-process-manager) | sudah pakai PM2 untuk aplikasi lain | ✅ | `pm2 status` | ringan |
| [Docker](#d-docker--docker-compose) | server dengan Docker, ingin reproduksibel | ✅ | ✅ healthcheck | penuh |
| [Podman](#e-podman-rootless) | rootless, tanpa daemon | ✅ | ✅ healthcheck | penuh |

---

## 0. Prasyarat umum

1. **Token bot** dari [@BotFather](https://t.me/BotFather).
2. **Python 3.10+** (hanya untuk metode tanpa container) dan **ffmpeg**.
3. Salin konfigurasi:

```bash
cd /opt/app_bot_telegram        # sesuaikan lokasi project
cp .env.example .env
nano .env                       # isi BOT_TOKEN (wajib), ALLOWED_USER_IDS (opsional)
```

4. Siapkan folder runtime dan cookies opsional:

```bash
mkdir -p downloads tmp cookies logs
```

5. Verifikasi cepat sebelum menyalakan service:

```bash
./run.sh check     # token valid? ffmpeg terdeteksi?
```

`ffmpeg` adalah keharusan: tanpa itu video+audio tidak bisa digabung, kompresi
otomatis mati, dan transcode H.264 dilewati (video HEVC/VP9 bisa muncul sebagai
dokumen di Telegram).

---

## A. `run.sh` (paling sederhana)

`run.sh` membuat virtualenv, memasang dependency, menyalin `.env`, lalu menjalankan
bot di foreground. Cocok untuk uji coba atau dijalankan di dalam `tmux`/`screen`.

```bash
./run.sh            # jalankan bot
./run.sh check      # validasi token + dependency
./run.sh push       # dorong menu "/" ke Telegram
./run.sh health     # exit 0 bila bot sehat
./run.sh sysinfo    # info CPU/RAM/disk ke terminal
./run.sh test       # 65+ tes
./run.sh update     # perbarui yt-dlp
```

Skrip ini otomatis memakai `uv` bila tersedia, dan jatuh ke `python3 -m venv` + `pip`
bila tidak — jadi tetap jalan di server minimal.

**Menjalankan terus-menerus tanpa systemd:**

```bash
# opsi 1: tmux
tmux new -s appbot './run.sh'
# lepas: Ctrl-b lalu d, kembali: tmux attach -t appbot

# opsi 2: nohup
nohup ./run.sh > logs/out.log 2>&1 &
echo $! > appbot.pid
# hentikan: kill "$(cat appbot.pid)"
```

Cara ini tidak auto-restart setelah reboot. Untuk produksi pilih B–E.

---

## B. systemd (direkomendasikan untuk VPS)

Unit template sudah disediakan di `deploy/appbot-telegram@.service` (nama dengan `@`
karena memakai `User=%i`).

```bash
# 1. Letakkan project
sudo mkdir -p /opt/app_bot_telegram
sudo rsync -a --exclude .venv --exclude downloads --exclude tmp ./ /opt/app_bot_telegram/
sudo chown -R "$USER":"$USER" /opt/app_bot_telegram

# 2. Siapkan venv & dependency sebagai user pemilik
cd /opt/app_bot_telegram
./run.sh check          # membuat .venv, memasang dependency, memvalidasi token

# 3. Pasang unit (template, dijalankan sebagai user $USER)
sudo cp deploy/appbot-telegram@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now appbot-telegram@"$USER"

# 4. Pantau
systemctl status appbot-telegram@"$USER"
journalctl -u appbot-telegram@"$USER" -f
```

Perintah harian:

```bash
sudo systemctl restart appbot-telegram@"$USER"    # setelah ubah .env
sudo systemctl stop appbot-telegram@"$USER"
journalctl -u appbot-telegram@"$USER" --since "1 hour ago"
```

Catatan unit:
- `EnvironmentFile` membaca `/opt/app_bot_telegram/.env` (bukan `.env.example`).
- `KillSignal=SIGINT` + `TimeoutStopSec=30` memberi waktu unduhan berhenti rapi.
- `ProtectSystem=full` + `NoNewPrivileges` membatasi akses tulis ke folder project.
- Ganti `User=%i` dengan user lain cukup lewat nama instance:
  `systemctl enable --now appbot-telegram@botuser`.

---

## C. PM2 (Node.js process manager)

PM2 bisa mengelola proses non-Node dengan `interpreter: "none"`.

```bash
# 1. Pasang Node + PM2 (sekali saja)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g pm2

# 2. Siapkan project & dependency
cd /opt/app_bot_telegram
./run.sh check          # membuat .venv + .env
mkdir -p logs

# 3. Jalankan lewat ecosystem file
pm2 start deploy/ecosystem.config.js
pm2 status
pm2 logs appbot-telegram --lines 100

# 4. Autostart setelah reboot
pm2 save
pm2 startup systemd -u "$USER" --hp "$HOME"    # jalankan perintah yang dicetak
```

Perintah harian:

```bash
pm2 restart appbot-telegram       # setelah ubah .env
pm2 reload appbot-telegram        # restart tanpa downtime (graceful)
pm2 stop appbot-telegram
pm2 delete appbot-telegram
pm2 monit                          # CPU/RAM realtime
```

Catatan:
- PM2 **tidak** membaca `.env`; bot memuatnya sendiri via `python-dotenv`. Pastikan
  `.env` ada di dalam `APPBOT_ROOT`.
- Lokasi project berbeda? `APPBOT_ROOT=/srv/appbot pm2 start deploy/ecosystem.config.js`.
- Log default: `/opt/app_bot_telegram/logs/{out,error}.log`.
- `kill_timeout: 20000` memberi waktu unduhan berhenti sebelum proses dimatikan.

---

## D. Docker / Docker Compose

### D.1 Compose (cara utama)

```bash
cd /opt/app_bot_telegram
cp .env.example .env && nano .env     # isi BOT_TOKEN
mkdir -p cookies

docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100
```

Yang disiapkan `docker-compose.yml`:

| Bagian | Nilai |
|---|---|
| image | `appbot-telegram:latest` (build lokal dari `Dockerfile`) |
| user | non-root `appbot` (uid 1000) di dalam image |
| volume | `appbot_data` → `/app/data` (downloads + tmp + heartbeat), `./cookies` → `/app/cookies:ro` |
| restart | `unless-stopped` |
| healthcheck | dari Dockerfile: `python bot.py --health` |
| log | json-file, rotasi 10 MB × 3 |
| stop | `stop_grace_period: 30s` |

Perintah harian:

```bash
docker compose restart
docker compose logs -f
docker compose exec bot python bot.py --check     # cek token dari dalam container
docker compose exec bot python bot.py --health
docker compose exec bot sh                        # masuk ke shell
docker compose down                               # hentikan (data tetap di volume)
docker compose up -d --build                      # rebuild setelah kode berubah
```

`DATA_DIR=/app/data` membuat file sementara berada di volume, bukan di layer image.
File unduhan tetap dihapus otomatis setelah dikirim ke Telegram.

### D.2 Tanpa Compose

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
docker compose ps        # kolom STATUS: healthy / unhealthy / starting
docker inspect --format '{{json .State.Health}}' appbot-telegram | head -c 400
```

Status `unhealthy` berarti job queue berhenti menulis heartbeat >5 menit: bot macet,
token dicabut, atau event loop tertahan. Lihat log lalu `docker compose restart`.

---

## E. Podman (rootless)

### E.1 Build & jalankan

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

`--userns=keep-id:uid=1000,gid=1000` memetakan user host ke uid 1000 di container,
sehingga folder milik user bisa ditulis oleh proses non-root di dalam image.
Label `:Z` memaksa relabel SELinux (wajib di Fedora/RHEL).

### E.2 podman-compose

```bash
pip install --user podman-compose     # atau: sudo dnf install podman-compose

podman-compose up -d --build
podman-compose ps
podman-compose logs -f
podman-compose down
```

`docker-compose.yml` bisa dipakai apa adanya karena Podman 4+ mendukung Compose spec,
tetapi volume bernama akan dikelola Podman (bukan Docker).

### E.3 Quadlet (systemd user, cara modern)

`deploy/appbot-telegram.container` adalah unit Quadlet siap pakai (Podman ≥ 4.4):

```bash
mkdir -p ~/.config/containers/systemd
cp deploy/appbot-telegram.container ~/.config/containers/systemd/

# pastikan image & folder sudah ada (lihat E.1)
systemctl --user daemon-reload
systemctl --user enable --now appbot-telegram.service

systemctl --user status appbot-telegram
journalctl --user -u appbot-telegram -f
```

Agar tetap jalan setelah logout dan saat boot:

```bash
sudo loginctl enable-linger "$USER"
```

Update image lalu restart:

```bash
podman build -t localhost/appbot-telegram:latest .
systemctl --user restart appbot-telegram
```

---

## F. Opsional: Bot API lokal (upload sampai ~2 GB)

Bot API resmi membatasi unggahan bot ke 50 MB. Dengan server Bot API sendiri, batasnya
naik ke ~2 GB (`MAX_UPLOAD_MB=1900`).

1. Ambil `api_id` & `api_hash` di <https://my.telegram.org/apps>.
2. Tambahkan ke `.env`:

```ini
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
MAX_UPLOAD_MB=1900
```

3. Jalankan dengan override Compose:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.localapi.yml up -d --build
docker compose ps         # ada layanan bot-api
```

Bot otomatis memakai `LOCAL_API_URL=http://bot-api:8081` dan menunggu hingga server siap
(restart policy menangani percobaan awal).

---

## G. Update & perawatan

| Tugas | Perintah |
|---|---|
| Perbarui yt-dlp (penting: situs sering berubah) | `./run.sh update` lalu restart |
| Perbarui kode & restart | `git pull` lalu restart sesuai metode |
| Rebuild image | `docker compose up -d --build` / `podman build -t …` + restart |
| Lihat pemakaian disk | `du -sh downloads tmp /var/lib/docker/volumes/appbot*` |
| Bersihkan dari dalam bot | `/cleanup` atau `/cleanup all` (admin) |
| Lihat job aktif | `/cleanup` menampilkan jumlah job aktif |

Jadwalkan pembaruan yt-dlp mingguan; banyak kegagalan "gagal memproses link" hilang
setelah yt-dlp diperbarui.

---

## H. Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| `Token tidak valid` saat start | `.env` belum diisi / token salah. `./run.sh check` |
| `BOT_TOKEN belum diisi` | `EnvironmentFile`/`env_file` tidak menemukan `.env`; cek path |
| Container `unhealthy` tapi log normal | Heartbeat >5 menit: bot macet atau job queue mati. Restart, cek `bot.py --health` |
| `ffmpeg: TIDAK DITEMUKAN` di `/sysinfo` | Di host: `apt install ffmpeg`. Di image: ffmpeg sudah ada, jangan override `FFMPEG_LOCATION` |
| `Permission denied` menulis `downloads/` | Docker: pakai volume bernama. Podman: tambahkan `--userns=keep-id` dan `:Z` |
| Video muncul sebagai dokumen | `TRANSCODE_TO_H264=1` dan ffmpeg tersedia; HEVC/VP9 tidak diputar sebagian klien |
| `pm2 status` restart terus | Lihat `logs/error.log`; biasanya `BOT_TOKEN` kosong atau `.venv` tidak ada |
| systemd `status=2` | Token/jaringan gagal; `journalctl -u appbot-telegram@$USER -n 50` |
| Upload >50 MB ditolak | Aktifkan Bot API lokal (bagian F) atau turunkan `MAX_VIDEO_HEIGHT` |
| Antrean panjang | Naikkan `MAX_CONCURRENT_DOWNLOADS`, tapi perhatikan CPU/RAM |
| Menu `/` tidak muncul | `./run.sh push`, lalu restart aplikasi Telegram |

---

## I. Pindah server

Yang perlu dibawa: kode (atau image), `.env`, dan `cookies/`. Folder `downloads/` dan
`tmp/` tidak perlu — isinya file sementara yang dihapus otomatis.

```bash
# di server lama
tar czf appbot-config.tgz .env cookies/
# di server baru
tar xzf appbot-config.tgz -C /opt/app_bot_telegram
./run.sh check
```