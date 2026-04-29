# 8mb.local container with Jellyfin FFmpeg 7.1 (VAAPI + QSV + NVENC + CPU)
# No compilation needed — uses pre-built Jellyfin FFmpeg package

# Stage 1: Build Frontend
FROM node:20-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY frontend/ ./
ENV PUBLIC_BACKEND_URL=""
RUN npm run build && \
    find build -name "*.map" -delete && \
    find build -name "*.ts" -delete

# Stage 2: Runtime with all services
FROM ubuntu:22.04

ARG BUILD_VERSION=136
ENV APP_VERSION=${BUILD_VERSION}
ARG BUILD_COMMIT=unknown
ENV BUILD_COMMIT=${BUILD_COMMIT}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install base packages + Intel GPU repo (for up-to-date VAAPI/QSV drivers)
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip supervisor redis-server \
    gpg wget ca-certificates \
    && wget -qO - https://repositories.intel.com/gpu/intel-graphics.key | gpg --dearmor -o /usr/share/keyrings/intel-graphics.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] https://repositories.intel.com/gpu/ubuntu jammy client" > /etc/apt/sources.list.d/intel-gpu.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    libva2 libva-drm2 libva-x11-2 libvpl2 libmfxgen1 \
    intel-media-va-driver-non-free \
    vainfo intel-gpu-tools \
    && apt-get clean && rm -rf /tmp/*

# Install Jellyfin FFmpeg 7.1 — pre-built with VAAPI, QSV (libvpl), NVENC, and all CPU codecs
# Update URL for newer versions: https://repo.jellyfin.org/files/ffmpeg/ubuntu/latest-7.x/amd64/
ARG JELLYFIN_FFMPEG_URL=https://repo.jellyfin.org/files/ffmpeg/ubuntu/latest-7.x/amd64/jellyfin-ffmpeg7_7.1.3-5-jammy_amd64.deb
RUN wget -q -O /tmp/jellyfin-ffmpeg.deb "${JELLYFIN_FFMPEG_URL}" && \
    dpkg -i /tmp/jellyfin-ffmpeg.deb || true && \
    apt-get update && apt-get install -y -f && \
    rm /tmp/jellyfin-ffmpeg.deb && \
    ln -sf /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg && \
    ln -sf /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --no-cache-dir -r /app/requirements.txt && \
    rm /app/requirements.txt && \
    find /usr/local/lib/python3.10 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.10 -type f -name '*.pyc' -delete && \
    find /usr/local/lib/python3.10 -type f -name '*.pyo' -delete

# Copy application code
COPY backend-api/app /app/backend
COPY worker/app /app/worker

# Copy pre-built frontend
COPY --from=frontend-build /frontend/build /app/frontend-build

# Embed build metadata
RUN echo "Version: ${APP_VERSION}" > /app/VERSION && \
    echo "Commit: ${BUILD_COMMIT}" >> /app/VERSION && \
    echo -n "Built: " >> /app/VERSION && date -u +%FT%TZ >> /app/VERSION && \
    echo "FFmpeg: $(ffmpeg -version | head -n1)" >> /app/VERSION

# Create necessary directories
RUN mkdir -p /app/uploads /app/outputs /var/log/supervisor /var/lib/redis /var/log/redis

# Configure supervisord
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Container entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8001

ENTRYPOINT ["/app/entrypoint.sh"]
