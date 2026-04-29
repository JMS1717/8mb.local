#!/usr/bin/env sh
# Docker entrypoint: set up NVIDIA environment, then launch supervisord.

log() {
  ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date)
  echo "[entrypoint] $ts $*"
}

# Ensure NVIDIA and Jellyfin FFmpeg library paths are available
for libdir in /usr/lib/jellyfin-ffmpeg/lib /usr/local/nvidia/lib64 /usr/local/nvidia/lib /usr/local/cuda/lib64 /usr/lib/wsl/lib; do
  if [ -d "$libdir" ]; then
    case ":${LD_LIBRARY_PATH:-}:" in
      *:"$libdir":*) ;;
      *) export LD_LIBRARY_PATH="${libdir}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ;;
    esac
  fi
done

log "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf "$@"
