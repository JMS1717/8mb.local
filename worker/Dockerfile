# Stage 1 - build FFmpeg with NVENC/AV1 support
FROM nvidia/cuda:12.1.0-devel-ubuntu22.04 AS ffmpeg-build

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf \
    automake \
    build-essential \
    cmake \
    git \
    libtool \
    pkg-config \
    texinfo \
    wget \
    yasm \
    nasm \
    libopus-dev \
    libnuma-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

RUN git clone --depth=1 https://git.ffmpeg.org/ffmpeg.git ffmpeg
WORKDIR /tmp/ffmpeg

RUN ./configure \
    --prefix=/opt/ffmpeg \
    --pkg-config-flags="--static" \
    --extra-cflags="-I/usr/local/cuda/include" \
    --extra-ldflags="-L/usr/local/cuda/lib64" \
    --extra-libs="-lpthread -lm" \
    --bindir=/opt/ffmpeg/bin \
    --enable-gpl \
    --enable-nonfree \
    --enable-cuda-nvcc \
    --enable-libnpp \
    --enable-nvenc \
    --enable-libopus

RUN make -j"$(nproc)" && make install

# Stage 2 - runtime image
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    libopus0 \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/ffmpeg/bin:${PATH}"

COPY --from=ffmpeg-build /opt/ffmpeg /opt/ffmpeg

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

COPY backend/app ./app

ENV NVIDIA_VISIBLE_DEVICES=all

CMD ["celery", "-A", "app.tasks", "worker", "--loglevel=info"]
