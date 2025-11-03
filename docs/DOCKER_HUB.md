# Publishing 8mb.local to Docker Hub

This repo contains three images:
- `8mblocal-backend` (FastAPI API)
- `8mblocal-worker` (Celery worker with FFmpeg + NVENC)
- `8mblocal-frontend` (SvelteKit preview server)

## 1) Create Docker Hub repositories
Create three public repos under your Docker Hub account (replace `jms1717` with your username/org):
- `jms1717/8mblocal-backend`
- `jms1717/8mblocal-worker`
- `jms1717/8mblocal-frontend`

## 2) Add GitHub secrets for CI
In your GitHub repo, add these secrets:
- `DOCKERHUB_USERNAME` = your Docker Hub username
- `DOCKERHUB_TOKEN` = a Docker Hub access token with write permissions

## 3) CI workflow
This repo includes `.github/workflows/docker-publish.yml` which will:
- build and push `linux/amd64` images for backend, worker, and frontend
- tag images as:
  - `sha-<shortsha>` on every run
  - `latest` on pushes to `main`/`master`/`smartdrop-nvenc-clean`
  - `<git tag>` for tag builds like `v1.2.3`

Trigger it by pushing to one of those branches or by creating a release tag, or run it manually via the Actions tab.

## 4) Quick start (pull and run)
For users who just want to run the stack (no build needed):

```
# From repo root
# Ensure you have NVIDIA drivers + Docker GPU runtime for the worker
# Then run:

docker compose -f docker-compose.hub.yml up -d
```

- Backend: http://localhost:8000
- Frontend: http://localhost:5173

Environment defaults:
- `PUBLIC_BACKEND_URL` points to `http://localhost:8000`
- `REDIS_URL` uses the compose Redis service
- Auth defaults can be overridden in a `.env` file if desired

## Notes
- The worker image uses NVIDIA CUDA base and requires GPU access (Compose `gpus: all`). On Linux, install `nvidia-docker2` / NVIDIA Container Toolkit; on Windows + WSL2, ensure GPU support is enabled.
- Multi-arch (arm64) builds are not enabled by default due to CUDA base image constraints. The workflow targets `linux/amd64`.
