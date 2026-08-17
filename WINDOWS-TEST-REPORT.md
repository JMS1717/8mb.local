# 8mb.local Windows and Local Release Test Report

## Scope and source identity

This report covers the newest GitHub `main` source pulled into the local test
copy, plus the selected fixes and local release tooling ported from the older
working copy. No commit, push, merge, GitHub release, Docker push, or Partner
Center change was performed. A separately recorded, explicitly authorized
Powerhouse container replacement was performed for production smoke testing.

- Repository: `https://github.com/JMS1717/8mb.local`
- Local test copy: `C:\Users\jdude\Documents\Codex\2026-08-12\continue-the-8mb-local-windows-and\latest-github-20260814`
- Branch: `main`
- GitHub `main` and local base commit: `d46835817fbade646db2d51cba04a709a7e68c15`
- Base commit: `Delete PRIVACY.md` (2026-08-10)
- Requested/application version: `140.0.0.0`
- Worktree: dirty by design; it contains the uncommitted release system and
  application fixes listed below.
- Tracked files modified: 34
- Relevant untracked source files added: 18

The live GitHub check used `git ls-remote --symref` and `git ls-remote` for
`HEAD` and `refs/heads/main`; both resolved to the commit above. The current
working copy is therefore based on the newest verified GitHub default branch,
but is intentionally not a pristine clone after the local changes.

## Files reviewed

Reviewed the README and release/build/test scripts; frontend routes and shared
codec/API modules; backend API, settings manager, authentication, upload,
compression, download, stream, and system routers; worker tasks, encoder, and
hardware detection; shared runtime code; requirements and npm lockfiles;
Dockerfile and all three Compose files; Windows PyInstaller, FFmpeg, installer,
smoke-test, branding, and MSIX scripts; the MSIX manifest template; and all
GitHub Actions workflows.

The current GitHub batch screen, batch API, parallel dispatch tests, pending
batch state, modular encoder routing, NVENC/QSV/AMF/VAAPI support, and CPU
fallback code were preserved. The older v139 tree was used only for targeted
behavioral fixes; whole frontend/backend directories were not copied.

## Changes made

- Added root `VERSION` as the single full Windows-compatible version source.
- Added `scripts/set-version.ps1` and `scripts/check-version.ps1`.
- Added generated frontend and backend version modules so the UI and API report
  the same version.
- Added `release-local.ps1` with test, Windows, MSIX, Docker, checksum, manifest,
  and report stages; protected output-directory reuse; explicit skip flags; and
  no publishing/deployment behavior.
- Added `tests/test_version_consistency.py` and regression tests for codec
  visibility, CPU fallback protection, profile FPS preservation, and cooperative
  cancellation behavior.
- Preserved user codec visibility choices during hardware detection and kept
  automatic codec selection limited to actually detected and visible codecs.
- Preserved saved profile FPS caps when defaults are saved.
- Prevented users from disabling every supported CPU fallback and returned a
  validation response for that invalid request.
- Made deleting the selected default profile immediately select the persisted
  replacement in the frontend.
- Used cooperative cancellation (`terminate=False`) in the affected request
  paths so worker cleanup can run.
- Removed the Store-forbidden MSIX virtualization capabilities while retaining
  the single required `runFullTrust` capability for the packaged Win32 app.
- Made the Windows build reuse a complete local `.venv` when available, while
  retaining the isolated temporary-venv fallback on clean machines.
- Fixed the Windows helper's PowerShell smoke-test invocation.
- Updated the smoke test to authenticate the frontend shell and to test the
  current direct-Basic-auth SSE route; the outdated SSE-token endpoint was not
  present in the newest GitHub API.
- Added a release-gated settings smoke stage for codec visibility, available
  codec filtering, profile selection/editing/deletion, FPS-cap preservation,
  history, size buttons, retention, and worker-concurrency round trips. The
  stage restores temporary values before the smoke process exits.
- Made release smoke authentication use a per-run synthetic password held only
  in child-process environment variables; it is not written to logs or files.
- Updated Docker labels, environment metadata, Compose version fallbacks,
  workflows, and README documentation to use the shared version workflow.
- Changed fresh-install and untouched-stock migration defaults to add the
  `Discord 19.7 MB` H.264/Opus profile and a 19.7 MB quick-select button while
  preserving customized profiles, buttons, and explicit defaults. The 9.7 MB
  option remains available.
- Added Folder Watch configuration and persistence under Settings. It validates
  existing absolute readable/writable folders, supports Windows/UNC/Linux
  mounted paths, polling, stable-file detection, recursive scanning,
  new-only/process-existing, profile selection, output location, and
  keep/delete/move-after-validated-output behavior. It dispatches the existing
  Celery compression task and records bounded per-file state to avoid duplicate
  discovery.
- Added cross-platform `MEDIA_STORAGE=disk|memory|auto` selection. Linux/Docker
  can use `/dev/shm`; native Windows keeps normal per-user paths and marks
  `auto`/`memory` upload inputs with `FILE_ATTRIBUTE_TEMPORARY` so the Windows
  cache can prefer RAM while retaining safe disk spill behavior. Explicit
  Windows `memory` mode uses a conservative admission budget based on memory,
  concurrency, FFmpeg working space, and OS headroom. Upload chunks are 1 MiB
  and failed partial uploads are removed.
- Added transient API-input cleanup after the complete encode/retry/fallback
  lifecycle on every platform. Folder Watch sources remain owned by Folder
  Watch and are not deleted by this path. No delete-on-close flag, RAM-disk driver, pipe rewrite,
  or whole-file Python buffer was introduced.
- Protected active job input/output paths from retention cleanup using Redis job
  metadata.
- Corrected target-size bitrate math to use the effective trimmed duration
  (for example, a 600-second source trimmed to 60 seconds uses 60 seconds for
  bitrate calculation).
- Added regression coverage for Discord defaults/migration, Folder Watch
  validation/discovery, and trim-duration behavior.
- Fixed stale pipeline status in the frontend by clearing decoder/encoder state
  for each job, parsing messages after SSE reconnects, and showing the detected
  source codec for CUDA decode (for example, `cuda (hevc)`).
- Fixed Apple/mobile HEVC MP4 compatibility by adding the `hvc1` video sample
  entry tag for HEVC MP4 outputs across normal, decode-retry, and CPU-fallback
  paths. The bitstream is unchanged; only the container metadata is corrected.

## Commands run and results

The final full-release command was:

```powershell
.\release-local.ps1 -Version 140.0.0.0 -Overwrite
```

The output directory was:

`C:\Users\jdude\Documents\Codex\2026-08-12\continue-the-8mb-local-windows-and\latest-github-20260814\dist\release\140.0.0.0`

The final run completed 36 stages with result `PASS` and `release_ready=true`.
It ran:

- Version synchronization and stale-version checks: PASS.
- `npm ci`: PASS; 193 packages installed.
- SvelteKit sync: PASS.
- `svelte-check`: PASS, 0 errors and 0 warnings.
- Frontend check and lint: PASS, 0 errors and 0 warnings.
- Frontend production build: PASS.
- Root Python tests: 11 passed; backend API tests: 57 passed with 1 existing
  environment-gated skip; worker tests: 42 passed; shared tests: 7 passed.
- Focused Windows temporary-storage tests: 7 passed, including the actual
  Windows file attribute, FFmpeg pathname reopen, auto/memory/disk behavior,
  and success/failure cleanup lifecycle.
- Python compile checks: PASS.
- Version-consistency tests: PASS.
- Docker Compose config validation for default, CPU, and VAAPI files: PASS.
- Portable Windows build: PASS.
- Portable authenticated smoke test: PASS, including the settings/codec/profile
  persistence gate.
- Current-user installer smoke, shortcut, app, uninstall, and user-data
  preservation test: PASS, including the settings/codec/profile persistence
  gate.
- Store-submission MSIX build and validation: PASS.
- Local Docker build, metadata validation, save, archive inspection, startup,
  synthetic input, upload, compression, download, FFprobe, and test-container
  cleanup: PASS.
- Post-release frontend rebuild: PASS, `svelte-check` 0 errors/0 warnings and
  production build complete.
- Post-fix worker suite: PASS, 44 tests.
- Post-fix Docker image `jms1717/8mblocal:local-140.0.0.0-hevc-hvc1`: PASS;
  saved archive size 1,514,899,968 bytes and SHA-256
  `CB99D1A310A6A4EEE62DEF116774204B0B2EFD6523587ED9CB3567E7BE7BD5F2`.
- Follow-up Docker image `jms1717/8mblocal:local-140.0.0.0-folderwatch-shm-20260814`:
  PASS; saved archive size 1,514,900,480 bytes and SHA-256
  `F788B4D63333AAEE591436B355A32051DE4435F8F00846032263EB9BCF8B3DB0`.

`git diff --check` passed. PowerShell parsing passed for the project scripts.
The first Docker check found the local daemon stopped; Docker Desktop was
started locally and the final Docker stages then completed successfully.

## Version and packaging evidence

All active version locations checked by `scripts/check-version.ps1` resolve to
`140.0.0.0` (display form `140.0.0` where applicable), including the root
VERSION file, generated UI/backend modules, Docker defaults and labels, Windows
version metadata, installer metadata, MSIX, workflows, and artifact names.

Final artifacts:

| Artifact | Size | SHA-256 |
|---|---:|---|
| `8mblocal.exe` | 196,454,051 bytes | `C4B0F34E93D7BD72D6B5972368BE4D2E4BFC4F0B20E8A0568C4B2E57F38C9D5E` |
| `8mblocal-Setup.exe` | 196,958,105 bytes | `7DD5DE5F0F28FED30297E2F3C1C5FBC2AA0DA2EE5CAB7510BD2C31EB7110E1C3` |
| `8mblocal_140.0.0.0_x64.msix` | 195,129,793 bytes | `181AAF9D561F32E9164B75B8A40BC221516488C5291A0368277F6AF476F7E86D` |
| `8mblocal-docker.tar` | 1,514,900,480 bytes | `F788B4D63333AAEE591436B355A32051DE4435F8F00846032263EB9BCF8B3DB0` |

The EXE and installer file metadata report version `140.0.0.0`. The MSIX
contains:

- Identity: `jms1717.8mb.local`
- Publisher: `CN=AAE66F20-996E-4A3C-B08E-182952BAD9F7`
- Architecture: `x64`
- Version: `140.0.0.0`
- Executable: `8mblocal.exe`
- Capabilities: exactly one `runFullTrust`
- `unvirtualizedResources`: absent
- `FileSystemWriteVirtualization`: absent

The MSIX is unsigned in this local environment. Build and structure validation
passed; Store signing/submission and installation requiring a trusted
certificate remain Partner Center/local-environment steps.

The latest Docker image tag was
`jms1717/8mblocal:local-140.0.0.0-folderwatch-shm-20260814`. Its metadata
reported application version `140.0.0.0`, source revision
`d46835817fbade646db2d51cba04a709a7e68c15`, repository URL
`https://github.com/JMS1717/8mb.local`, and a local build timestamp. The image
was saved locally and was not pushed.

The latest Docker image was saved at
`dist\release\140.0.0.0\8mblocal-docker.tar` and transferred to Powerhouse
with the matching SHA-256 shown above.

## Windows smoke evidence

The portable executable started without administrator elevation. The smoke test
verified health, frontend loading, API version, Basic authentication, the
current direct-authenticated SSE stream, synthetic FFmpeg input, upload,
compression with CPU `libx264`, status polling, download, and FFprobe output.

The installer was installed to a unique test directory in current-user mode,
without overwriting the existing installation. It created the expected shortcut,
started successfully, passed the same application smoke path, and uninstalled
while preserving the test user-data sentinel.

The cached tested FFmpeg bundle included `ffmpeg.exe` and `ffprobe.exe` with
CPU AV1 support (`libsvtav1`). The first clean temporary build dependency
install stalled during intermittent package-network resolution; the final
build used the complete local `.venv` and passed. This was an environment/tool
bootstrap issue, not an application failure.

## Codec and settings evidence

The source and UI retain the latest batch and modular encoder architecture.
Codec-related settings cover selected codec, codec visibility, hardware
detection, CPU fallbacks, legacy `libaom_av1` mapping, profile codec values,
and profile FPS caps. The backend regression suite passed the targeted codec
availability, saved-visibility, fallback, settings validation, profile, and
cancellation tests. The packaged Windows smoke gate additionally changed and
restored a CPU codec visibility flag, verified it disappeared from the
available-codec selector, created/selected/edited/deleted a temporary profile,
verified its FPS cap survived default-preset saving, and round-tripped the
other settings controls. The current UI check produced no accessibility
warnings.

The new Folder Watch settings/discovery tests passed, and the full frontend
production build passed. The packaged smoke stage did not run an end-to-end
Folder Watch job because the existing Windows smoke helper does not yet create
an external watched directory; that remains a focused manual/native-runtime
test opportunity. The Docker release smoke ran disk-backed uploads, not an
explicit tmpfs mode.

The earlier Powerhouse test evidence remains separate from this local release
run: CPU H.264/H.265/AV1 fallback and GPU H.264/HEVC were proven there, while
`av1_nvenc` was excluded after real validation. This local Windows run proves
the CPU packaged smoke path; it does not claim GPU hardware codec coverage on
this Windows machine.

## Powerhouse deployment and mobile-playback evidence

The production Compose project is `/home/powerhouse/Docker/8mblocal` (the
requested `/home/Docker/8mblocal` path does not exist). Only the exact
`8mblocal` container was recreated; its port 8001, `.env` bind mount, uploads
mount, outputs mount, network, and unrelated containers were preserved. The
previous image was retained under unique rollback tags.

The current production container is healthy on the post-fix image. Evidence:

- `/healthz`: HTTP 200, `{"ok":true}`.
- `/api/version`: HTTP 200, `{"version":"140.0.0.0"}`.
- Production HEVC encode: task `ea1952b3-35fc-433e-a1e3-79979aa14138`, worker
  result `SUCCESS`, encoder `hevc_nvenc`.
- Output FFprobe: HEVC Main, `hvc1`, `yuv420p`, 30 fps, 6.000 seconds, 180
  frames, normal zero start timestamp, AAC audio, progressive MP4.
- The pre-fix production sample was also valid but reported `hev1`; Apple
  device reports that H.264 worked while the old HEVC sample did not. The
  `hvc1` result is the targeted compatibility fix.

The new output remains available for manual iPhone/iPad verification at:
`/api/jobs/ea1952b3-35fc-433e-a1e3-79979aa14138/download`. Test inputs and the
old `hev1` output were removed by exact path after validation; the new sample
was retained for the user test.

## Follow-up settings and RAM configuration

The settings UI follow-up places Folder Watch last in the actual markup, keeps
it collapsed by default, adds an Enabled/Disabled status badge, and explains
that stable seconds is the quiet period during which file size and modified
time must remain unchanged. It is not the video's duration or total encode
time. The default remains 5 seconds, with a separate polling interval.

The three local Compose profiles now expose `MEDIA_SHM_SIZE` with a default of
`10g`; this is a ceiling rather than preallocated RAM. On Powerhouse, the exact
production Compose file was backed up before the minimal service-only change.
The recreated `8mblocal` container reports `/dev/shm` as `1.0G`, logs uploads at
`/dev/shm/8mb.local/uploads`, remains healthy, and returns the expected version.
Folder Watch is still disabled and has no configured input folder for manual
user testing.

The current Windows rebuild was performed after the mobile HEVC fix. Portable
smoke passed for health, frontend/version, Basic Auth/SSE, settings persistence,
upload, CPU transcode, download, and FFprobe. The helper's transcode case is
CPU H.264; a separate Windows portable HEVC encode was not claimed. Focused
Folder Watch tests passed 3/3, Windows temporary-storage tests passed 4/4,
and HEVC/mobile cleanup tests passed 5/5.

The follow-up legacy-documentation audit found the Windows README still
described the old v138 MSIX and incorrectly documented
`unvirtualizedResources`. It was corrected to use the root `VERSION`, current
artifact naming, Store-safe manifest capabilities, Windows RAM-preferred
temporary storage, and Folder Watch guidance. Historical v138 changelog and
GPU validation sections were intentionally left unchanged.

## Docker/Linux scope

The final local Docker run used temporary bind-mounted upload/output/config
directories, a localhost-only high port, a unique test container name, and
`AUTH_ENABLED=false`. The container exposed health/frontend/API behavior,
FFmpeg/FFprobe, upload, compression, download, and valid output streams. The
uniquely named test container was removed successfully. No Docker image was
published.

Native Dell Linux testing remains separate from this local Windows release
report and is not claimed here. Powerhouse isolated and production smoke
evidence is recorded above.

## Warnings and remaining manual steps

- Inno Setup reports that the `x64` architecture identifier is deprecated; it
  substituted `x64os`. This is a packaging warning, not a failed build.
- PyInstaller reports optional Android and pycparser hidden-import warnings;
  the Windows executable and smoke tests passed.
- The retention cutoff now uses timezone-aware UTC timestamps so local timezone
  offsets cannot cause fresh files to be deleted.
- If a clean host lacks MakeAppx, the MSIX helper downloads the pinned
  `Microsoft.Windows.SDK.BuildTools` version from NuGet. The current host used
  its installed Windows SDK, so the fallback download was not exercised in
  this run; adding a verified package SHA-256 remains a hardening opportunity.
- The MSIX is unsigned locally and cannot be installed as a trusted Store
  package until signed through the appropriate Microsoft process.
- Folder Watch has not been proven against a real UNC share or Linux tmpfs in
  this Windows-only run; unit validation and cross-platform path logic are in
  place, but host-specific permissions/mount behavior still needs Linux/UNC
  evidence.
- The rebuilt post-fix Docker image was deployed only to the authorized
  Powerhouse `8mblocal` service for smoke testing; no registry publication or
  GitHub/Partner Center action occurred.
- Before any publication, review the dirty worktree and commit changes through
  the normal project review process. This task intentionally did not commit or
  push anything.

## Readiness

For the local Windows/Docker workflow and the targeted production mobile
compatibility defect, the tested version is ready for manual review. It is not
authorization for a GitHub release, Docker publication, or Microsoft Store
submission. Store signing/Partner Center work and the separate Dell evidence
remain manual follow-up steps.

## Post-report memory, cleanup, and HEVC verification — 2026-08-14

- Raised the default Docker `MEDIA_SHM_SIZE` ceiling from `1g` to `10g` and
  the default `MEDIA_MEMORY_LIMIT_GB` budget from `16` to `10`. Neither value
  reserves host RAM. Uploads are admitted against live shared-memory and host
  availability before writing; `auto` falls back to `/app/uploads` when the
  reservation does not fit.
- Added an in-flight reservation counter to prevent concurrent uploads from
  overcommitting the current RAM budget. A direct container check proved both
  paths: forced low capacity selected `/app/uploads`, while capacity available
  selected `/dev/shm/8mb.local/uploads` and returned a reservation.
- API-staged input cleanup now runs on Linux/Docker as well as Windows after
  success, retry/fallback, cancellation, or failure. Folder Watch sources are
  still retained under their own keep/delete/move policy.
- Retention cleanup now prunes expired or orphaned history rows together with
  their media. The cutoff uses timezone-aware UTC. Exact-image test: expired
  output and history row were both removed; a fresh output remained.
- Final-source Docker image:
  `jms1717/8mblocal:local-140.0.0.0-memory-hevc-v2-20260814`.
  Health and `/api/version` passed; `/dev/shm` reported `10G`. A real upload,
  `libx265` MP4 compression, source cleanup, and FFprobe passed. The output
  reported `codec_name=hevc`, `codec_tag_string=hvc1`, `pix_fmt=yuv420p`, and
  `duration=1.000000`.
- Full backend suite after the fixes: 60 passed, 1 skipped. Full worker
  suite: 46 passed. Frontend `npm run check`: 0 errors, 0 warnings. All three
  Compose profiles rendered successfully with 10g shared-memory and 10 GB
  memory-budget defaults.
- The existing Windows EXE/installer/MSIX artifacts were built before this
  follow-up's dynamic-admission and history-pruning changes. Rebuild those
  artifacts before treating them as packages containing this latest source.

## Final v140 local release build and Powerhouse deployment — 2026-08-14

The current dirty checkout was rebuilt after the Support the Project settings
layout change and all current memory, cleanup, history, and HEVC changes. The
full local release command completed successfully in:

`dist/release/140.0.0.0-current-20260814/`

The build produced these non-empty artifacts:

- `8mblocal.exe` — 196,458,138 bytes — SHA-256
  `57965C44A70776145AF9F61DF7B90E893864D03E60E4850A5D2888910AC8081B`.
- `8mblocal-Setup.exe` — 196,962,764 bytes — SHA-256
  `5C1656B4D4E9F34AF67494E5BF8FBE838B43EEE9B4899B0105ADEEA994A06284`.
- `8mblocal_140.0.0.0_x64.msix` — 195,134,346 bytes — SHA-256
  `C55A4A5C483CE6998CBC9283BA8F655C25F6110954E8A3D2D4DE700D5C58DBFD`.
- `8mblocal-docker.tar` — 1,514,908,672 bytes — SHA-256
  `5B313F4A5FD762DD2AB8CF97A15449B12AA54BE3D0B95C8D4064F134A81E2B17`.
- `SHA256SUMS.txt`, `BUILD-MANIFEST.json`, and `TEST-RESULTS.md` were
  generated by the release script.

The portable and installer smoke tests passed health, frontend/version,
authentication/SSE, settings and profile persistence, upload, transcode,
download, and FFprobe validation. MSIX creation passed as an unsigned Store
submission package. The local Docker build and smoke passed health, API,
frontend, upload, compression, download, FFprobe, and cleanup.

The production Powerhouse project is `/home/powerhouse/Docker/8mblocal`. Before
deployment, the previous compose file and `.env` were backed up under:

`/home/powerhouse/Docker/8mblocal/backups/20260814-v140-memory-settings/`

Only the exact `8mblocal` service was recreated. The old production image was
`sha256:3fdee716a1cfbf86736c06e9ce304bd54267f4457c0835ef9e8a673d600985e9`.
The deployed image is
`jms1717/8mblocal:codex-140.0.0.0-20260814`, image ID
`sha256:88797cf7543ab3f59ca643fe128c0f735cd63c2ddf848a917b26a668c23e4bcb`,
from commit `d46835817fbade646db2d51cba04a709a7e68c15`. The final container
`c768c7dec333` is healthy, reports API version `140.0.0.0`, serves the
frontend with HTTP 200, and reports a real 10 GiB `/dev/shm`. The compose
defaults now use `MEDIA_SHM_SIZE=10g` and `MEDIA_MEMORY_LIMIT_GB=10` without
preallocating host RAM.

The bounded production API smoke used synthetic media. Upload, HEVC NVENC
compression, download, and FFprobe all passed. The output was HEVC with
`hvc1` and AAC, duration `1.044898` seconds, and the API-staged source was
removed after completion. Worker startup validated H.264 NVENC, HEVC NVENC,
CPU H.264, CPU H.265, and CPU AV1; `av1_nvenc` was rejected by real encoder
validation and the CPU AV1 fallback remained selected. The temporary image
archive was removed from `/tmp` after the deployment smoke. The prior image,
production volumes, and unrelated containers were retained.

The earlier paragraph in this report stating that the current production
container had `1.0G` shared memory describes the pre-deployment snapshot only;
the final deployed container has the verified 10 GiB setting above. The older
pre-follow-up image name in the preceding memory section is likewise retained
as historical evidence; the final release image and tag are recorded here.

## Final local hardening pass — 2026-08-16

This additional pass used the current dirty checkout at commit
`f853c14e313c0940e12e879e281adf808cfb3208` and did not contact or modify any
remote system. It addressed four reliability/security issues found during the
review:

- Windows upload admission now runs off the FastAPI event loop, reserves a
  bounded RAM-preferred budget, and releases it on success or failure.
- Batch ZIP creation now runs off the event loop and is published atomically,
  so downloads cannot observe a partially-written archive.
- Redis-backed adaptive concurrency leases renew during long encodes and stop
  cleanly on release.
- The SPA catch-all now rejects paths that resolve outside the frontend build
  root; settings GET endpoints require the same Basic Auth protection as the
  corresponding state-changing endpoints.

Fresh validation after rebuilding the current source:

- Backend/worker/shared tests: `124 passed, 1 skipped, 5 warnings`.
- Frontend `svelte-check`: `0 errors and 0 warnings`.
- Python compile checks: passed.
- All three Docker Compose config checks: passed.
- Version check: `140.0.0.0` / display `140.0.0`.
- Windows release smoke with authentication, settings/profile persistence,
  upload, transcode, download, and FFprobe: passed.
- Full portable-EXE E2E: `18 codec/edge job(s)` plus batch ZIP, duplicate-name
  ZIP, invalid upload cleanup, SSE reconnect, cancellation, restart recovery,
  and FFprobe validation: passed.

Current rebuilt Windows artifacts:

- `dist/8mblocal.exe` — 196,471,357 bytes — SHA-256
  `3A0336AE46688824459F66E2C8BC4246691EA538CD573B53967F68F6355C29A1`.
- `dist/8mblocal-Setup.exe` — 196,979,901 bytes — SHA-256
  `E70D637F90907FC9375F8A19ECB80CAFD466EA8DB5B8A41EDC8AFF5AAE3D809E`.
- `dist/8mblocal_140.0.0.0_x64.msix` — 195,147,676 bytes — SHA-256
  `A77ABF25517B21B5B0D5943697558DD53C935E551996C97029A9E0567A3DABB5`.

The EXE and installer report version `140.0.0.0`; the MSIX identity is
`jms1717.8mb.local`, publisher
`CN=AAE66F20-996E-4A3C-B08E-182952BAD9F7`, architecture `x64`, and version
`140.0.0.0`. The package contains `runFullTrust` and neither
`unvirtualizedResources` nor `FileSystemWriteVirtualization`. The MSIX is
unsigned and was not submitted to Partner Center.

The local Docker daemon was unavailable during this final pass, so no new
Docker image or archive is claimed for this exact final source. The earlier
Powerhouse deployment and Docker evidence in this report remain historical;
the current final local source must receive a fresh Docker build when Docker
is available. No commit, push, release, Docker publication, or production
change was made in this pass.

The actual local orchestrator was also exercised with:

`powershell -NoProfile -ExecutionPolicy Bypass -File .\release-local.ps1 -Version 140.0.0.0 -SkipDocker -OutputDir .\dist\release\140.0.0.0-hardening-20260816`

It passed version synchronization, frontend checks/build, Python compilation,
11 repository tests, 63 backend tests, 44 worker tests, 7 shared tests,
version consistency, portable EXE smoke, current-user installer
install/smoke/uninstall, and MSIX validation. Its manifest correctly records
`INCOMPLETE` and `release_ready=false` because `-SkipDocker` was supplied. The
staged Windows artifacts are saved under
`dist/release/140.0.0.0-hardening-20260816/` with checksums in
`SHA256SUMS.txt`. The worktree now has 31 application/source files modified,
this report modified for the handoff, and the three relevant untracked source
files listed above.

## Full local release and isolated Powerhouse retest — 2026-08-17

The Docker Desktop daemon became available and the current dirty checkout was
tested without changing Git or production. A Docker context defect was found:
the existing local `dist/` tree was approximately 10.5 GB and was not excluded
from the build context. The root `.dockerignore` now excludes `dist/`, build
outputs, frontend build caches, and local state/Redis data. The corrected
context was approximately 77.6 MB. No large local output directory was deleted.

The complete command passed:

`powershell -NoProfile -ExecutionPolicy Bypass -File .\\release-local.ps1 -Version 140.0.0.0 -OutputDir .\\dist\\release\\140.0.0.0-full-20260817 -Overwrite`

Result: `PASS`; `TEST-RESULTS.md` reports `Release-ready: True`. The run passed
frontend installation/check/lint/build, repository/backend/worker/shared
tests, Python compilation, version consistency, all Compose profiles, portable
EXE smoke, current-user installer install/smoke/uninstall, MSIX build and
manifest validation, Docker build, image metadata, Docker save/archive checks,
container startup, health/API/frontend, synthetic upload/compression/download,
FFprobe validation, and isolated container cleanup.

Current full-release artifacts:

- `dist/release/140.0.0.0-full-20260817/8mblocal.exe` — 196,469,165 bytes —
  SHA-256 `FE4E282D3241FCEFE2B5228DA2A4BD385FE1DF10A81744026E0A549783FDF0DC`.
- `dist/release/140.0.0.0-full-20260817/8mblocal-Setup.exe` — 196,974,717
  bytes — SHA-256
  `8930024A32F8BB31B0C1F1CF4A6FB944A146C9AE31FAD840D7B526487780A1A0`.
- `dist/release/140.0.0.0-full-20260817/8mblocal_140.0.0.0_x64.msix` —
  195,145,105 bytes — SHA-256
  `C9DB498BF533D652C15224CB67352096EE86DE259487FC129464DC3A66F7CCE2`.
- `dist/release/140.0.0.0-full-20260817/8mblocal-docker.tar` — 1,515,115,520
  bytes — SHA-256
  `7843F163340528355C92FA92F84908ACF4563F366F44F8490D7FE904C4125AB7`.

The local image was tagged
`jms1717/8mblocal:local-140.0.0.0` and contained version `140.0.0.0`, source
commit `f853c14e313c0940e12e879e281adf808cfb3208`, and the repository source
label. The image tar was transferred to Powerhouse and its remote SHA-256
matched the local hash.

Powerhouse isolated test results:

- Test image was loaded into a uniquely named isolated deployment with a
  localhost-only port, temporary bind mounts, `--gpus all`, and 10 GiB shared
  memory. No production volumes, `.env`, networks, queues, or media were used.
- Health and version passed before and after a test-stack restart.
- CPU `libx264`, `libx265`, and `libsvtav1` jobs passed.
- GPU `h264_nvenc` and `hevc_nvenc` jobs passed.
- Every downloaded output passed FFprobe with valid video/audio streams. HEVC
  outputs used the `hvc1` MP4 sample-entry tag.
- The worker removed transient upload inputs after completion.
- The exact isolated container, image tag, and test directory were removed.
  A root-owned transient subdirectory required a temporary root helper based
  on the existing application image; no broad cleanup command was used.

Production verification after cleanup: the existing `8mblocal` container
remained running and healthy, still used
`jms1717/8mblocal:codex-140.0.0.0-20260814`, still exposed port 8001, retained
its existing uploads/outputs/.env mounts, and reported API version
`140.0.0.0`. Production was not stopped, recreated, or deployed to during
this retest.

Remaining non-blocking items: npm audit still reports two low-severity
transitive `cookie` advisories through the pinned SvelteKit dependency; the
available forced remediation is a breaking downgrade and was not applied.
FastAPI/Pydantic/Celery dependency deprecation warnings remain. The MSIX is an
unsigned Store-submission package and still requires the normal signing/Store
submission process. Real-device mobile playback should remain a manual check.

## Dependency security fix and rebuilt artifacts — 2026-08-17

The transitive npm advisory was fixed in the source checkout. SvelteKit 2.70.2
declares `cookie ^0.6.0`, so `frontend/package.json` now pins a root npm
override to the patched `cookie 0.7.2`; `frontend/package-lock.json` records
the same version. This avoids npm's unsafe proposed SvelteKit downgrade to
`0.0.30`.

Validation after the change:

- `npm ci --ignore-scripts --no-audit --no-fund`: passed.
- `npm ls cookie @sveltejs/kit --all`: SvelteKit 2.70.2 with `cookie 0.7.2
  overridden`.
- `npm audit --omit=dev --audit-level=low`: `found 0 vulnerabilities`.
- Frontend check, lint, and production build: passed with 0 Svelte errors and
  0 warnings.
- Complete `release-local.ps1` build, Windows portable/installer/MSIX smoke,
  Docker build/save/runtime smoke, and all automated tests: passed.

The rebuilt security-release artifacts are under
`dist/release/140.0.0.0-security-20260817/`:

- `8mblocal.exe` — SHA-256
  `DB15AA03F28FA93D524689712EECA196A03ED98B35384EC024C9E769FDE83EBE`.
- `8mblocal-Setup.exe` — SHA-256
  `2FBD93623E9BE7AC00F46A011DDA03936F2516AE91BCAF66B3B2B21341ABCC80`.
- `8mblocal_140.0.0.0_x64.msix` — SHA-256
  `20A0C1C32D62118A24AE4414F3AFC7DA15C3FEEB32246685C2D1C65EB4286682`.
- `8mblocal-docker.tar` — SHA-256
  `AB968673C30D57C1EAB99E2F4C1C22580D2CEC55BA9CB2F3E8FED4CE348FE885`.

No remote system was changed and no package was published.

## v141 release candidate and permanent audit gates — 2026-08-17

The active version was advanced through `scripts/set-version.ps1` to
`141.0.0.0` / display `141.0.0`. The synchronizer was also corrected to handle
CRLF Dockerfiles, and `scripts/check-version.ps1` now validates the Dockerfile
version under that line-ending format.

The local release workflow now permanently includes two required frontend
security stages after `npm ci`:

- `frontend-npm-audit` runs `npm audit --omit=dev --audit-level=low`.
- `frontend-dependency-security` verifies the patched cookie override,
  package-lock resolution, and installed `node_modules` version.

The complete v141 command passed:

`powershell -NoProfile -ExecutionPolicy Bypass -File .\\release-local.ps1 -Version 141.0.0.0 -OutputDir .\\dist\\release\\141.0.0.0-full-20260817 -Overwrite`

All 38 stages passed with zero failures and zero skipped stages. This included
the new audit/regression gates, all frontend/Python tests, version and Compose
validation, Windows portable EXE and installer smoke, MSIX validation, Docker
build/save/runtime smoke, upload/compression/download, FFprobe, and cleanup.

v141 artifacts:

- `dist/release/141.0.0.0-full-20260817/8mblocal.exe` — 196,471,648 bytes —
  SHA-256 `FC3A3F62156885CF0E5F5477C3845EF68A2A7F15F7C5ED3CBA9780DE9BCCC696`.
- `dist/release/141.0.0.0-full-20260817/8mblocal-Setup.exe` — 196,973,808
  bytes — SHA-256
  `DAB082FDC3BA029268C7C93D222BF1B7701F29506AA01FFA32737C2DD2150752`.
- `dist/release/141.0.0.0-full-20260817/8mblocal_141.0.0.0_x64.msix` —
  195,147,418 bytes — SHA-256
  `8432C5C4466DDE20BFEB6459685D1AE3CED196D2F388FB40AD8E2035F599582B`.
- `dist/release/141.0.0.0-full-20260817/8mblocal-docker.tar` — 1,515,115,520
  bytes — SHA-256
  `2EA327D545799398FA2021EBA15BF2D07F02BF76A12898A2E5B8B2D667DF6125`.

The local image tag is `jms1717/8mblocal:local-141.0.0.0`, with version
`141.0.0.0` and source revision
`f853c14e313c0940e12e879e281adf808cfb3208`. It was not pushed or deployed.

The current v141 change list is recorded at the top of `CHANGELOG.md`. At the
time this section was written, Powerhouse production remained unchanged and
only the earlier isolated v140 test had been run there. The subsequent v141
deployment and production smoke evidence is recorded in the next section.

## v141 Powerhouse deployment and local PC installation — 2026-08-17

The v141 Docker image was transferred to Powerhouse as a local tar archive and
verified there with SHA-256
`2EA327D545799398FA2021EBA15BF2D07F02BF76A12898A2E5B8B2D667DF6125`. The
remote image was loaded as `jms1717/8mblocal:local-141.0.0.0` and deployed only
to the exact production Compose service `8mblocal`. The production Compose
image is now `jms1717/8mblocal:codex-141.0.0.0-20260817`; the old v140 image
was retained for rollback.

Before deployment, the protected rollback configuration backup was created at
`/home/powerhouse/Docker/8mblocal/backups/codex-v141-20260817T123830Z`.
Only the `8mblocal` container was recreated. The deployment began at
`2026-08-17T12:41:18Z` and the application health check passed at
`2026-08-17T12:41:25Z` (approximately seven seconds of application
recreation/outage). Final verification showed the container running and
healthy, port 8001 unchanged, the existing uploads/outputs/.env mounts
unchanged, `/healthz` returning `{"ok":true}`, and `/api/version` returning
`{"version":"141.0.0.0"}`. No unrelated container was restarted.

A real production-path synthetic smoke then passed using the deployed v141
image: upload, `hevc_nvenc` compression, download, and FFprobe validation.
The downloaded file contained HEVC video with the `hvc1` MP4 sample-entry tag
and AAC audio, with a valid 2.538667-second duration. The exact synthetic
output and history entry were removed, and the transient uploaded input was
confirmed absent. The temporary smoke directory was removed.

The v141 installer was tested on this PC with the isolated current-user
installation helper. It installed to a unique directory under:

`dist/release/141.0.0.0-full-20260817/pc-installed-smoke-20260817/run-739f888b0a06405c9e1625eed2e8c14c/installed/`

The existing installation was not overwritten. The installed copy passed the
desktop shortcut target check, startup/health, frontend loading, API version
`141.0.0.0`, synthetic upload, CPU transcode, status polling, download, and
FFprobe validation. The uninstaller then removed the isolated application and
shortcut while preserving its test user-data sentinel. No `8mblocal.exe`
test process remained. The test logs and preserved smoke data remain under
`dist/release/141.0.0.0-full-20260817/pc-installed-smoke-20260817/`.

This PC run did not enable the optional authenticated settings sub-suite
because the required temporary smoke credentials were not present in the
current process environment; it therefore does not claim a fresh installed
copy settings/authentication result. The full v141 release workflow and its
automated settings/version tests remain recorded above.

## v141 reliability audit and rebuild — 2026-08-17

The focused audit found and fixed a real frontend reliability defect: the main
compression page and Queue page closed native `EventSource` connections on a
transient error, disabling the browser's automatic SSE reconnect and making a
live or completed job appear stuck until Queue or a page refresh was opened.
Those handlers now keep the stream open and allow native reconnect behavior.
The batch page copy was also corrected to describe parallel processing when
capacity allows.

The cross-platform subprocess helper was hardened for platform-mocked test
environments: it now checks that the Windows-only `CREATE_NO_WINDOW` constant
exists before returning it. Two Windows storage tests were corrected to mock
the Windows platform explicitly; they were test-environment defects, not
runtime failures.

Validation after the fixes:

- Frontend `npm run check`: passed with 0 errors and 0 warnings.
- Frontend production build: passed.
- Dependency-backed Python suite in the application image: 134 passed, 2
  platform-conditional skips, 5 existing deprecation warnings.
- Complete `release-local.ps1 -Version 141.0.0.0` rebuild: all stages passed,
  including frontend, Python, Windows EXE, installer, MSIX, Docker build/save,
  container runtime, upload/compression/download, FFprobe, and cleanup.

The rebuilt artifacts are under
`dist/release/141.0.0.0-bugfix-20260817/`:

- `8mblocal.exe` — 196,470,035 bytes — SHA-256
  `0506C5C69A65E7F269110A1C22F4585A95AF3F44D185FBBD2DF6623FDBA65EF8`.
- `8mblocal-Setup.exe` — 196,975,157 bytes — SHA-256
  `7E0BE24F47A28156E2B77081C06314BA333D1B264A6F64F84DAE93C259579EDC`.
- `8mblocal_141.0.0.0_x64.msix` — 195,146,142 bytes — SHA-256
  `C7AF785E692BCF7DD7875791899985E88A27EA277169A6AB6729315DBB98A5F6`.
- `8mblocal-docker.tar` — 1,515,116,544 bytes — SHA-256
  `51AAD8469DBBC08B8FFD8FCD7F78DC5E7B6E508C7EDD905A311D8EF417171ABF`.

This audit/rebuild changed only the local checkout. Powerhouse remains on the
previously deployed v141 image; the new SSE fix has not been deployed there in
this audit.

## v141 additional reliability audit — 2026-08-17

The follow-up source review found and fixed four additional edge cases:

- Corrupt or hand-edited `codec_visibility` values could crash settings reads,
  treat strings such as `"false"` as true, or hide every CPU fallback. Reads
  now coerce supported scalar values, repair malformed sections, and guarantee
  a usable CPU fallback without discarding unrelated settings.
- Folder Watch now rehydrates persisted queued/running records after an API
  restart so output validation and original-file actions are not lost when the
  in-process pending map is rebuilt.
- Direct compression and Folder Watch now persist job metadata before task
  dispatch, eliminating a fast-worker race that could produce an output with no
  queue record. Partial metadata from a failed direct request is cleaned up.
- The native Windows launcher now reloads persisted media-storage, memory
  budget, upload-limit, retention, history, concurrency, and logging settings
  from the per-user `.env` file before importing runtime configuration.

New regression coverage passed:

- Focused tests: 19 passed, 1 existing Pydantic deprecation warning.
- Full Python suite: 141 passed, 2 platform-conditional skips, 5 existing
  deprecation warnings.
- Frontend `npm run check`: 0 errors and 0 warnings.
- Frontend production build: passed.
- `npm audit --omit=dev --audit-level=high`: 0 vulnerabilities.
- `scripts/check-version.ps1`: `VERSION_CHECK_PASSED=141.0.0.0`.
- `scripts/check-frontend-dependencies.ps1`:
  `FRONTEND_DEPENDENCY_SECURITY_CHECK=PASSED`.
- `git diff --check`: passed.

The existing `141.0.0.0-bugfix-20260817` Windows artifacts do not include
these follow-up source edits because they were created before this audit. A
new Windows release rebuild is required before distributing a refreshed EXE,
installer, or MSIX. Powerhouse was not redeployed during this follow-up.

## Follow-up artifacts and platform validation — 2026-08-17

The follow-up source was rebuilt locally after the additional fixes.

Windows-only release output:

- `dist/release/141.0.0.0-followup-20260817/8mblocal.exe` — 196,473,397
  bytes — SHA-256
  `327132F3AA4467E878702BDA1F4D98F101F5B4F214EB81F43A3DED08ABA0208A`.
- `dist/release/141.0.0.0-followup-20260817/8mblocal-Setup.exe` —
  196,976,492 bytes — SHA-256
  `F10ADED5DCEBF7252915D6F2F09C8059D0E3DDB71BDDC50F2D22657E46614B67`.
- `dist/release/141.0.0.0-followup-20260817/8mblocal_141.0.0.0_x64.msix` —
  195,148,886 bytes — SHA-256
  `2E4840826DCFE50677A2013CAEDB5BE94F2829D930C36F5DE2ABE79EFDE16FE4`.

The fresh portable and isolated installer smoke tests passed health, frontend,
version, authentication, SSE authorization, settings and profile persistence,
codec visibility, upload, transcode, download, and FFprobe validation. The
installer was removed afterward and its test user-data sentinel was preserved.
MSIX validation passed for identity `jms1717.8mb.local`, publisher
`CN=AAE66F20-996E-4A3C-B08E-182952BAD9F7`, x64 architecture, version
`141.0.0.0`, required `runFullTrust`, and no forbidden virtualization
capabilities. The package is unsigned for Store submission; installation was
not claimed.

Docker-only release output:

- Local tag: `jms1717/8mblocal:local-141.0.0.0`.
- `dist/release/141.0.0.0-docker-followup-20260817/8mblocal-docker.tar` —
  1,515,119,616 bytes — SHA-256
  `C32266076ADDE2777E633A7BB52FA8C46EEAC6C982AB0FBD55CF7A0F6314D7B4`.

The fresh Docker pass built the image, verified Compose configuration, checked
image labels/environment metadata, saved and structurally inspected the tar,
started a disposable container, created synthetic media, uploaded it,
compressed it, downloaded it, validated both streams with FFprobe, and removed
only the test container. The Docker image was not pushed. Windows stages were
intentionally skipped in this Docker-only pass.
