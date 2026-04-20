#!/usr/bin/env sh
# Docker entrypoint: set up NVIDIA environment, then launch supervisord.

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
