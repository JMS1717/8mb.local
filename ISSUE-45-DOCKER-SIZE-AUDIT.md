# Issue #45 Docker Image Size Audit

Date: 2026-08-18

## Scope and source

This audit uses the issue #44 functional baseline without changing `main`:

- Branch: `agent/issue-45-docker-size`
- Source commit used for the final build and published image: `c04b850eec30879f160959c70c4ea2d06200dd35`
- Parent issue #44 test commit: `0138df78bf46e5e87d026f1f4708251b311cedcc`
- `main` reference at the start of the issue #44 work: `d991b907e1e47283dd0a2f4e251d7c4dc878896c`
- Initial prototype tag: `jms1717/8mblocal:issue45-slim-a`
- Pre-publish clean candidate tag: `jms1717/8mblocal:issue45-slim-clean`
- Final immutable tag: `jms1717/8mblocal:issue45-slim-c04b850`
- Final published digest: `sha256:af7c61dc4fd38e07febd461aefaac9128f5e17e22b3bc915490df1123c22140d`
- Final local image size: `501,348,738` bytes

The issue branch was pushed to GitHub. The immutable Docker Hub test tag was
published and pulled fresh for validation. The image was then deployed only to
the Powerhouse `8mblocal` Compose service after an isolated RAM test. No merge
to `main`, normal/latest Docker tag, GitHub release, or Partner Center change
was performed.

## Finding

The largest avoidable size cost was the final `nvidia/cuda:12.2.0-runtime-ubuntu22.04` base. It carried the complete CUDA runtime tree even though this application's FFmpeg binary only needs the NPP libraries used by `scale_npp`; NVIDIA driver libraries and NVENC/NVDEC are injected by the NVIDIA Container Toolkit when a container is started with GPU access.

The candidate keeps the CUDA development image only as a build stage, changes the final stage to plain Ubuntu 22.04, copies only the NPP libraries that the built FFmpeg links against, and strips the Intel iHD driver's build symbols. It retains the existing custom FFmpeg, Intel VAAPI/QSV stack, AMD VAAPI drivers, CPU encoders, NVIDIA NVENC/NVDEC support, and application code.

### Size measurements

The measurements below intentionally distinguish several Docker size concepts:

| Measurement | Issue #44 baseline | Issue #45 candidate |
|---|---:|---:|
| Live filesystem inside a container (`du -sx /`) | 2,739,316 KB | 1,018,776 KB |
| Docker image inspect `Size` | 1,515,092,082 bytes | 501,347,269 bytes |
| `docker system df` displayed image size | 4.33 GB | 1.55 GB |
| `docker system df` unique size | 1.16 GB | 1.546 GB |
| `docker save` archive | not recreated for baseline | 501,387,776 bytes |

The `docker system df` values include Docker's layer accounting and shared-layer state, so they should not be compared as a simple compressed registry size. The candidate's live root is approximately 1.02 GB and its image metadata/save size is approximately 501 MB. A registry push was intentionally not performed, so no registry digest or registry-compressed measurement is claimed.

The candidate removes these major final-image costs:

- CUDA runtime layer: about 1.73 GB removed.
- Unstripped Intel iHD driver: 377,913,392 bytes reduced to 37,343,048 bytes.
- Full CUDA library tree: not copied into the final image.
- Retained NPP components: `libnppc`, `libnppig`, `libnppicc`, `libnppidei`, and `libnppif`.

## Runtime stack audit

The shipped candidate still uses the repository-owned build:

- FFmpeg `n6.1.1`
- libva `2.21.0`
- Intel media-driver `24.1.5`
- GmmLib `22.3.18`
- oneVPL dispatcher `v2023.4.0`
- oneVPL GPU implementation from Ubuntu's `libmfx-gen1.2` package
- SVT-AV1 `v2.2.1`
- NVENC headers `sdk/12.1`
- Ubuntu 22.04 runtime packages for Python, Redis, CPU codecs, VAAPI, Mesa, Intel media runtime, and diagnostics

FFmpeg was built with:

```text
--enable-cuda-nvcc --enable-libnpp --enable-nvenc
--enable-libvpl --enable-vaapi --enable-libdrm
--enable-libx264 --enable-libx265 --enable-libvpx --enable-libopus
--enable-libaom --enable-libsvtav1 --enable-libdav1d
```

The candidate reported `cuda`, `vaapi`, `qsv`, and `drm` hardware methods and the expected QSV, VAAPI, NVENC, `libx264`, `libx265`, and `libsvtav1` encoders.

The final image does not contain the full `/usr/local/cuda-12.2` runtime tree. `LD_LIBRARY_PATH` remains compatible with NVIDIA Container Toolkit injection:

```text
/usr/local/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64
```

`LIBVA_DRIVERS_PATH` remains cross-vendor:

```text
/usr/local/lib/dri:/usr/lib/x86_64-linux-gnu/dri:/usr/lib/dri
```

The image contains both `radeonsi_drv_video.so` and `r600_drv_video.so` in the normal distro driver directory. It does not globally force `LIBVA_DRIVER_NAME=iHD`.

All checked FFmpeg, Intel iHD, Mesa VAAPI, and DRI driver dependencies passed `ldd` checks with no `not found` entries. This is a structural check, not proof of every vendor's hardware behavior.

## Architecture options

### A. Custom FFmpeg with a lean runtime — selected and prototyped

This is the candidate described above. It keeps one universal image and reduces the final image without changing the FFmpeg feature set. It passed the CPU and NVIDIA functional matrix and Intel H.264 tests.

### B. Cleaner repository-owned FFmpeg/media build

This would further reorganize the source-built Intel components and package only exact runtime artifacts. It is maintainable in principle, but the current stack has already passed real Intel QSV/VAAPI probes on the reporter's machine. Replacing the media stack would add risk without evidence of a runtime failure, so it was not done for issue #45.

### C. Another maintained FFmpeg distribution

No alternative distribution was selected. The project requires one image that preserves NVIDIA NVENC/NVDEC, CUDA scaling, Intel QSV, Intel/AMD VAAPI, and CPU fallback. The current repository build provides those features and Jellyfin FFmpeg is explicitly out of scope.

### D. Split images

Separate CPU, NVIDIA, and VAAPI images could reduce individual images further, but they would multiply packaging and support paths. The universal candidate is already close to the requested size target and passed the available hardware matrix, so splitting is not justified yet.

## Tests and evidence

### Repository tests

Run through the repository virtual environment:

- Root tests: `23 passed`
- Worker tests: `49 passed, 1 skipped`
- Backend API tests: `72 passed, 1 skipped`
- The combined multi-directory pytest invocation was not used for the final result because two directories contain the same test module basename; pytest reported an import-file mismatch during collection. Running each suite separately is the correct project-compatible invocation.
- Backend tests emitted existing FastAPI/Pydantic deprecation warnings; no test failed because of them.

### Compose and structural checks

These completed successfully:

```text
docker compose -f docker-compose.yml -f docker-compose.cpu.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.vaapi.yml config --quiet
ldd /usr/local/bin/ffmpeg          # no missing libraries
ldd /usr/local/lib/dri/iHD_drv_video.so  # no missing libraries
ldd all available DRI drivers     # no missing libraries
```

The candidate labels and application metadata agree:

```text
org.opencontainers.image.version = 141.0.0.0
org.opencontainers.image.revision = c04b850
/app/VERSION = 141.0.0.0
```

### Local CPU and NVIDIA tests

The existing E2E harness was run with exact encoder enforcement. The following all completed with the requested encoder as `actual_encoder`:

| Requested encoder | Result |
|---|---|
| `libx264` | PASS, `actual_encoder=libx264` |
| `libx265` | PASS, `actual_encoder=libx265` |
| `libsvtav1` | PASS, `actual_encoder=libsvtav1` |
| `h264_nvenc` | PASS, `actual_encoder=h264_nvenc` |
| `hevc_nvenc` | PASS, `actual_encoder=hevc_nvenc` |
| `av1_nvenc` | PASS, `actual_encoder=av1_nvenc` |

The E2E run also passed health, upload, output validation, history, repeated download, and restart recovery. A separate direct probe passed both CUDA decode (`h264_cuvid`) and `scale_npp` followed by NVENC.

The local Docker Desktop host reported an RTX 4070 Ti-class NVIDIA GPU through `nvidia-smi`. The candidate therefore proves that removing the CUDA runtime base did not break the available NVIDIA path when the container is started with `--gpus all`.

### Intel XPS test

The configured native SSH alias `xps` connected successfully to the Intel system. It reported:

- Host: `dell-xps13-2015-i5-8gb-256gb`
- User: `jms1717`
- `/dev/dri/renderD128`
- Native `vainfo`: Intel iHD driver 24.1.0
- Docker: 29.7.2

The candidate image was transferred to a unique test directory, hash-checked, loaded, and run with `/dev/dri` passed through.

Results:

- `h264_qsv`: PASS, exact final encoder.
- `h264_vaapi`: PASS, exact final encoder.
- Direct FFmpeg QSV H.264 probe: PASS; FFprobe reported `codec_name=h264`.
- Direct FFmpeg VAAPI H.264 probe: PASS; FFprobe reported `codec_name=h264`.
- Candidate `vainfo`: loaded the bundled iHD driver 24.1.5.
- `hevc_qsv`: not available on this 2015 Intel GPU; startup reported no usable HEVC encoding profile and the exact E2E correctly rejected the CPU fallback.
- `hevc_vaapi`: not available on this 2015 Intel GPU for the same hardware-profile reason; the exact E2E correctly rejected the CPU fallback.

This is an XPS hardware limitation, not an image failure. A newer Intel system is still required to prove HEVC QSV/VAAPI. The candidate did not turn an unsupported HEVC request into a false hardware pass.

The disposable XPS image, archive, E2E containers, and unique test directory were removed after testing. No existing XPS application directory or image was changed.

### 10th-generation Intel acceptance test

The clean candidate was saved, SHA-256 checked, and transferred to the configured
`10thGenLaptop` host in the unique directory
`/home/jms1717/8mb-local-codex-test/issue45-clean-20260818`. The archive hash was
`CF947B2C2726BA0F100147EAED6C05D5F703C5EAC5414FABE6D08DDE018D3530` locally and
the same hash was verified after transfer. Docker loaded the image without
modification.

The extended application E2E was run with:

```text
python3 scripts/e2e_test.py --mode docker \
  --docker-image jms1717/8mblocal:issue45-slim-clean \
  --docker-gpu vaapi --profile extended \
  --codecs h264_qsv,hevc_qsv,h264_vaapi,hevc_vaapi \
  --require-exact-codecs --skip-batch --timeout 180
```

Results:

| Requested encoder | Result |
|---|---|
| `h264_qsv` | PASS, exact final encoder |
| `hevc_qsv` | PASS, exact final encoder |
| `h264_vaapi` | PASS, exact final encoder |
| `hevc_vaapi` | PASS, exact final encoder |

The same run passed health, upload/analyze, output creation, download, FFprobe
validation, invalid-file rejection and cleanup, SSE terminal replay, active
cancellation, and restart/history recovery. The worker reported
`type=intel_qsv` and `/dev/dri/renderD128`; application results were exact, so
CPU fallback could not masquerade as hardware success. `av1_qsv` and
`av1_vaapi` were rejected by the UHD 620 hardware profile, while `libsvtav1`
remained available as the CPU fallback. No host application directory or
existing container was changed.

### Published-image Intel validation

The final immutable image was pulled directly on the 10th-generation Intel
host before deployment to Powerhouse. Its local inspection reported the same
registry digest:

```text
jms1717/8mblocal:issue45-slim-c04b850
sha256:af7c61dc4fd38e07febd461aefaac9128f5e17e22b3bc915490df1123c22140d
size=501,348,738 bytes
```

The extended exact-codec E2E against that pulled image passed:

| Requested encoder | Actual encoder | Result |
|---|---|---|
| `h264_qsv` | `h264_qsv` | PASS |
| `hevc_qsv` | `hevc_qsv` | PASS |
| `h264_vaapi` | `h264_vaapi` | PASS |
| `hevc_vaapi` | `hevc_vaapi` | PASS |

The same run passed edge cases, invalid-file rejection and cleanup, SSE
terminal replay, active cancellation, history/download recovery, FFprobe
validation, and restart recovery. The command used
`--require-exact-codecs --skip-batch`; the harness summary's generic wording
mentioned a batch scenario, but batch was explicitly skipped and is not
claimed as part of this run.

The first Powerhouse RAM probe used `/api/health` and therefore stopped before
upload because the actual endpoint is `/healthz`; this was a harness error, not
an application failure. The corrected probe passed and its uniquely named
resources were removed.

### Powerhouse deployment and RAM validation

Read-only production inspection found:

- Host: `powerhouse`; project directory: `/home/powerhouse/Docker/8mblocal`.
- Production service/container: `8mblocal`, port 8001, NVIDIA GPU request,
  persistent binds for `uploads`, `outputs`, and `.env`.
- Before replacement: `jms1717/8mblocal:v141`, image ID
  `sha256:e081be23957560969474841d90210055a41925c6e7facb9dc7e5c1990e0cdfaf`.
- Before replacement: healthy, no running FFmpeg process, and zero running
  compression jobs.
- Host GPU: Quadro RTX 4000, driver `535.261.03`.
- Host memory at inspection: 94 GiB total, 22 GiB available; root disk had
  338 GiB free.

Before changing the service, the exact Compose file and `.env` were backed up
under:

```text
/home/powerhouse/Docker/8mblocal/.codex-issue45-backup-20260818-1442
```

The Compose image reference was changed only for the `8mblocal` service to the
immutable issue-45 tag. The existing dynamic RAM configuration was retained:
`MEDIA_STORAGE=auto`, `MEDIA_MEMORY_LIMIT_GB=10`, and a 10 GiB Docker shared
memory mount. The service was recreated at `2026-08-18T14:41:16Z` and was
healthy at final verification. The observed recreate window was about 6
seconds; `/healthz` returned `{"ok":true}` afterward.

The isolated RAM test used the published image with a unique localhost-only
port and temporary binds. It proved:

- `/dev/shm` was a `tmpfs` mount with a 10 GiB ceiling.
- The application selected `/dev/shm/8mb.local/uploads` in explicit memory
  mode.
- Upload staging appeared at that path.
- Exact `h264_nvenc` compression completed with
  `hardware_used=true`, `fallback_occurred=false`, and
  `actual_encoder=h264_nvenc`.
- FFprobe validated the output.
- The transient source was absent from both `/dev/shm` and disk upload paths
  immediately after success.

The production smoke test then repeated the same proof with `MEDIA_STORAGE=auto`
on the live service. A synthetic 8-second video staged in `/dev/shm`, completed
as exact `h264_nvenc`, produced a valid MP4, and had its transient source
removed. The test output and its single test history row were deleted by exact
path/task ID afterward. Final production verification showed the new immutable
image running and healthy, unchanged port 8001, the same GPU request and
persistent mounts, and no uniquely named test container or temporary test
directory remaining. The unrelated-container inventory remained 34 running
services before/after this deployment check; no unrelated service was
restarted or modified.

## Files changed

- `Dockerfile`
  - Strip the installed Intel iHD runtime driver.
  - Use plain Ubuntu 22.04 for the final stage.
  - Copy only the NPP libraries directly required by FFmpeg's CUDA scaling path.
- `ISSUE-45-DOCKER-SIZE-AUDIT.md`
  - This audit, measurements, test evidence, and recommendation.

No application source, Windows packaging, Compose, or media-stack behavior was changed beyond the runtime-image packaging changes above.

## Recommendation and remaining work

The lean universal runtime is a strong issue #45 candidate and is functionally validated for CPU, NVIDIA NVENC/NVDEC/CUDA scaling, Intel H.264/HEVC QSV/VAAPI on a 10th-generation Intel host, and cross-vendor driver discovery. It should remain on this issue branch until reviewed.

Before merging or publishing a normal tag:

1. Build the candidate on a clean Linux builder as a reproducibility check.
2. Run an exact AMD VAAPI encode on an AMD host; the image currently passes structural AMD-driver checks only.
3. Confirm the normal release workflow records the smaller candidate's image size and does not require the old CUDA runtime base.
4. Decide whether to clarify the local-only `jms1717/8mblocal:vaapi` Compose image tag in a separate documentation change.

The issue-45 image is ready for OneCreek/maintainer review and the current
Powerhouse deployment is healthy. Intel H.264/HEVC QSV/VAAPI and NVIDIA
H.264/HEVC real application paths are proven on the available hosts. AMD
hardware remains the only unexecuted hardware proof in this cycle; no claim is
made that an AMD encode passed.
