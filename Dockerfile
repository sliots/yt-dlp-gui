FROM python:3.12-slim

# ── 低频变更层 ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip \
    -o /tmp/deno.zip && unzip /tmp/deno.zip -d /usr/local/bin && rm /tmp/deno.zip

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 高频变更层 ──
COPY . .

# yt-dlp nightly 置于最底层，每日定时构建仅重拉此层
RUN curl -fsSL https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp_linux \
    -o /usr/local/bin/yt-dlp && chmod +x /usr/local/bin/yt-dlp

RUN mkdir -p /app/config /downloads

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

ENTRYPOINT ["python", "-m", "app.main"]
