# Multi-stage unified 8mb.local container
# Build-time args let us produce multiple variants from one Dockerfile
ARG CUDA_VERSION=13.0.1
ARG UBUNTU_FLAVOR=ubuntu22.04
ARG FFMPEG_VERSION=8.0
ARG NV_CODEC_HEADERS_REF=sdk/12.2
ARG BUILD_FLAVOR=latest
ARG DRIVER_MIN=550.00
ARG NV_CODEC_COMPAT=12.0
ARG USE_CUDA_13=false

# Stage 0: CUDA 13 base (if needed) - manually install CUDA 13 toolkit
FROM ubuntu:22.04 AS cuda13-base
ARG USE_CUDA_13
RUN if [ "$USE_CUDA_13" = "true" ]; then \
    export DEBIAN_FRONTEND=noninteractive && \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates wget gnupg2 && \
    wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb && \
    dpkg -i cuda-keyring_1.1-1_all.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        cuda-toolkit-13-0 \
        libnvidia-encode-580 \
        libnvidia-decode-580 && \
    rm -rf /var/lib/apt/lists/* cuda-keyring_1.1-1_all.deb; \
fi

# Stage 1: Build FFmpeg with multi-vendor GPU support (NVIDIA NVENC, Intel QSV, AMD VAAPI)
# CUDA is parameterized to support both legacy and latest builds
FROM nvidia/cuda:${CUDA_VERSION}-devel-${UBUNTU_FLAVOR} AS ffmpeg-build
ARG FFMPEG_VERSION
ARG NV_CODEC_HEADERS_REF
ARG NV_CODEC_COMPAT
ARG USE_CUDA_13=false
# Default to RTX 50-series and recent architectures (Blackwell/Hopper/Ada/Ampere/Turing/Volta)
# For RTX 50-series: sm_100, Hopper: sm_90, Ada: sm_89, Ampere: sm_86/80, Turing: sm_75, Volta: sm_70
ARG NVCC_ARCHS="100 90 89 86 80 75"
ARG ENABLE_LIBNPP=true

ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential yasm nasm cmake pkg-config git wget ca-certificates \
    libnuma-dev libx264-dev libx265-dev libvpx-dev libopus-dev \
    libaom-dev libdav1d-dev \
    zlib1g-dev libbz2-dev liblzma-dev \
    libva-dev libdrm-dev

WORKDIR /build

# Ensure CUDA toolchain (nvcc) is on PATH for FFmpeg configure
ENV CUDA_HOME=/usr/local/cuda
ENV PATH=/usr/local/cuda/bin:${PATH}

# NVIDIA NVENC headers
# Use NV_CODEC_COMPAT for legacy builds (sdk/12.0 for FFmpeg 6.x, sdk/12.2+ for FFmpeg 7+)
RUN REF="${NV_CODEC_HEADERS_REF}" && \
    if [ -n "${NV_CODEC_COMPAT}" ] && [ "${NV_CODEC_COMPAT}" != "${NV_CODEC_HEADERS_REF}" ]; then \
        echo "Using ${NV_CODEC_COMPAT} headers for FFmpeg ${FFMPEG_VERSION} compatibility" && \
        REF="${NV_CODEC_COMPAT}"; \
    fi && \
    git clone --depth=1 --branch "${REF}" https://github.com/FFmpeg/nv-codec-headers.git || \
    (echo "Warning: ${REF} not found, falling back to ${NV_CODEC_HEADERS_REF}" && \
     git clone --depth=1 --branch "${NV_CODEC_HEADERS_REF}" https://github.com/FFmpeg/nv-codec-headers.git) && \
    cd nv-codec-headers && \
    make install && cd ..

# Ensure pkg-config can find ffnvcodec (nv-codec-headers installs ffnvcodec.pc under /usr/local/lib/pkgconfig)
# Avoid UndefinedVar warning by declaring build arg and using parameter expansion safely
ARG PKG_CONFIG_PATH
ENV PKG_CONFIG_PATH=/usr/local/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}

# Build FFmpeg with all hardware acceleration support
RUN wget -q https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz && \
            tar xf ffmpeg-${FFMPEG_VERSION}.tar.xz && cd ffmpeg-${FFMPEG_VERSION} && \
                # Robustly locate nvcc across CUDA layouts (e.g., /usr/local/cuda or /usr/local/cuda-13.0)
                NVCC_PATH="$(ls -1d /usr/local/cuda*/bin/nvcc 2>/dev/null | head -n1)" && \
                if [ -z "$NVCC_PATH" ]; then echo "nvcc not found under /usr/local/cuda*" && exit 1; fi && \
                echo "Using NVCC at: $NVCC_PATH" && \
                    # Use a single target arch during configure/build to avoid nvcc -ptx multi-arch restriction
                    FIRST_ARCH=$(echo ${NVCC_ARCHS} | awk '{print $1}') && \
                    NVCC_FLAGS="-arch=sm_${FIRST_ARCH}" && \
                    echo "Using NVCC flags: $NVCC_FLAGS (from NVCC_ARCHS='${NVCC_ARCHS}')" && \
                    NPP_FLAG="--disable-libnpp" && if [ "${ENABLE_LIBNPP}" = "true" ]; then NPP_FLAG="--enable-libnpp"; fi && \
                    # FFmpeg 7.x+ uses --enable-ffnvcodec, FFmpeg 6.x uses --enable-cuda-nvcc
                    FFMPEG_MAJOR=$(echo ${FFMPEG_VERSION} | cut -d. -f1) && \
                    echo "FFmpeg major version: $FFMPEG_MAJOR" && \
            if [ "$FFMPEG_MAJOR" -ge 7 ]; then \
                echo "Using FFmpeg 7.x+ configuration with --enable-ffnvcodec" && \
                ./configure \
                    --enable-nonfree --enable-gpl \
                    --enable-ffnvcodec --enable-nvenc --enable-nvdec --enable-cuvid \
                    --enable-vaapi \
                    --enable-libx264 --enable-libx265 --enable-libvpx --enable-libopus --enable-libaom --enable-libdav1d \
                    --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages \
                    || (echo "FFmpeg configure failed; dumping ffbuild/config.log:" && cat ffbuild/config.log && exit 1); \
            else \
                echo "Using FFmpeg 6.x configuration with --enable-cuda-nvcc" && \
                echo "NVCC_PATH=$NVCC_PATH" && \
                echo "NVCC_FLAGS=$NVCC_FLAGS" && \
                echo "NPP_FLAG=$NPP_FLAG" && \
                ./configure \
                    --enable-nonfree --enable-gpl \
                    --enable-cuda --enable-cuda-nvcc --enable-cuvid \
                    --enable-nvenc --enable-nvdec \
                    --enable-vaapi \
                    --enable-libx264 --enable-libx265 --enable-libvpx --enable-libopus --enable-libaom --enable-libdav1d \
                    --nvcc="$NVCC_PATH" \
                    --nvccflags="$NVCC_FLAGS" \
                    ${NPP_FLAG} \
                    --extra-cflags="-I/usr/local/cuda/include" \
                    --extra-ldflags="-L/usr/local/cuda/lib64" \
                    --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages \
                    || (echo "FFmpeg configure failed; dumping ffbuild/config.log:" && cat ffbuild/config.log && exit 1); \
            fi && \
    make -j$(nproc) && make install && ldconfig && \
    # Strip binaries to reduce size
    strip --strip-all /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    # Create CUDA wrapper library for RTX 50-series WSL2 compatibility
    # This provides missing cuPvt symbols as stubs without intercepting dlsym
    echo '#define _GNU_SOURCE' > /tmp/cuda_wrapper.c && \
    echo '#include <stdio.h>' >> /tmp/cuda_wrapper.c && \
    echo 'typedef int CUresult;' >> /tmp/cuda_wrapper.c && \
    echo '#define CUDA_SUCCESS 0' >> /tmp/cuda_wrapper.c && \
    echo 'CUresult cuPvtCompilePtx(void* a, void* b, void* c, void* d) { return CUDA_SUCCESS; }' >> /tmp/cuda_wrapper.c && \
    echo 'CUresult cuPvtBinaryFree(void* a) { return CUDA_SUCCESS; }' >> /tmp/cuda_wrapper.c && \
    gcc -shared -fPIC /tmp/cuda_wrapper.c -o /usr/local/lib/libcuda_stubs.so && \
    # Clean up build artifacts
    cd .. && rm -rf ffmpeg-${FFMPEG_VERSION} ffmpeg-${FFMPEG_VERSION}.tar.xz nv-codec-headers /build

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
# Try runtime-* which includes full CUDA runtime (not just base libs)
FROM nvidia/cuda:${CUDA_VERSION}-runtime-${UBUNTU_FLAVOR}
ARG BUILD_FLAVOR
ARG FFMPEG_VERSION
ARG CUDA_VERSION
ARG DRIVER_MIN

# Build-time version (can be overridden)
ARG BUILD_VERSION=123
ENV APP_VERSION=${BUILD_VERSION}
ENV BUILD_FLAVOR=${BUILD_FLAVOR}
ENV BUILD_FFMPEG=${FFMPEG_VERSION}
ENV BUILD_CUDA=${CUDA_VERSION}
ENV DRIVER_MIN=${DRIVER_MIN}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip supervisor redis-server \
    libopus0 libx264-163 libx265-199 libvpx7 libnuma1 \
    libva2 libva-drm2 libaom3 libdav1d5 \
    pciutils procps \
    && apt-get clean && rm -rf /tmp/*

# Copy FFmpeg binaries and their library dependencies from build stage
COPY --from=ffmpeg-build /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-build /usr/local/bin/ffprobe /usr/local/bin/ffprobe
# Copy CUDA wrapper library for RTX 50-series CUDA 13 support
COPY --from=ffmpeg-build /usr/local/lib/libcuda_stubs.so /usr/local/lib/libcuda_stubs.so
# Install CUDA 12.8 forward compatibility package for RTX 50-series / CUDA 13 support
# This provides libcuda.so from driver 570 which supports CUDA 13 hardware on the host
RUN apt-get update && apt-get install -y cuda-compat-12-8 && rm -rf /var/lib/apt/lists/*
# Copy FFmpeg libraries
COPY --from=ffmpeg-build /usr/local/lib/libavcodec.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libavformat.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libavutil.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libavfilter.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libswscale.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libswresample.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libavdevice.so* /usr/local/lib/
# Copy NVIDIA Performance Primitives (NPP) libraries required by FFmpeg's --enable-libnpp
# Note: cuda:*-base-* includes libcudart but NOT libnpp*, so we must copy from devel stage
COPY --from=ffmpeg-build /usr/local/cuda/lib64/libnpp*.so* /usr/local/cuda/lib64/
# Create symlinks for NVENC/NVDEC libraries in a standard location where FFmpeg can find them
# The actual .so.1 files are mounted by the NVIDIA container toolkit at runtime
# CRITICAL FIX for RTX 50-series: Replace stub libcuda.so.1 with symlink to WSL driver
RUN ldconfig && \
    mkdir -p /usr/local/nvidia/lib64 && \
    # Replace the 172KB stub libcuda.so.1 with the real WSL driver
    # The .wsl symlink exists but linker finds stub first - we need to replace the stub
    rm -f /usr/lib/x86_64-linux-gnu/libcuda.so.1 /usr/lib/x86_64-linux-gnu/libcuda.so && \
    ln -s libcuda.so.1.wsl /usr/lib/x86_64-linux-gnu/libcuda.so.1 && \
    ln -s libcuda.so.1 /usr/lib/x86_64-linux-gnu/libcuda.so && \
    # Update ldconfig to search in all NVIDIA library locations
    echo "/usr/lib/x86_64-linux-gnu" >> /etc/ld.so.conf.d/nvidia.conf && \
    echo "/usr/local/cuda/lib64" >> /etc/ld.so.conf.d/nvidia.conf && \
    echo "/usr/local/nvidia/lib64" >> /etc/ld.so.conf.d/nvidia.conf && \
    echo "/usr/lib/wsl/lib" >> /etc/ld.so.conf.d/nvidia.conf && \
    ldconfig

WORKDIR /app
ENV PYTHONPATH=/app

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
COPY common /app/common

# Copy pre-built frontend
COPY --from=frontend-build /frontend/build /app/frontend-build

# Create necessary directories
RUN mkdir -p /app/uploads /app/outputs /var/log/supervisor /var/lib/redis /var/log/redis

# Set NVIDIA driver capabilities for NVENC/NVDEC support
ENV NVIDIA_DRIVER_CAPABILITIES=compute,video,utility
ENV NVIDIA_VISIBLE_DEVICES=all

# Configure supervisord
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Runtime compatibility check and entrypoint
COPY entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

EXPOSE 8001

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
