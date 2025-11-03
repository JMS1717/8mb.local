# Stage 1: build FFmpeg with NVENC on CUDA base
FROM nvidia/cuda:12.1.0-devel-ubuntu22.04 AS ffmpeg-build

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential pkg-config \
    nasm yasm cmake autoconf automake libtool \
    libnuma-dev libopus-dev wget curl \
    python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/src
# Install NVENC headers (ffnvcodec)
RUN git clone --depth 1 https://github.com/FFmpeg/nv-codec-headers.git && \
    make -C nv-codec-headers install

# Build FFmpeg 6.x
RUN git clone --depth 1 --branch n7.0 https://github.com/FFmpeg/FFmpeg.git ffmpeg
WORKDIR /opt/src/ffmpeg
RUN ./configure \
    --prefix=/opt/ffmpeg \
    --enable-nonfree \
    --enable-cuda-nvcc \
    --enable-libnpp \
    --enable-nvenc \
    --enable-libopus \
    --extra-cflags=-I/usr/local/cuda/include \
    --extra-ldflags=-L/usr/local/cuda/lib64 \
    --disable-debug --disable-doc && \
    make -j"$(nproc)" && make install

# Stage 2: runtime with Python and compiled FFmpeg
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04 AS runtime

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3-pip \
    libnuma1 libopus0 \
 && rm -rf /var/lib/apt/lists/*

ENV PATH=/opt/ffmpeg/bin:$PATH
COPY --from=ffmpeg-build /opt/ffmpeg /opt/ffmpeg

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir -r requirements.txt

COPY app /app/app

ENV REDIS_URL=redis://redis-broker:6379/0

# Celery worker
CMD ["celery", "-A", "app.celery_app.celery_app", "worker", "--loglevel=INFO", "-Q", "celery", "-n", "smartdrop@%h"]
