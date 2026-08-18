# Multi-stage unified 8mb.local container
# Stage 1: Build FFmpeg with NVIDIA NVENC, Intel oneVPL QSV, Linux VAAPI,
# and CPU encoders.
# Use CUDA 12.2 devel image: supports RTX 50-series and is compatible with NVIDIA driver 535+
FROM nvidia/cuda:12.2.0-devel-ubuntu22.04 AS ffmpeg-build

ENV DEBIAN_FRONTEND=noninteractive
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential nasm yasm cmake meson ninja-build pkg-config git wget ca-certificates \
    libnuma-dev libx264-dev libx265-dev libvpx-dev libopus-dev \
    libaom-dev libdav1d-dev libva-dev libdrm-dev \
    libx11-dev libxext-dev libxfixes-dev libx11-xcb-dev libxcb1-dev \
    libxcb-dri3-dev libwayland-dev

WORKDIR /build

# Ubuntu 22.04 provides VA-API 1.14, but oneVPL's Linux device selection
# requires vaGetDriverNameByIndex (VA-API 1.15+). Build a current libva so
# QSV can bind the requested /dev/dri render node instead of failing before
# the Intel implementation is loaded.
ARG LIBVA_VERSION=2.21.0
RUN git clone --depth 1 --branch ${LIBVA_VERSION} https://github.com/intel/libva.git && \
    meson setup libva/build libva --prefix=/usr/local --libdir=lib \
      -Dwith_x11=yes -Dwith_wayland=yes -Dwith_glx=no && \
    meson compile -C libva/build -j"$(nproc)" && \
    meson install -C libva/build && ldconfig && rm -rf libva

# Match the exact GmmLib dependency published for media-driver 24.1.5.
# Building media-driver against Jammy's older headers omits newer product
# families and fails late in the compile with undefined IGFX_* symbols.
ARG GMMLIB_VERSION=intel-gmmlib-22.3.18
RUN git clone --depth 1 --branch ${GMMLIB_VERSION} https://github.com/intel/gmmlib.git && \
    cmake -S gmmlib -B gmmlib/build \
      -DCMAKE_BUILD_TYPE=ReleaseInternal -DCMAKE_INSTALL_PREFIX=/usr/local && \
    cmake --build gmmlib/build -j"$(nproc)" && \
    cmake --install gmmlib/build && ldconfig && rm -rf gmmlib

# Keep the Intel iHD driver aligned with the VA-API runtime. Jammy's 22.3
# driver cannot report the render-node device ID required by oneVPL, and it
# omits HEVC encode entrypoints exposed by current Intel hardware/drivers.
ARG INTEL_MEDIA_DRIVER_VERSION=intel-media-24.1.5
RUN git clone --depth 1 --branch ${INTEL_MEDIA_DRIVER_VERSION} \
      https://github.com/intel/media-driver.git && \
    cmake -S media-driver -B media-driver/build \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \
      -DINSTALL_DRIVER_SYSCONF=OFF && \
    cmake --build media-driver/build -j"$(nproc)" && \
    cmake --install media-driver/build && \
    # The release driver contains a large unstripped symbol table.  Keep the
    # runtime code, but do not carry build/debug symbols into the final image.
    strip --strip-unneeded /usr/local/lib/dri/iHD_drv_video.so && \
    rm -rf media-driver

# Intel oneVPL dispatcher. Ubuntu 22.04's package is older than the oneVPL
# 2.6 minimum required by FFmpeg 6.1, so pin the upstream dispatcher release.
ARG LIBVPL_VERSION=v2023.4.0
RUN git clone --depth 1 --branch ${LIBVPL_VERSION} https://github.com/intel/libvpl.git && \
    cmake -S libvpl -B libvpl/build \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local \
      -DBUILD_TOOLS=OFF -DBUILD_EXAMPLES=OFF && \
    cmake --build libvpl/build -j"$(nproc)" && \
    cmake --install libvpl/build && ldconfig && rm -rf libvpl

# NVIDIA NVENC headers
# Pin to NVENC API 12.1 for widest compatibility with driver 535.x, while CUDA 12.2 runtime covers RTX 50‑series
RUN git clone --depth 1 --branch sdk/12.1 https://github.com/FFmpeg/nv-codec-headers.git && \
    cd nv-codec-headers && git checkout sdk/12.1 && make install && cd ..

# SVT-AV1: Ubuntu 22.04's libsvtav1 (0.9.x) is too old for FFmpeg 6.1's libsvtav1 glue
# (e.g. EbSvtAv1EncConfiguration.force_key_frames). Build a current release from source.
# Canonical upstream is on GitLab (the GitHub mirror has no release tags).
# Must match FFmpeg's bundled libsvtav1.c: FFmpeg 6.1.x targets SVT-AV1 2.x API;
# SVT 3.x changed ``svt_av1_enc_init_handle`` and breaks the build.
ARG SVTAV1_VERSION=v2.2.1
RUN git clone --depth 1 --branch ${SVTAV1_VERSION} https://gitlab.com/AOMediaCodec/SVT-AV1.git && \
    cd SVT-AV1/Build && \
    cmake .. -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local && \
    cmake --build . -j"$(nproc)" && cmake --install . && ldconfig && \
    cd /build && rm -rf SVT-AV1

# Build FFmpeg with NVIDIA NVENC, Intel oneVPL QSV, Linux VAAPI, and CPU encoders
RUN git clone --depth 1 --branch n6.1.1 https://github.com/FFmpeg/FFmpeg.git ffmpeg-6.1.1 && \
        cd ffmpeg-6.1.1 && \
                ./configure \
      --enable-nonfree --enable-gpl \
      --enable-cuda-nvcc --enable-libnpp --enable-nvenc --enable-libvpl --enable-vaapi --enable-libdrm \
      --enable-libx264 --enable-libx265 --enable-libvpx --enable-libopus --enable-libaom --enable-libsvtav1 --enable-libdav1d \
      --extra-cflags=-I/usr/local/cuda/include \
      --extra-ldflags=-L/usr/local/cuda/lib64 \
      --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages && \
    make -j$(nproc) && make install && ldconfig && \
    # Strip binaries to reduce size
    strip --strip-all /usr/local/bin/ffmpeg /usr/local/bin/ffprobe && \
    # Clean up build artifacts
        cd .. && rm -rf ffmpeg-6.1.1 nv-codec-headers /build

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
# Use plain Ubuntu for the application runtime.  NVIDIA Container Toolkit
# injects the host driver libraries/devices at runtime; only the NPP shared
# libraries directly required by FFmpeg's scale_npp path are copied below.
# This avoids shipping the full CUDA toolkit/runtime library tree.
FROM ubuntu:22.04

# Build metadata. scripts/set-version.ps1 keeps the default synchronized with
# the root VERSION file; release builders override these values explicitly.
ARG BUILD_VERSION=141.0.0.0
ENV APP_VERSION=${BUILD_VERSION}
ARG BUILD_COMMIT=unknown
ENV BUILD_COMMIT=${BUILD_COMMIT}
ARG BUILD_TIMESTAMP=unknown
ARG BUILD_REPOSITORY=https://github.com/JMS1717/8mb.local
LABEL org.opencontainers.image.version=${BUILD_VERSION} \
      org.opencontainers.image.revision=${BUILD_COMMIT} \
      org.opencontainers.image.created=${BUILD_TIMESTAMP} \
      org.opencontainers.image.source=${BUILD_REPOSITORY}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip supervisor redis-server \
    libopus0 libx264-163 libx265-199 libvpx7 libnuma1 \
    libaom3 libdav1d5 libva2 libva-drm2 libdrm2 libmfx1 libmfx-gen1.2 \
    mesa-va-drivers intel-media-va-driver vainfo \
    && apt-get clean && rm -rf /tmp/*

# Copy FFmpeg from build stage (only what we need)
COPY --from=ffmpeg-build /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-build /usr/local/bin/ffprobe /usr/local/bin/ffprobe
# FFmpeg links these NPP components for the existing NVIDIA scale_npp path.
# CUDA driver libraries and NVENC/NVDEC implementations are supplied by the
# NVIDIA Container Toolkit from the host at runtime.
COPY --from=ffmpeg-build /usr/local/cuda-12.2/targets/x86_64-linux/lib/libnppc.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/cuda-12.2/targets/x86_64-linux/lib/libnppig.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/cuda-12.2/targets/x86_64-linux/lib/libnppicc.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/cuda-12.2/targets/x86_64-linux/lib/libnppidei.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/cuda-12.2/targets/x86_64-linux/lib/libnppif.so* /usr/local/lib/
# SVT-AV1 is built from source in ffmpeg-build (not Ubuntu packages)
COPY --from=ffmpeg-build /usr/local/lib/libSvtAv1Enc.so* /usr/local/lib/
# Intel oneVPL dispatcher built above (the GPU implementation is supplied by
# the runtime's libmfx-gen package and uses /dev/dri on Linux hosts).
COPY --from=ffmpeg-build /usr/local/lib/libvpl.so* /usr/local/lib/
# oneVPL needs VA-API 1.15+ for explicit Linux render-node selection. These
# libraries take precedence over Ubuntu 22.04's older /usr/lib copies.
COPY --from=ffmpeg-build /usr/local/lib/libva.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libva-drm.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libva-x11.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libva-wayland.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/libigdgmm.so* /usr/local/lib/
COPY --from=ffmpeg-build /usr/local/lib/dri/iHD_drv_video.so /usr/local/lib/dri/
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
COPY shared /app/shared

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
# Keep the source-built Intel iHD driver first while retaining Mesa's AMD and
# other VAAPI drivers from the distro directory. A single /usr/local-only path
# makes radeonsi invisible and breaks AMD VAAPI.
ENV LIBVA_DRIVERS_PATH=/usr/local/lib/dri:/usr/lib/x86_64-linux-gnu/dri:/usr/lib/dri
# Keep the source-built Intel stack ahead of Jammy's older VA/Gmm libraries.
# This is intentional: only the matching source-built Intel artifacts are
# loaded from /usr/local; AMD continues to use the distro Mesa driver.
ENV LD_LIBRARY_PATH=/usr/local/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64

# Configure supervisord
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Container entrypoint sets up NVIDIA library paths
COPY entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh \
    && chmod +x /app/entrypoint.sh

EXPOSE 8001

# Expose a real container health signal for Compose/orchestrators.  This only
# checks the API process; codec/device readiness remains available through
# /api/system/encoder-tests and the startup probe cache.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=3).read()"

ENTRYPOINT ["/app/entrypoint.sh"]
