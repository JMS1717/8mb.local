# Multi-stage unified 8mb.local container
# Stage 1: Build FFmpeg with NVIDIA NVENC GPU support + CPU encoders
# Use CUDA 12.2 devel image: supports RTX 50-series and is compatible with NVIDIA driver 535+
FROM nvidia/cuda:12.2.0-devel-ubuntu22.04 AS ffmpeg-build

ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential yasm nasm cmake pkg-config git wget ca-certificates \
    libnuma-dev libx264-dev libx265-dev libvpx-dev libopus-dev \
    libaom-dev libdav1d-dev meson ninja-build

WORKDIR /build

# NVIDIA NVENC headers
# Pin to NVENC API 12.1 for widest compatibility with driver 535.x, while CUDA 12.2 runtime covers RTX 50‑series
RUN git clone https://git.videolan.org/git/ffmpeg/nv-codec-headers.git && \
    cd nv-codec-headers && git checkout sdk/12.1 && make install && cd ..

# Build SVT-AV1 from source for fast CPU AV1 encoding
RUN git clone --depth 1 --branch v2.3.0 https://gitlab.com/AOMediaCodec/SVT-AV1.git && \
    cd SVT-AV1 && \
    cd Build && \
    cmake .. -G"Unix Makefiles" -DCMAKE_BUILD_TYPE=Release -DBUILD_APPS=OFF -DBUILD_DEC=OFF && \
    make -j$(nproc) && make install && ldconfig && \
    cd /build && rm -rf SVT-AV1

# Build FFmpeg with NVIDIA NVENC + CPU encoders (including SVT-AV1)
RUN wget -q https://ffmpeg.org/releases/ffmpeg-6.1.1.tar.xz && \
        tar xf ffmpeg-6.1.1.tar.xz && cd ffmpeg-6.1.1 && \
                ./configure \
      --enable-nonfree --enable-gpl \
      --enable-cuda-nvcc --enable-libnpp --enable-nvenc \
      --enable-libx264 --enable-libx265 --enable-libvpx --enable-libopus --enable-libaom --enable-libdav1d --enable-libsvtav1 \
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
    npm ci

COPY frontend/ ./
# Build with empty backend URL (same-origin deployment)
ENV PUBLIC_BACKEND_URL=""
RUN npm run build && \
    # Remove source maps and unnecessary files to reduce size
    find build -name "*.map" -delete && \
    find build -name "*.ts" -delete

# Stage 3: Runtime with all services
# Use CUDA 12.2 runtime: minimum driver 535; supports RTX 50-series and older (535+) systems
FROM nvidia/cuda:12.2.0-runtime-ubuntu22.04

# Build-time version (can be overridden)
ARG BUILD_VERSION=200
ENV APP_VERSION=${BUILD_VERSION}
ARG BUILD_COMMIT=unknown
ENV BUILD_COMMIT=${BUILD_COMMIT}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip python3-dev gcc supervisor redis-server \
    libopus0 libx264-163 libx265-199 libvpx7 libnuma1 \
    libaom3 libdav1d5 \
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
# SVT-AV1 shared library (built from source in Stage 1)
COPY --from=ffmpeg-build /usr/local/lib/libSvtAv1Enc.so* /usr/local/lib/
RUN ldconfig

WORKDIR /app

# Install Python dependencies directly (Docker container = isolation; no venv needed)
COPY requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install -r /app/requirements.txt && \
    rm /app/requirements.txt && \
    # Verify critical packages installed successfully
    python3 -c "import uvicorn; import celery; print('✓ uvicorn + celery OK')"

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

# Set NVIDIA driver capabilities for NVENC/NVDEC support
ENV NVIDIA_DRIVER_CAPABILITIES=compute,video,utility
ENV NVIDIA_VISIBLE_DEVICES=all

# Configure supervisord
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Container entrypoint sets up NVIDIA library paths
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8001

ENTRYPOINT ["/app/entrypoint.sh"]
