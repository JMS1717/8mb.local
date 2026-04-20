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

# Dynamic defaults for CPU encoding
if [ -z "$WORKER_CONCURRENCY" ]; then
  # On Apple Silicon 8-12 cores is common, leave some breathing room
  cores=$(nproc)
  export WORKER_CONCURRENCY=$(( cores > 2 ? cores - 1 : 1 ))
  log "Set WORKER_CONCURRENCY to $WORKER_CONCURRENCY based on $cores CPU cores"
fi

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf "$@"
