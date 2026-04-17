## Goal
Help AI coding agents be immediately productive in this repository by summarizing the architecture, developer workflows, important files, and project-specific conventions.

## Big picture (quick)
- Frontend: `frontend/` — SvelteKit app (Vite). UI uses SSE for live progress and calls backend APIs.
- Backend API: `backend-api/app/` — FastAPI (`main.py` mounts routers). Uploads, `ffprobe`, Celery enqueue, downloads live under `routers/`.
- Worker: `worker/app/` — Celery worker that runs `ffmpeg` encodes. Core encode flow: `tasks.py`; helpers: `encoder.py`, `hw_detect.py`, `progress.py`, `startup_tests.py`. **NVIDIA NVENC + CPU only** — Intel QSV / VAAPI / AMD AMF were removed; they did not work reliably with strict target-bitrate control in testing.
- Broker / runtime: Redis (broker + pub/sub). Files stored under `uploads/` and `outputs/`.
- Orchestration: `docker-compose.yml` (GPU by default), `docker-compose.cpu.yml` (CPU-only fallback), and `supervisord.conf` show start commands and ENV patterns.

## Where to look first (files that reveal behavior)
- `README.md` — high-level architecture, NVIDIA + CPU workflows, and Docker examples.
- `docs/GPU_SUPPORT.md` — why only NVENC/CPU (legacy Intel/AMD paths removed; they did not work reliably for strict rate control).
- `supervisord.conf` — exact commands used in container images for `uvicorn` and Celery worker (very useful for reproducing environment variables for GPU support).
- `docker-compose.yml` — GPU by default (`gpus: all`); `docker-compose.cpu.yml` when NVIDIA passthrough is unavailable.
- `backend-api/app/main.py` — app creation, static SPA, router mounts; per-route logic in `backend-api/app/routers/`.
- `backend-api/app/deps.py` — shared paths, Redis helpers, batch/job utilities.
- `backend-api/app/config.py` — canonical environment variables and `.env` usage.
- `worker/app/tasks.py` — Celery `compress_video` task, FFmpeg invocation, progress publish format (`type`: `log`, `progress`, `done`, `error`).
- `worker/app/utils.py`, `worker/app/hw_detect.py`, `worker/app/startup_tests.py` — NVIDIA-oriented detection and startup tests.
- `frontend/` — SvelteKit source and `package.json` scripts (`dev`, `build`, `preview`).

## Developer workflows (practical commands & tips)
- Local quick run (Docker Compose): `docker compose up` (GPU by default; use `docker compose -f docker-compose.cpu.yml up` without NVIDIA). Use `--build` if you changed Python code in images.
- Backend dev (without Docker): from project root run the same command seen in `supervisord.conf` for parity:
  - `uvicorn backend.main:app --host 0.0.0.0 --port 8001` (ensure `PYTHONPATH=/path/to/backend-api/app` or run from `backend-api` with `PYTHONPATH=/app`).
- Worker dev: start Celery similar to supervisord:
  - `celery -A worker.celery_app worker --loglevel=info -n 8mblocal@%h --concurrency=4` (set `REDIS_URL` env). Worker code triggers startup encoder tests automatically — they run in a background thread.
- Frontend dev: `cd frontend && npm install && npm run dev` (uses Vite); `npm run build` for production bundles.

## Important conventions and patterns
- Job/task IDs: backend generates `job_id` and worker uses Celery `task_id`. Redis keys: `job:{task.id}`, `progress:{task_id}` and `cancel:{task_id}`. Use these exact keys when integrating or debugging.
- File naming: uploads saved to `/app/uploads` with `jobid_filename`; outputs to `/app/outputs` with `_8mblocal_{taskid}` suffix to avoid collisions.
- Hardware detection vs tests: hardware is detected (`worker/app/hw_detect.py`) and then validated by background startup tests. Encoders may be listed by ffmpeg but fail initialization — the startup cache (`ENCODER_TEST_CACHE`) and `DISABLE_STARTUP_TESTS` env control behavior.
- Encoder mapping: requested codec → mapped encoder happens in `worker/app/hw_detect.py` and `map_codec_to_hw`. When a startup test marks an encoder unavailable, worker falls back to CPU encoders (e.g. `libx264`).
- Progress messages: worker publishes JSON events on Redis pub/sub. Messages include `type` (`log`/`progress`/`done`/`error`) and often `task_id` and `progress` fields. The frontend expects these shapes.

## Tests and validation
- Unit tests live in `tests/` (e.g., `test_auto_resolution.py`, `test_hw_detect.py`). Run with pytest from repo root: `pytest -q`.
- Quick validation: run `ffmpeg -hide_banner -encoders | grep -i nvenc` inside container to check available encoders; `docker exec` into running container or run locally in image built for CI.

## Common pitfalls & how agents should handle them
- Do not assume a listed NVENC encoder will initialize successfully; prefer reading the startup test cache or respecting `ENCODER_TEST_CACHE` logic in `worker/app/tasks.py` / startup tests.
- When editing worker or backend code, ensure you preserve Redis key names and published event formats.
- Changing `WORKER_CONCURRENCY` or GPU flags requires container/service restart — documented in README and used by `supervisord.conf`.

## Quick examples agents can use
- Read the API flow: `backend-api/app/routers/compress.py` (and `upload.py`) → `celery_app.send_task('worker.worker.compress_video', ...)` for task kwargs and output naming.
- To simulate a publish event when writing tests or new features, publish JSON to channel `progress:<task_id>` and use `type` keys consistent with `worker/app/tasks.py` (e.g. `{"type":"log","message":"..."}`).

If anything here is unclear or you'd like more examples (SSE message shapes, ffmpeg command snippets, or a small local dev script), tell me which area to expand and I'll iterate.

## Agent automation & run policy
When an automated agent (or you) needs to run or refresh the running service locally, follow these rules exactly:

- Build the Docker images using cache (do not use --no-cache unless explicitly asked):

  ```powershell
  docker-compose build
  ```

- Stop and remove the old container for the `8mblocal` service, then start the new one:

  ```powershell
  # stop (if running)
  docker-compose stop 8mblocal
  # remove the stopped container (force if needed)
  docker-compose rm -f 8mblocal
  # run the service in detached mode
  docker-compose up -d 8mblocal
  ```

- If you prefer to rebuild and run in one step (still using cache):

  ```powershell
  docker-compose build && docker-compose up -d 8mblocal
  ```

- After the new container is running, run tests when needed (unit tests live under `tests/`):

  ```powershell
  pytest -q
  ```

- Safety / permissions:
  - Do not push any commits or create PRs on the repository without explicit user approval.
  - If a change will be pushed, always ask the user for confirmation and show the diff summary first.

Follow these steps only when the change requires running the full stack (e.g., worker/backend changes, container runtime/env updates, or encoder library updates). For small edits (documentation, single-file refactors) prefer local tests only and avoid rebuilding the container unless necessary.
