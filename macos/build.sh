#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
version="$(tr -d '\r\n ' < "$repo_root/VERSION")"
architecture="$(uname -m)"
if [[ "$architecture" != "arm64" ]]; then
  echo "Apple Silicon arm64 is required; found $architecture" >&2
  exit 1
fi

ffmpeg_path="$(command -v ffmpeg)"
ffprobe_path="$(command -v ffprobe)"
encoder_inventory="$("$ffmpeg_path" -hide_banner -encoders 2>&1)"
if [[ "$encoder_inventory" != *h264_videotoolbox* ]] || [[ "$encoder_inventory" != *hevc_videotoolbox* ]]; then
  echo "FFmpeg must expose H.264 and HEVC VideoToolbox encoders." >&2
  exit 1
fi
if ! "$ffmpeg_path" -y -hide_banner -loglevel error \
  -f lavfi -i color=black:s=256x256:d=0.1:r=1 \
  -c:v h264_videotoolbox -allow_sw 0 -frames:v 1 -f null -; then
  if [[ "${MACOS_REQUIRE_HARDWARE_PROBE:-1}" == "1" ]]; then
    echo 'VideoToolbox hardware initialization failed.' >&2
    exit 1
  fi
  echo '::warning::Hosted runner did not expose a VideoToolbox compression session; package validation continues without claiming a hardware encode.' >&2
fi

pushd "$repo_root/frontend" >/dev/null
npm ci --no-audit --no-fund
npx svelte-kit sync
npm run check
npm run build
popd >/dev/null

build_root="$(mktemp -d "${TMPDIR:-/tmp}/8mblocal-macos.XXXXXX")"
trap 'rm -rf "$build_root"' EXIT
python3 -m venv "$build_root/venv"
venv_python="$build_root/venv/bin/python"
"$venv_python" -m pip install --upgrade pip
"$venv_python" -m pip install -r "$repo_root/requirements.txt"
"$venv_python" -m pip install 'pywebview==6.2.1' 'pyinstaller>=6.11,<7'

rm -rf "$repo_root/dist/8mblocal.app"
"$venv_python" -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name 8mblocal \
  --target-arch arm64 \
  --osx-bundle-identifier com.jms1717.eightmblocal \
  --distpath "$repo_root/dist" \
  --workpath "$build_root/pyinstaller-work" \
  --specpath "$build_root" \
  --paths "$repo_root/backend-api" \
  --paths "$repo_root" \
  --add-data "$repo_root/frontend/build:frontend-build" \
  --add-binary "$ffmpeg_path:bin" \
  --add-binary "$ffprobe_path:bin" \
  --collect-submodules app \
  --collect-submodules worker.app \
  --collect-submodules webview \
  --hidden-import shared.local_runtime \
  --hidden-import shared.subprocess_utils \
  --hidden-import celery.backends.cache \
  --hidden-import celery.loaders.app \
  --hidden-import kombu.transport.memory \
  --hidden-import worker.app.worker \
  --hidden-import worker.app.tasks \
  --hidden-import worker.app.startup_tests \
  "$repo_root/windows/desktop_app.py"

app_path="$repo_root/dist/8mblocal.app"
signing_identity="${MACOS_SIGNING_IDENTITY:--}"
if [[ "$signing_identity" == "-" ]]; then
  codesign --force --deep --options runtime --timestamp=none --sign "$signing_identity" "$app_path"
else
  codesign --force --deep --options runtime --timestamp --sign "$signing_identity" "$app_path"
fi
codesign --verify --deep --strict --verbose=2 "$app_path"

dmg_stage="$build_root/dmg"
mkdir -p "$dmg_stage"
cp -R "$app_path" "$dmg_stage/8mblocal.app"
ln -s /Applications "$dmg_stage/Applications"
dmg_path="$repo_root/dist/8mblocal_${version}_macos_arm64.dmg"
rm -f "$dmg_path"
for attempt in 1 2 3; do
  if hdiutil create -volname '8mb.local' -srcfolder "$dmg_stage" -ov -format UDZO "$dmg_path"; then
    break
  fi
  if [[ "$attempt" == "3" ]]; then
    echo 'Unable to create the macOS DMG after three attempts.' >&2
    exit 1
  fi
  echo "hdiutil create failed on attempt $attempt; retrying..." >&2
  rm -f "$dmg_path"
  sleep 3
done

if [[ -n "${APPLE_NOTARY_PROFILE:-}" ]]; then
  if [[ "$signing_identity" == "-" ]]; then
    echo 'A Developer ID identity is required when notarization is enabled.' >&2
    exit 1
  fi
  codesign --force --timestamp --sign "$signing_identity" "$dmg_path"
  notary_args=(--keychain-profile "$APPLE_NOTARY_PROFILE")
  if [[ -n "${APPLE_NOTARY_KEYCHAIN:-}" ]]; then
    notary_args+=(--keychain "$APPLE_NOTARY_KEYCHAIN")
  fi
  xcrun notarytool submit "$dmg_path" "${notary_args[@]}" --wait
  xcrun stapler staple "$dmg_path"
fi

shasum -a 256 "$dmg_path" > "$dmg_path.sha256"
echo "Built $dmg_path"
