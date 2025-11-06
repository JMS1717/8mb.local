# Publishing 8mb.local to Docker Hub

## Image Variants

We publish two Docker image tags:

- **`jms1717/8mblocal:latest`** (Primary, Linux-focused)
  - CUDA 12.2.0 + FFmpeg 6.1.1
  - NVENC headers sdk/12.0
  - Target: Linux/Debian production servers with driver 535.x+
  - Architectures: 86 80 75 70 (Turing, Ampere, Ada)
  - **Actively maintained and recommended for production**

- **`jms1717/8mblocal:cuda13`** (One-time build, Windows/WSL2)
  - CUDA 13.0.1 + FFmpeg 8.0
  - NVENC headers sdk/12.2
  - Target: Windows/WSL2 with RTX 50-series (Blackwell) and driver 550+
  - Architectures: 100 90 89 86 80 75
  - **Provided as-is, not actively maintained**

Both images use unified Dockerfile with multi-stage build for backend/worker/frontend.

## 1) Create Docker Hub repository
Create a public repo under your Docker Hub account (replace `jms1717` with your username/org):
- `jms1717/8mblocal`

## 2) Add GitHub secrets for CI
In your GitHub repo, add these secrets:
- `DOCKERHUB_USERNAME` = your Docker Hub username
- `DOCKERHUB_TOKEN` = a Docker Hub access token with write permissions

## 3) CI workflow
The `.github/workflows/build.yml` workflow:
- Uses manual dispatch with flavor selection (latest / cuda13 / both)
- Builds unified container with all services (backend/worker/redis)
- Pushes to Docker Hub with appropriate tags
- Supports build args for CUDA version, FFmpeg version, NVENC headers, etc.

Trigger manually via the Actions tab → Build and Push Docker Images → Run workflow → Select build_flavor.

## 4) Quick start (pull and run)

### Linux/Debian with NVIDIA driver 535.x+ (Recommended)
```bash
docker run -d --name 8mblocal --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,video,utility \
  -p 8001:8001 \
  -v ./uploads:/app/uploads \
  -v ./outputs:/app/outputs \
  jms1717/8mblocal:latest
```

### Windows/WSL2 with RTX 50-series and driver 550+
```bash
docker run -d --name 8mblocal --gpus all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,video,utility \
  -p 8001:8001 \
  -v ./uploads:/app/uploads \
  -v ./outputs:/app/outputs \
  jms1717/8mblocal:cuda13
```

Access the web UI at: http://localhost:8001

## Notes
- The container uses NVIDIA CUDA base and requires GPU access for NVENC. On Linux, install `nvidia-container-toolkit`; on Windows + WSL2, ensure GPU support is enabled in Docker Desktop.
- The `:latest` tag is recommended for production Linux/Debian deployments with driver 535.x or newer.
- The `:cuda13` tag is a one-time build for newer Windows/WSL2 GPUs and is not actively maintained.
- Multi-arch (arm64) builds are not enabled due to CUDA base image constraints. The workflow targets `linux/amd64`.
