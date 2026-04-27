# Multi-stage unified 8mb.local container
# Stage 1: Build FFmpeg 6.1.1 with VAAPI (Intel) + CPU encoders
FROM ubuntu:22.04 AS ffmpeg-build

ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential nasm yasm cmake pkg-config git wget ca-certificates \
    libnuma-dev libx264-dev libx265-dev libvpx-dev libopus-dev \
    libaom-dev libdav1d-dev \
    libva-dev libdrm-dev

WORKDIR /build

# Build FFmpeg 6.1.1 with VAAPI + CPU encoders (no QSV - needs FFmpeg 7+)
RUN wget -q https://ffmpeg.org/releases/ffmpeg-6.1.1.tar.xz && \
    tar xf ffmpeg-6.1.1.tar.xz && cd ffmpeg-6.1.1 && \
    ./configure \
      --enable-nonfree --enable-gpl \
      --enable-vaapi --enable-libdrm \
      --enable-libx264 --enable-libx265 --enable-libvpx --enable-libopus --enable-libaom --enable-libdav1d \
      --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages && \
    make -j$(nproc) && make install && ldconfig && \
    strip --strip-all /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    cd .. && rm -rf ffmpeg-6.1.1 ffmpeg-6.1.1.tar.xz /build

# Stage 2: Build Frontend
FROM node:20-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY frontend/ ./
# Build with empty backend URL (same-origin deployment)
ENV PUBLIC_BACKEND_URL=""
RUN npm run build && \
    # Remove source maps and unnecessary files to reduce size
    find build -name "*.map" -delete && \
    find build -name "*.ts" -delete

# Stage 3: Runtime with all services
FROM ubuntu:22.04

# Build-time version (can be overridden)
ARG BUILD_VERSION=136
ENV APP_VERSION=${BUILD_VERSION}
ARG BUILD_COMMIT=unknown
ENV BUILD_COMMIT=${BUILD_COMMIT}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip supervisor redis-server \
    libopus0 libx264-163 libx265-199 libvpx7 libnuma1 \
    libaom3 libdav1d5 \
    libva-drm2 libva-x11-2 libdrm2 \
    intel-media-va-driver-non-free i965-va-driver \
    vainfo intel-gpu-tools \
    && apt-get clean && rm -rf /tmp/*

# Copy FFmpeg from build stage (only what we need)
COPY --from=ffmpeg-build /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-build /usr/local/bin/ffprobe /usr/local/bin/ffprobe
# Copy only FFmpeg libraries (not entire /usr/local/lib)
COPY --from=ffmpeg-build /usr/local/lib/libavcodec.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libavformat.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libavutil.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libavfilter.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libswscale.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libswresample.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libavdevice.so* /usr/local/lib/
RUN ldconfig

WORKDIR /app

# Install Python dependencies (single consolidated requirements)
COPY requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --no-cache-dir -r /app/requirements.txt && \
    rm /app/requirements.txt && \
    # Remove pip cache and unnecessary files
    find /usr/local/lib/python3.10 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.10 -type f -name '*.pyc' -delete && \
    find /usr/local/lib/python3.10 -type f -name '*.pyo' -delete

# Copy application code
COPY backend-api/app /app/backend
COPY worker/app /app/worker

# Copy pre-built frontend
COPY --from=frontend-build /frontend/build /app/frontend-build

# Embed build metadata for runtime introspection
RUN echo "Version: ${APP_VERSION}" > /app/VERSION && \
    echo "Commit: ${BUILD_COMMIT}" >> /app/VERSION && \
    echo -n "Built: " >> /app/VERSION && date -u +%FT%TZ >> /app/VERSION && \
    echo "FFmpeg: $(ffmpeg -version | head -n1)" >> /app/VERSION

# Create necessary directories
RUN mkdir -p /app/uploads /app/outputs /var/log/supervisor /var/lib/redis /var/log/redis

# Configure supervisord
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Container entrypoint sets up NVIDIA library paths
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8001

ENTRYPOINT ["/app/entrypoint.sh"]
