# Issue #44 hardware-acceleration audit

Date: 2026-08-17
Source: local checkout `main` at `d991b907e1e47283dd0a2f4e251d7c4dc878896c`
Application version: `141.0.0.0`
Production and GitHub were not changed.

## Bottom line

The reporter's Intel startup evidence is consistent with the current image: the
Intel runtime was not replaced. The highest-confidence defects were in state
handling and reporting, not proof that the Intel media stack could not encode.

Fixed in this checkout:

- The frontend no longer labels every non-NVENC encoder as CPU. QSV, VAAPI,
  AMF, and VideoToolbox use one shared classifier.
- Real jobs now carry requested, resolved, actual, hardware, device, and
  fallback telemetry through the worker, status API, queue metadata, SSE done
  event, and UI.
- Cancellation now works while waiting for the adaptive encode slot and during
  preparation, probing, encoding, retries, and finalization.
- A worker cancellation is reported as one `CANCELED` terminal state. It no
  longer writes malformed `REVOKED` metadata that caused later status requests
  to return HTTP 500 or emits cancellation followed by generic failure.
- Hardware cache refresh now rediscoveries devices and replaces the encoder
  probe cache instead of merging stale keys. The forked Celery child lock is
  recreated, fixing a real startup-probe/Celery-prefork deadlock.
- A restored browser `activeJobId` is checked against durable status and cleared
  when the job is missing or terminal.
- Upload XHRs settle exactly once and have long, large-file-safe timeouts with
  explicit upload-complete handling.
- VAAPI driver discovery retains Intel, AMD Mesa, and other standard driver
  directories instead of forcing an Intel-only path.

## Root causes

### Confirmed

1. `frontend/src/routes/+page.svelte` previously used an `_nvenc`-only test for
   the encoder badge. Thus `hevc_qsv` and `hevc_vaapi` were displayed as CPU
   even when the pipeline used hardware. The shared helper in
   `frontend/src/lib/codecs.ts` fixes this.
2. `AdaptiveConcurrencyGate.acquire()` could wait indefinitely without checking
   cancellation. The task wrapper acquired the gate before entering the code
   that polled the cancellation flag. This created a genuine queued/waiting
   cancellation deadlock.
3. Active cancellation wrote a plain dictionary into Celery's `REVOKED` result
   slot. Celery later tried to decode that dictionary as exception metadata and
   raised `ValueError: Exception information must include the exception type`.
4. The worker hardware cache and encoder cache could retain stale results after
   a manual rerun. A forked Celery child could also inherit a locked cache
   mutex while the startup probe thread was running. The local Docker E2E first
   reproduced this as jobs stuck in PENDING; the lock-scope/fork-reset fix made
   the same E2E pass.
5. The browser restored an `activeJobId` without first asking whether the job
   still existed after a Redis/container restart. Upload XHR terminal cases did
   not all settle explicitly.

### Strongly suspected or requiring newer Intel hardware

The current XPS is a Broadwell Intel HD 5500. It has a working render node and
real H.264 QSV/VAAPI encoding, but its VAAPI profile does not expose HEVC
encoding. Exact HEVC QSV and VAAPI application tests therefore correctly fall
back to `libx265` on this host. This is a hardware capability limitation, not a
failure of the application fallback path.

OneCreek's Debian logs remain the required external proof for exact HEVC QSV
and VAAPI on the reporter's newer Intel hardware. The prepared command is:

```text
python scripts/e2e_test.py --mode docker --docker-image <image> --docker-gpu vaapi --profile quick --codecs hevc_qsv,hevc_vaapi --require-exact-codecs --skip-edge-cases --skip-batch --timeout 180
```

CPU fallback must fail that exact-codec test; a successful fallback is not
hardware proof.

### Unrelated/maintainability findings

The image still uses the existing pinned FFmpeg/libva/GmmLib/media-driver/
oneVPL arrangement. It was not broadly migrated because the reporter's real
startup probes already prove that this stack can initialize H.264/HEVC QSV and
VAAPI on supported Intel hardware. The only media-stack change is preserving
standard AMD/Mesa VAAPI driver discovery alongside the source-built Intel
driver.

## Files changed

- `Dockerfile`: retain AMD/Mesa VAAPI driver discovery and clarify the pinned
  dispatcher comment.
- `worker/app/hw_detect.py`: force-refresh support, generation/timestamp data,
  cross-vendor VAAPI paths, cache locking, and fork-safe lock reset.
- `worker/app/startup_tests.py`: generation-tagged detailed probe results.
- `worker/app/worker.py`: replace, rather than merge, probe-cache snapshots.
- `worker/app/tasks.py`: cancellation checkpoints, exact FFmpeg process-group
  termination, one cancellation terminal state, unified GPU environment, and
  encoder telemetry.
- `shared/concurrency.py`: cancellable local and Redis gate waits.
- `shared/local_runtime.py`: local-runtime CANCELED state and telemetry.
- `backend-api/app/celery_app.py`: local-runtime CANCELED handling.
- `backend-api/app/deps.py`: forced worker refresh and CANCELED batch mapping.
- `backend-api/app/models.py`: status and queue telemetry fields/phases.
- `backend-api/app/routers/compress.py`: queue-state telemetry and CANCELED
  mapping.
- `backend-api/app/routers/download.py`: telemetry in status responses and
  safe handling of stale/corrupt canceled results.
- `backend-api/app/routers/stream.py`: CANCELED SSE terminal event.
- `backend-api/app/routers/system.py`: fresh probe-result reporting and DRI
  vendor diagnostics.
- `frontend/src/lib/api.ts`: upload/batch XHR timeout and settle-once logic.
- `frontend/src/lib/codecs.ts`: canonical encoder classification helper.
- `frontend/src/lib/sse.ts`, `frontend/src/lib/types.ts`: canceled phase and
  telemetry types.
- `frontend/src/routes/+page.svelte`: truthful encoder badge/telemetry,
  fallback display, stale-job recovery, and upload-state messaging.
- `backend-api/tests/test_download_paths.py`: status telemetry and malformed
  canceled-result regression coverage.
- `backend-api/tests/test_encoder_test_reporting.py`: stale-result and fresh
  probe-result coverage.
- `tests/test_concurrency_cancellation.py`: gate cancellation coverage.
- `tests/test_frontend_encoder_badge.py`: canonical hardware badge coverage.
- `tests/test_media_stack_configuration.py`: runtime/driver-path checks.
- `worker/tests/test_cancellation_lifecycle.py`: durable canceled-state and
  transient-input cleanup coverage.
- `worker/tests/test_hw_cache_fork_safety.py`: prefork lock regression test.
- `worker/tests/test_hw_refresh.py`: forced refresh and cache replacement tests.

## Validation evidence

### Local source and frontend

- `npm run check`: PASS — 0 errors, 0 warnings.
- `npm run build`: PASS — SvelteKit production build completed.
- `git diff --check`: PASS. Git only reported normal CRLF conversion warnings.
- Current source snapshot: `test-evidence/issue44-final/8mb-local-source-20260817-final.zip`
- Snapshot SHA-256:
  `886CCB796219C31548E2128EA2FBC808487F1A41118A017127CA1E070367D2A2`
- Snapshot was copied to XPS, hash matched, and extraction was verified.

### Automated Python tests

Run in the rebuilt Docker image with the current source mounted:

```text
python3 -m pytest -q --import-mode=importlib
```

Result: **153 passed, 2 skipped, 5 deprecation warnings**.

The two skips are platform-dependent fork coverage on Windows. The warnings
are existing Pydantic/FastAPI lifecycle deprecations; they are not test
failures.

The same targeted tests were run on Windows where import dependencies were not
installed natively; the authoritative full run was performed in the project
Docker environment. A direct XPS native pytest attempt was not runnable because
that host has no pytest package; this is an environment limitation, not a
reported application failure.

### Local Docker build and E2E

Build:

```text
docker build --progress=plain -t jms1717/8mblocal:issue44-final .
```

Result: PASS. Image ID:
`sha256:d0ba9d5bade4330661105029c627c7115e6b893d4a0c144ab12aa27ffe42a5c5`
Size: 1,515,091,346 bytes. The image embeds version `141.0.0.0`.

Structural checks passed:

- FFmpeg 6.1.1 starts.
- QSV, VAAPI, NVENC, CPU, and SVT-AV1 encoders are compiled in.
- `ldd /usr/local/bin/ffmpeg` has no missing libraries.
- `ldd /usr/local/lib/dri/iHD_drv_video.so` has no missing libraries.
- `iHD_drv_video.so`, `radeonsi_drv_video.so`, and the other expected driver
  paths are discoverable.

The rebuilt image passed:

```text
python scripts/e2e_test.py --mode docker --docker-image jms1717/8mblocal:issue44-final --profile quick --codecs libx264 --skip-edge-cases --skip-batch --timeout 120
```

Evidence: health, upload, compression, valid MP4/FFprobe output, history,
repeated download, and restart recovery all passed. Earlier extended local E2E
also passed invalid-input cleanup, SSE reconnect replay, active cancellation,
edge media, and batch coverage.

Live Docker cancellation reproduced the previous failure before the fix and
then reached durable `CANCELED` without the HTTP 500 after the fix. The gate
unit test proves a waiting task cancels without acquiring or leaking a slot.

### XPS Intel hardware

Host: `dell-xps13-2015-i5-8gb-256gb`, Pop!_OS 24.04, Intel HD Graphics 5500,
`/dev/dri/renderD128`, iHD 24.1.0. No production system was touched.

Real application-level exact tests using the current source mounted into an
isolated container:

- `h264_qsv`: PASS; final encoder exactly `h264_qsv`.
- `h264_vaapi`: PASS; final encoder exactly `h264_vaapi`.
- `hevc_qsv`: exact test correctly failed because the XPS hardware does not
  expose a usable HEVC encode profile; application fell back to `libx265`.
- `hevc_vaapi`: exact test correctly failed for the same hardware capability;
  application fell back to `libx265`.

Direct host probes agreed: H.264 QSV and VAAPI passed; HEVC QSV/VAAPI returned
FFmpeg/VAAPI unsupported-profile errors. This does not prove or disprove
OneCreek's newer Intel HEVC result.

The final snapshot was transferred to XPS and verified with the same SHA-256.
The isolated Docker test containers were removed; `docker ps -a --filter
name=issue44` showed no remaining test container. The source and logs remain
under the unique XPS test directory for evidence.

## Remaining work

### Follow-up: transient input recovery after terminal jobs

OneCreek found a separate frontend lifecycle issue after canceling a job:
the worker correctly deleted the transient staged input, but the page kept the
old analysis and tried to submit it again. The API correctly returned
`Input not found`.

The fix keeps the browser's selected `File` and analysis metadata, but tracks
the server-side staged input explicitly with the upload identity and a valid
flag. Terminal success, failure, and cancellation invalidate that staged state
without weakening cleanup. The next Compress action re-uploads and analyzes
the retained browser file automatically. A stale `Input not found` response
has one bounded recovery attempt; upload or retry failures stop with a clear
error and cannot loop indefinitely. Reset and selecting a new file also clear
the staged state.

Files changed for this follow-up:

- `frontend/src/routes/+page.svelte`: explicit staged-input lifecycle,
  automatic re-stage before retrying compression, one-shot stale-input
  recovery, and terminal-state invalidation.
- `tests/test_frontend_input_lifecycle.py`: regression checks for the staged
  input contract, terminal invalidation, bounded recovery, and reset behavior.

Validation for this follow-up:

- `frontend`: `npm run check` — PASS, 0 errors and 0 warnings.
- `frontend`: `npm run build` — PASS.
- Static frontend lifecycle and encoder badge tests — PASS, 7 tests.
- Full Python suite inside the project container — PASS, 158 passed, 2
  skipped.
- Docker quick E2E with the rebuilt local image — PASS, including health,
  upload, compression, FFprobe-valid output, history/download, and restart
  recovery.

The Intel/QSV/VAAPI media stack was not changed for this follow-up.

1. OneCreek or another newer Intel system must run the exact HEVC QSV and
   HEVC VAAPI application test with `--require-exact-codecs`. The expected
   terminal metadata must show `actual_encoder=hevc_qsv` and
   `actual_encoder=hevc_vaapi`; CPU fallback does not count.
2. The interrupted XPS full Dockerfile rebuild was intentionally stopped after
   compiling 28% of the pinned Intel media driver because the local rebuilt
   image and the isolated current-source application tests already supplied
   usable evidence. It did not fail; it was a long source-build test that was
   not needed to validate the Broadwell limitation.
3. Windows EXE/installer/MSIX are outside this issue audit and were not rebuilt
   here.

At the time of the original hardware audit, no commit, push, merge, release,
Docker publish, Partner Center change, or production deployment was performed.
