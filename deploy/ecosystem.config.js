/**
 * PM2 configuration for the Telegram bot.
 *
 *   pm2 start deploy/ecosystem.config.js
 *   pm2 logs appbot-telegram
 *   pm2 save && pm2 startup
 *
 * Override APPBOT_ROOT when the project is not at /opt/app_bot_telegram:
 *   APPBOT_ROOT=/srv/appbot pm2 start deploy/ecosystem.config.js
 *
 * Note: PM2 does not read .env. The bot loads it itself through python-dotenv,
 * so make sure .env exists inside APPBOT_ROOT.
 */
const path = require("path");

const ROOT = process.env.APPBOT_ROOT || "/opt/app_bot_telegram";
const LOG_DIR = process.env.APPBOT_LOG_DIR || path.join(ROOT, "logs");

module.exports = {
  apps: [
    {
      name: "appbot-telegram",
      cwd: ROOT,
      // run the virtualenv interpreter directly; do not wrap it in node
      script: path.join(ROOT, ".venv", "bin", "python"),
      args: "bot.py",
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      min_uptime: "60s",
      restart_delay: 5000,
      // give running downloads/uploads time to stop cleanly on restart
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