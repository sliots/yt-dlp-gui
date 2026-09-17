FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/

ARG DENO_VERSION=v2.9.7
ARG YTDLP_VERSION=2026.09.16.232951
ARG BGUTIL_VERSION=2.0.0

RUN apt-get update && apt-get install -y --no-install-recommends curl unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

RUN mkdir -p /out/plugins \
    && curl -fsSL "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" \
       -o /tmp/deno.zip \
    && unzip /tmp/deno.zip -d /out \
    && curl -fsSL "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/download/${YTDLP_VERSION}/yt-dlp_linux" \
       -o /out/yt-dlp \
    && curl -fsSL "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/releases/download/${BGUTIL_VERSION}/bgutil-ytdlp-pot-provider.zip" \
       -o /out/plugins/bgutil-ytdlp-pot-provider.zip \
    && chmod +x /out/deno /out/yt-dlp

FROM python:3.14-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /out/deno /usr/local/bin/deno
COPY --from=builder /out/yt-dlp /usr/local/bin/yt-dlp
COPY --from=builder /out/plugins /opt/yt-dlp-plugins
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY VERSION pyproject.toml ./

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /opt/ffmpeg-wrapper /app/config /downloads \
    && chmod +x /app/app/ffmpeg_wrapper.py \
    && ln -s /app/app/ffmpeg_wrapper.py /opt/ffmpeg-wrapper/ffmpeg \
    && ln -s /usr/bin/ffprobe /opt/ffmpeg-wrapper/ffprobe \
    && chown -R app:app /app /downloads

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_ROOT=/downloads \
    CONFIG_ROOT=/app/config \
    YTDLP_BIN=/usr/local/bin/yt-dlp \
    FFMPEG_WRAPPER_DIR=/opt/ffmpeg-wrapper \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    DENO_DIR=/tmp/deno

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/api/health', timeout=3)" || exit 1

ENTRYPOINT ["python", "-m", "app.main"]
