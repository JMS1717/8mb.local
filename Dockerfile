# Multi-stage unified 8mb.local container
# Stage 1: Build FFmpeg with multi-vendor GPU support (NVIDIA NVENC, Intel QSV, AMD AMF/VAAPI)
# Using CUDA 11.8 for compatibility with older NVIDIA drivers (535.x series)
FROM nvidia/cuda:11.8.0-devel-ubuntu22.04 AS ffmpeg-build

ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential yasm cmake pkg-config git wget ca-certificates \
    libnuma-dev libx264-dev libx265-dev libvpx-dev libopus-dev \
    libaom-dev libdav1d-dev \
    libva-dev libdrm-dev

WORKDIR /build

# NVIDIA NVENC headers
# Pin to NVENC API 12.1 (driver 535.x compatibility). Use sdk/12.1.
RUN git clone https://git.videolan.org/git/ffmpeg/nv-codec-headers.git && \
    cd nv-codec-headers && git checkout sdk/12.1 && make install && cd ..

# Build FFmpeg with all hardware acceleration support
RUN wget -q https://ffmpeg.org/releases/ffmpeg-6.1.1.tar.xz && \
        tar xf ffmpeg-6.1.1.tar.xz && cd ffmpeg-6.1.1 && \
        ./configure \
      --enable-nonfree --enable-gpl \
      --enable-cuda-nvcc --enable-libnpp --enable-nvenc \
      --enable-vaapi \
      --enable-libx264 --enable-libx265 --enable-libvpx --enable-libopus --enable-libaom --enable-libdav1d \
      --extra-cflags=-I/usr/local/cuda/include \
      --extra-ldflags=-L/usr/local/cuda/lib64 \
      --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages && \
    make -j$(nproc) && make install && ldconfig && \
    # Strip binaries to reduce size
    strip --strip-all /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    # Clean up build artifacts
        cd .. && rm -rf ffmpeg-6.1.1 ffmpeg-6.1.1.tar.xz nv-codec-headers /build

# Stage 2: Build Frontend
FROM node:20-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --only=production

COPY frontend/ ./
# Build with empty backend URL (same-origin deployment)
ENV PUBLIC_BACKEND_URL=""
RUN npm run build && \
    # Remove source maps and unnecessary files to reduce size
    find build -name "*.map" -delete && \
    find build -name "*.ts" -delete

# Stage 3: Runtime with all services
# Using CUDA 11.8 runtime for compatibility with older NVIDIA drivers (535.x series)
FROM nvidia/cuda:11.8.0-runtime-ubuntu22.04

# Build-time version (can be overridden)
ARG BUILD_VERSION=123
ENV APP_VERSION=${BUILD_VERSION}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip supervisor redis-server \
    libopus0 libx264-163 libx265-199 libvpx7 libnuma1 \
    libva2 libva-drm2 libaom3 libdav1d5 \
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

# Install Python dependencies (backend + worker combined)
COPY backend-api/requirements.txt /app/backend-requirements.txt
COPY worker/requirements.txt /app/worker-requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --no-cache-dir -r /app/backend-requirements.txt -r /app/worker-requirements.txt && \
    rm /app/backend-requirements.txt /app/worker-requirements.txt && \
    # Remove pip cache and unnecessary files
    find /usr/local/lib/python3.10 -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.10 -type f -name '*.pyc' -delete && \
    find /usr/local/lib/python3.10 -type f -name '*.pyo' -delete

# Copy application code
COPY backend-api/app /app/backend
COPY worker/app /app/worker

# Copy pre-built frontend
COPY --from=frontend-build /frontend/build /app/frontend-build

# Create necessary directories
RUN mkdir -p /app/uploads /app/outputs /var/log/supervisor /var/lib/redis /var/log/redis

# Set NVIDIA driver capabilities for NVENC/NVDEC support
ENV NVIDIA_DRIVER_CAPABILITIES=compute,video,utility
ENV NVIDIA_VISIBLE_DEVICES=all

# Configure supervisord
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 8001

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
