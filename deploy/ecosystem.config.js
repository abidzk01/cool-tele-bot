/**
 * Konfigurasi PM2 untuk bot Telegram.
 *
 *   pm2 start deploy/ecosystem.config.js
 *   pm2 logs appbot-telegram
 *   pm2 save && pm2 startup
 *
 * Sesuaikan APPBOT_ROOT bila project tidak berada di /opt/app_bot_telegram:
 *   APPBOT_ROOT=/srv/appbot pm2 start deploy/ecosystem.config.js
 *
 * Catatan: PM2 tidak membaca .env. Bot memuat .env sendiri lewat python-dotenv,
 * jadi pastikan file .env ada di dalam APPBOT_ROOT.
 */
const path = require("path");

const ROOT = process.env.APPBOT_ROOT || "/opt/app_bot_telegram";
const LOG_DIR = process.env.APPBOT_LOG_DIR || path.join(ROOT, "logs");

module.exports = {
  apps: [
    {
      name: "appbot-telegram",
      cwd: ROOT,
      // jalankan interpreter python dari virtualenv, jangan dibungkus node
      script: path.join(ROOT, ".venv", "bin", "python"),
      args: "bot.py",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "60s",
      restart_delay: 5000,
      // beri waktu unduhan/unggahan berhenti rapi saat restart
      kill_timeout: 20000,
      max_memory_restart: "512M",
      time: true,
      merge_logs: true,
      out_file: path.join(LOG_DIR, "out.log"),
      error_file: path.join(LOG_DIR, "error.log"),
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};