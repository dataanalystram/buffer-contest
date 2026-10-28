# HuhQueue in one container: UI + REST API on 8787, public edge (media + OAuth callbacks) on 8788, worker in-process.
#   docker compose up -d        (see docker-compose.yml)
# Data (SQLite, media, the generated token key) lives in the /data volume - back it up, keep it across upgrades.
FROM python:3.12-slim

# ffmpeg/ffprobe: video end cards and duration probes (optional features, small enough to always include)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY huhqueue ./huhqueue
RUN pip install --no-cache-dir . && useradd --create-home --uid 1000 huhq && mkdir -p /data && chown huhq /data

USER huhq
ENV HUHQ_DATA_DIR=/data HUHQ_HOST=0.0.0.0 HUHQ_PORT=8787 HUHQ_PUBLIC_PORT=8788 PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8787 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=4).status == 200 else 1)"
CMD ["huhqueue", "serve", "--edge-host", "0.0.0.0"]
