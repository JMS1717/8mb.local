# Configuration & Tuning

8mb.local maintains highly flexible configuration matrices, adjustable either globally via Docker environment variables or locally in real-time via the integrated Settings UI.

---

### Environment Variables

Create a `.env` file in the root of your deployment and mount it at `/app/.env` within your `docker-compose.yml`:

```env
# Authentication (also configurable dynamically via Settings UI)
AUTH_ENABLED=false
AUTH_USER=admin
AUTH_PASS=changeme

# Data lifecycle
FILE_RETENTION_HOURS=1

# Worker concurrency (max parallel FFmpeg jobs)
# (Needs Container Restart if modified via ENV or UI)
WORKER_CONCURRENCY=4

# Codec visibility flags (all default to true)
CODEC_H264_NVENC=true
CODEC_HEVC_NVENC=true
CODEC_AV1_NVENC=true
CODEC_LIBX264=true
CODEC_LIBX265=true
CODEC_LIBAOM_AV1=true

# Batch thresholds
MAX_BATCH_FILES=200
BATCH_METADATA_TTL_HOURS=24
MAX_UPLOAD_SIZE_MB=51200

# Architecture bounds (advanced usage)
REDIS_URL=redis://127.0.0.1:6379/0
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8001
DAEMON_PORT=8000
```

---

### In-App Settings Panel (`/settings`)

The vast majority of variables can be manipulated in real-time. Navigate to the `Settings` module across your web browser interface:

- **System Parameters**: Enable or disable Basic Auth globally.
- **Hardware Targets**: Override the number of concurrent `WORKER_CONCURRENCY` encoding assignments depending on your GPU's ceiling scale. Note: Changing concurrency via the UI forces a background container `SIGHUP` and soft restart in order to scale the subprocess Celery daemons accurately!
- **Component Toggles**: Clean up the UI explicitly by hiding unusable encoders.
- **Compression Logic**: Map default profiles and construct universal fallbacks that seamlessly execute for quick "drag and drop" events on the root homepage.
- **Size Matrix Definitions**: Manually restructure the grid buttons to output highly specific target bounds (e.g. `20MB`, `90MB`) for platform-specific targets like standard Telegram/Discord sizes.
