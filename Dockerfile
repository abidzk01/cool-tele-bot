# syntax=docker/dockerfile:1
FROM python:3.12-slim

# --- Runtime dasar -----------------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FFMPEG_LOCATION=/usr/bin/ffmpeg \
    DATA_DIR=/app/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# --- Pengguna non-root -------------------------------------------------------
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appbot

WORKDIR /app

# dependency dulu agar layer cache efisien
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY --chown=appbot:appbot . .
RUN mkdir -p /app/data/downloads /app/data/tmp /app/cookies \
 && chown -R appbot:appbot /app

USER appbot

# Healthcheck nyata: memeriksa heartbeat yang ditulis job queue tiap 60 detik,
# jadi yang diuji adalah event loop yang hidup, bukan cuma proses yang ada.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "bot.py", "--health"]

CMD ["python", "bot.py"]