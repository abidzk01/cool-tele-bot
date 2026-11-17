# 🤖 Bot Telegram: Info Sistem + Downloader YouTube / TikTok / Instagram

Bot Telegram berbasis `python-telegram-bot` v22 dan `yt-dlp`, dengan 4 kemampuan utama:

| Fitur | Perintah | Catatan |
|---|---|---|
| 🖥 Info sistem | `/sysinfo` | CPU, RAM, swap, disk, uptime, versi Python/yt-dlp/ffmpeg, memori proses bot, tombol refresh |
| ▶️ YouTube | `/yt <link\|kata kunci>` | Video mp4 (maks resolusi bisa diatur), `/yt audio <link>` untuk MP3, dukung Shorts & playlist (maks 5 item) |
| 🎵 TikTok | `/tt <link>` | **Tanpa watermark** (API tikwm, fallback yt-dlp), dukung slideshow foto + audio asli |
| 📸 Instagram | `/ig <link>` | Reel, post foto/video, dan carousel (maks 10 media) |
| 🧹 Bersihkan | `/cleanup` | Batalkan unduhan yang sedang berjalan + hapus file sisa; `/cleanup all` (admin) membersihkan semua pengguna |

Kirim link biasa ke chat (tanpa perintah) juga otomatis dikenali platformnya.

---

## 1. Struktur proyek

```
app_bot_telegram/
├── bot.py                      # entry point + CLI (--check, --sysinfo, --push-commands, --health)
├── config.py                   # konfigurasi dari .env
├── run.sh                      # launcher: run/check/push/health/test/update
├── requirements.txt
├── .env.example
├── Dockerfile                  # image non-root + HEALTHCHECK
├── docker-compose.yml          # compose standar (volume data + log rotasi)
├── appbot/
│   ├── handlers.py             # perintah Telegram, antrean, status live, /cleanup
│   ├── bot_profile.py          # dorong menu "/" & deskripsi bot ke Telegram
│   ├── jobs.py                 # registry unduhan berjalan (bisa dibatalkan)
│   ├── health.py               # heartbeat untuk healthcheck container
│   ├── telegram_io.py          # pengiriman video/audio/foto + caption
│   ├── system_info.py          # pengumpulan & format info sistem
│   ├── logging_setup.py
│   └── downloaders/
│       ├── __init__.py         # deteksi platform + dispatch
│       ├── base.py             # yt-dlp wrapper, progress, ffmpeg, cleanup
│       ├── youtube.py          # ladder resolusi, kompresi otomatis
│       ├── tiktok.py           # tikwm (no watermark) + fallback yt-dlp
│       └── instagram.py        # reels/posts/carousel
├── scripts/livetest.py         # uji unduhan nyata tanpa token
├── tests/test_bot.py           # 65+ tes unit & integrasi
├── docs/DEPLOY.md              # panduan deploy: run.sh, systemd, PM2, Docker, Podman
└── deploy/
    ├── appbot-telegram@.service      # systemd unit (template per user)
    ├── ecosystem.config.js          # PM2
    ├── appbot-telegram.container    # Quadlet Podman
    └── docker-compose.localapi.yml  # override Bot API lokal (upload ~2 GB)
```

## 2. Instalasi cepat

Butuh Python 3.10+ dan ffmpeg.

```bash
cd app_bot_telegram
./run.sh            # otomatis: buat .venv, pasang dependency, jalankan bot
```

Langkah manual:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env      # lalu isi BOT_TOKEN
.venv/bin/python bot.py
```

Ambil token dari [@BotFather](https://t.me/BotFather). Validasi tanpa menjalankan bot:

```bash
.venv/bin/python bot.py --check     # cek token + dependency
.venv/bin/python bot.py --sysinfo   # cetak info sistem ke terminal
```

## 3. Konfigurasi `.env`

| Variabel | Default | Keterangan |
|---|---|---|
| `BOT_TOKEN` | – | **Wajib.** Token dari BotFather |
| `ALLOWED_USER_IDS` | kosong | Kosong = publik. Isi `123,456` untuk whitelist |
| `ADMIN_USER_IDS` | kosong | Boleh `/cleanup all`. Kosong = `ALLOWED_USER_IDS` dianggap admin |
| `MAX_UPLOAD_MB` | `49` | Batas ukuran kirim. Bot API resmi hanya 50 MB |
| `MAX_VIDEO_HEIGHT` | `1080` | Resolusi maksimum YouTube |
| `MAX_DURATION_SECONDS` | `3600` | Tolak video lebih panjang (0 = bebas) |
| `MAX_CONCURRENT_DOWNLOADS` | `2` | Unduhan paralel |
| `TEMP_TTL_MINUTES` | `60` | Umur file sementara (janitor tiap 15 menit) |
| `DATA_DIR` | folder project | Akar `downloads/` dan `tmp/` (di container: `/app/data`) |
| `FFMPEG_LOCATION` | auto | Path binary ffmpeg |
| `AUTO_COMPRESS` | `1` | Kompres via ffmpeg bila hasil melebihi batas |
| `TRANSCODE_TO_H264` | `1` | Transcode HEVC/VP9/AV1 → H.264 agar bisa diputar inline di Telegram |
| `TIKTOK_USE_TIKWM` | `1` | `0` = pakai yt-dlp saja untuk TikTok |
| `COOKIES_YOUTUBE` / `COOKIES_INSTAGRAM` / `COOKIES_TIKTOK` | – | Path file cookies Netscape |
| `COOKIES_FROM_BROWSER` | – | Mis. `chrome`, `firefox` (dipakai bila file cookies kosong) |
| `PROXY_URL` | – | Proxy untuk yt-dlp |
| `LOCAL_API_URL` | – | Bot API server lokal, mis. `http://127.0.0.1:8081` (naikkan `MAX_UPLOAD_MB` ke ~2000) |

## 4. Menu perintah otomatis ("/")

Saat bot start, `appbot/bot_profile.py` langsung mendorong konfigurasi berikut ke Telegram,
jadi begitu kamu mengetik `/` di chat, daftar perintah muncul beserta deskripsinya:

- **Daftar perintah** untuk 4 scope: default, chat pribadi, grup, dan admin grup — supaya menu
  tetap muncul baik di DM maupun di grup.
- **Deskripsi singkat** (tampil di kolom pencarian chat) dan **deskripsi bot** (halaman profil).
- **Menu button** diatur ke `MenuButtonCommands`, jadi tombol menu selalu membuka daftar perintah.

Isi menu diturunkan dari `CommandHandler` yang benar-benar terdaftar di `build_application()`,
jadi tidak mungkin melenceng dari kode. Nama alternatif (`/youtube`, `/tiktok`, `/igdl`, …) tetap
berfungsi bila diketik tetapi sengaja tidak ditampilkan agar menu tidak penuh.

Untuk mendorong ulang tanpa menjalankan bot (mis. setelah mengubah deskripsi):

```bash
./run.sh push                    # atau: .venv/bin/python bot.py --push-commands
```

Perintah ini juga membaca ulang `get_my_commands()` dari Telegram dan menampilkan daftar yang
benar-benar tersimpan, sehingga kamu bisa memastikan pendorongan berhasil.

## 5. Cara bot menangani tiap platform

**TikTok tanpa watermark** — jalur utama memakai API publik `tikwm.com` yang mengembalikan `hdplay`/`play` tanpa watermark, lalu fallback ke yt-dlp dengan pemilihan format eksplisit (format `play_addr` diprioritaskan, `download_addr` = berwatermark dihindari). Slideshow foto dikirim sebagai sekumpulan gambar, audio asli diunduh terpisah.

**YouTube** — memilih format terbaik yang muat batas upload lewat selektor `filesize` + `height`, mencoba tangga resolusi (mis. 1080 → 720 → 480) bila perlu, lalu mengompresi video ke ukuran target bila masih kelebihan.

**Instagram** — yt-dlp menangani reel/post/carousel. Untuk konten privat atau saat Instagram membatasi permintaan, bot mengembalikan pesan yang menjelaskan cara memasang cookies.

**Kompatibilitas pemutar** — hasil HEVC/VP9/AV1 (umum di TikTok & Instagram) otomatis ditranscode ke H.264 + AAC dengan `+faststart`, sehingga video diputar inline di Telegram alih-alih muncul sebagai dokumen.

### Pembersihan file

File hasil unduhan **tidak disimpan** — langsung dihapus setelah diproses:

- **Kirim sukses** → file dihapus setelah Telegram menerima media.
- **Kirim gagal** (termasuk fallback ke dokumen yang ikut gagal) → file tetap dihapus, user dapat pesan error.
- **Dilewati karena melebihi batas upload** → file ikut dihapus agar tidak menumpuk.
- **Bot mati/terputus di tengah proses** → handler punya `finally` yang membersihkan semua item, dan janitor menyapu sisa file di `downloads/` maupun `tmp/` setiap 15 menit untuk file yang lebih tua dari `TEMP_TTL_MINUTES`.

Set `TEMP_TTL_MINUTES=0` bila ingin mematikan janitor (file yatim tidak akan dibersihkan otomatis).

### Perintah `/cleanup`

Membatalkan unduhan yang sedang berjalan **dan** menghapus file sisa:

- `/cleanup` — membatalkan unduhan milikmu. Bila tidak ada unduhan pengguna lain yang
  berjalan, seluruh isi `tmp/` dan `downloads/` dibersihkan. Bila ada, hanya file berumur
  lebih dari 1 menit yang dihapus agar tidak mengganggu unduhan orang lain.
- `/cleanup all` — hanya untuk admin (`ADMIN_USER_IDS`, atau `ALLOWED_USER_IDS` bila
  `ADMIN_USER_IDS` kosong): membatalkan **semua** unduhan lalu membersihkan seluruh folder.

Cara kerja pembatalan: setiap unduhan didaftarkan di `appbot/jobs.py` sebagai `asyncio.Task`.
Saat dibatalkan, bot mengirim sinyal ke progress hook yt-dlp yang langsung melempar
`DownloadCancelled` — sehingga thread yang sedang mengunduh ikut berhenti, bukan hanya
task async-nya. Folder kerja `JobDir` lalu dibersihkan otomatis.

Balasan contoh:

```
🧹 Pembersihan selesai
• Job dibatalkan  : 1 (milikmu)
• Job masih aktif : 0
• File dihapus    : 3 (62.85 MB)
   – tmp: 2, downloads: 1
• Durasi          : 0.1s
```

### Cookies (opsional)

1. Pasang ekstensi "Get cookies.txt LOCALLY" di browser, login ke situsnya, ekspor cookies.
2. Simpan sebagai `cookies/youtube.txt`, `cookies/instagram.txt`, atau `cookies/tiktok.txt`.
3. Set di `.env`, mis. `COOKIES_INSTAGRAM=cookies/instagram.txt`, lalu restart bot.

Tanpa cookies, konten publik biasanya tetap bisa diunduh. Cookies diperlukan saat YouTube meminta verifikasi anti-bot dari IP server, atau untuk konten Instagram yang mengharuskan login.

## 6. Deployment

Panduan lengkap untuk lima metode ada di **[docs/DEPLOY.md](docs/DEPLOY.md)**.

| Metode | Mulai cepat |
|---|---|
| `run.sh` | `./run.sh` (foreground; bisa dibungkus tmux/nohup) |
| systemd | `sudo cp deploy/appbot-telegram@.service /etc/systemd/system/ && sudo systemctl enable --now appbot-telegram@$USER` |
| PM2 | `pm2 start deploy/ecosystem.config.js && pm2 save && pm2 startup` |
| Docker | `docker compose up -d --build` lalu `docker compose ps` (status `healthy`) |
| Podman | `podman build -t localhost/appbot-telegram:latest .` + `deploy/appbot-telegram.container` (Quadlet) |

Semua metode memakai `.env` yang sama. Perintah healthcheck seragam:

```bash
.venv/bin/python bot.py --health     # exit 0 sehat, 1 tidak
./run.sh health
```

Di container, healthcheck sudah tertanam di image (`HEALTHCHECK CMD python bot.py --health`)
dan menguji heartbeat yang ditulis job queue tiap 60 detik — bukan sekadar proses hidup.

Untuk upload >50 MB (sampai ~2 GB), pakai Bot API lokal:
`docker compose -f docker-compose.yml -f deploy/docker-compose.localapi.yml up -d --build`.

## 7. Tes

```bash
.venv/bin/python -m unittest discover -s tests -v      # 65+ tes, tanpa jaringan
.venv/bin/python scripts/livetest.py \
    --video-url "https://www.youtube.com/watch?v=aqz-KE-bpKQ" \
    --tiktok-url "https://www.tiktok.com/@user/video/123" \
    --instagram-url "https://www.instagram.com/reel/ABC/"
```

`livetest.py` menjalankan pipeline unduhan sungguhan (tanpa token Telegram) dan memverifikasi file hasil dengan `ffprobe`.

## 8. Troubleshooting

| Gejala | Solusi |
|---|---|
| `Sign in to confirm you're not a bot` | Set `COOKIES_YOUTUBE`, atau jalankan dari IP rumahan |
| Video muncul sebagai dokumen, tidak bisa diputar | Pastikan `ffmpeg` terdeteksi (`/sysinfo`) dan `TRANSCODE_TO_H264=1` |
| Instagram: "menolak permintaan tanpa login" | Set `COOKIES_INSTAGRAM` |
| File > 50 MB tidak terkirim | Naikkan `MAX_UPLOAD_MB` dengan Bot API lokal, atau turunkan `MAX_VIDEO_HEIGHT` |
| TikTok gagal via API | Set `TIKTOK_USE_TIKWM=0` untuk memakai jalur yt-dlp saja |
| Bot tidak merespons | Cek `journalctl`/log; pastikan token valid lewat `bot.py --check` |
| Unduhan terasa lambat setelah selesai | Bot sedang transcode video ke H.264 (berat di server ARM/CPU lemah). Set `TRANSCODE_TO_H264=0` bila pemutar Telegram di perangkatmu sudah mendukung HEVC/VP9 |
| `/sysinfo` menyebut ffmpeg tidak ditemukan | Set `FFMPEG_LOCATION=/path/ke/ffmpeg` di `.env` |
| Menu `/` tidak muncul | Jalankan `./run.sh push`; bila tetap kosong, restart aplikasi Telegram (menu di-cache di klien) |
