#!/usr/bin/env sh
# Docker entrypoint: set up GPU/temp-file environment, then launch supervisord.

log() {
  ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date)
  echo "[entrypoint] $ts $*"
}

# Ensure NVIDIA library paths are available
for libdir in /usr/local/nvidia/lib64 /usr/local/nvidia/lib /usr/local/cuda/lib64 /usr/lib/wsl/lib; do
  if [ -d "$libdir" ]; then
    case ":${LD_LIBRARY_PATH:-}:" in
      *:"$libdir":*) ;;
      *) export LD_LIBRARY_PATH="${libdir}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ;;
    esac
  fi
done

log "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"

# Keep multipart uploads and FFmpeg temporary files on the mounted upload
# volume. Otherwise large batch uploads can fill Docker's writable layer and
# the host root filesystem before the application limit is reached.
export TMPDIR="${TMPDIR:-/app/uploads/.tmp}"
mkdir -p "$TMPDIR" /app/uploads /app/outputs /app/state /var/lib/redis 2>/dev/null || true
log "TMPDIR=$TMPDIR"

# Resolve the initial worker-pool ceiling before supervisord expands the
# worker command. Keep WORKER_CONCURRENCY=auto intact so the worker's adaptive
# gate can continue to react to live VRAM/RAM after startup.
if [ "${WORKER_CONCURRENCY:-auto}" = "auto" ]; then
  AUTO_WORKERS="$(PYTHONPATH=/app python3 -c 'from shared.concurrency import resolve_worker_concurrency; print(resolve_worker_concurrency("auto"))' 2>/dev/null || true)"
  case "$AUTO_WORKERS" in
    ''|*[!0-9]*) log "Automatic worker selection unavailable; using one worker pool slot"; export WORKER_POOL_CONCURRENCY=1 ;;
    *) export WORKER_POOL_CONCURRENCY="$AUTO_WORKERS"; log "Automatic worker pool ceiling=$WORKER_POOL_CONCURRENCY; live gate remains adaptive" ;;
  esac
else
  export WORKER_POOL_CONCURRENCY="${WORKER_CONCURRENCY}"
fi

# ----------------------------------------------------------------------------
# .env sanity check
# ----------------------------------------------------------------------------
# Docker's short-form bind mount (`./.env:/app/.env`) silently creates a host
# directory named `.env` if the file is missing, then mounts it as a directory
# inside the container. The Python settings_manager expects a FILE and will
# silently degrade to env-var-only mode, losing all persisted settings.
#
# Detect, log loudly, and try to recover (only if the directory is empty).
ENV_PATH="/app/.env"
if [ -d "$ENV_PATH" ]; then
  log "WARNING: $ENV_PATH is a directory (likely Docker auto-created it when"
  log "         the host-side ./.env file was missing). Attempting recovery…"
  if [ -z "$(ls -A "$ENV_PATH" 2>/dev/null)" ]; then
    rmdir "$ENV_PATH" 2>/dev/null && log "  removed empty $ENV_PATH directory"
    touch "$ENV_PATH" 2>/dev/null && log "  created empty $ENV_PATH file"
  else
    log "  $ENV_PATH contains files; leaving as-is. Fix on the HOST by:"
    log "    1. docker compose down"
    log "    2. rm -rf ./.env && touch .env"
    log "    3. docker compose up -d"
  fi
elif [ ! -e "$ENV_PATH" ]; then
  touch "$ENV_PATH" 2>/dev/null && log "created empty $ENV_PATH file for settings persistence"
fi

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf "$@"
