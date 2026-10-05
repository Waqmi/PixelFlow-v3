#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
PYTHON="${PIXELFLOW_PYTHON:-$ROOT/worker/.venv/bin/python}"
APP_NAME="PixelFlow-v3.0.app"
DMG_NAME="PixelFlow-v3.0-macOS-arm64.dmg"
APP_ONLY=0

if [[ "$#" -gt 1 ]]; then
  echo "usage: $0 [--app-only]" >&2
  exit 2
fi
if [[ "$#" -eq 1 ]]; then
  if [[ "$1" != "--app-only" ]]; then
    echo "usage: $0 [--app-only]" >&2
    exit 2
  fi
  APP_ONLY=1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS packaging requires Darwin" >&2
  exit 2
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "this entry point produces the macOS arm64 artifact; current architecture is $(uname -m)" >&2
  exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "worker Python not found: $PYTHON (set PIXELFLOW_PYTHON or create worker/.venv)" >&2
  exit 2
fi

mkdir -p "$DIST"

if [[ "${PIXELFLOW_SKIP_TAURI_BUILD:-0}" != "1" ]]; then
  (
    cd "$ROOT/apps/desktop"
    PIXELFLOW_PYTHON="$PYTHON" npm run tauri:build -- --bundles app
  )
fi

BUILT_APP="${PIXELFLOW_BUILT_APP:-}"
if [[ -z "$BUILT_APP" ]]; then
  BUILT_APP="$(find "${CARGO_TARGET_DIR:-$ROOT/apps/desktop/src-tauri/target}" -path '*/bundle/macos/PixelFlow.app' -type d -print | sort | tail -n 1)"
fi
if [[ -z "$BUILT_APP" || ! -d "$BUILT_APP" ]]; then
  echo "Tauri app bundle not found; set PIXELFLOW_BUILT_APP to an existing .app to package it" >&2
  exit 1
fi

APP_OUTPUT="$DIST/$APP_NAME"
rm -rf "$APP_OUTPUT"
ditto "$BUILT_APP" "$APP_OUTPUT"
codesign --force --deep --sign - "$APP_OUTPUT"
codesign --verify --deep --strict "$APP_OUTPUT"

if [[ "$APP_ONLY" == "1" ]]; then
  echo "app: $APP_OUTPUT"
  exit 0
fi

STAGING="$(mktemp -d "${TMPDIR:-/tmp}/pixelflow-dmg.XXXXXX")"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT
ditto "$APP_OUTPUT" "$STAGING/$APP_NAME"
codesign --force --deep --sign - "$STAGING/$APP_NAME"
codesign --verify --deep --strict "$STAGING/$APP_NAME"

find "$DIST" -maxdepth 1 -type f -name 'rw.*.dmg' -delete

mkdir -p "$STAGING/models/pose" "$STAGING/models/segmentation" "$STAGING/models/detection"
for relative in \
  "pose/yolo11n-pose.onnx" \
  "segmentation/yolo11n-seg.onnx" \
  "detection/yolov8m-worldv2-pixelflow.onnx" \
  "detection/yolov8m-worldv2-pixelflow.classes.json"; do
  if [[ ! -f "$ROOT/models/$relative" ]]; then
    echo "active model is missing: models/$relative" >&2
    exit 1
  fi
  cp "$ROOT/models/$relative" "$STAGING/models/$relative"
done
cp "$ROOT/models/README.md" "$ROOT/models/CLAUDE.md" "$STAGING/models/"
ln -s /Applications "$STAGING/Applications"
ln -s "$HOME/Documents" "$STAGING/文稿"
cat > "$STAGING/安装说明.txt" <<'EOF'
PixelFlow v3 安装

1. 将 PixelFlow-v3.0.app 拖到“应用程序”。
2. 将 models 文件夹拖到“文稿”。
3. 启动 App 后，设置页会自动发现“文稿/models”；也可以手动选择模型目录。
EOF

if [[ "${PIXELFLOW_DRY_RUN:-0}" == "1" ]]; then
  echo "dry-run staging:"
  find "$STAGING" -maxdepth 3 -print | sed "s#^$STAGING#.#" | sort
  exit 0
fi

DMG="$DIST/$DMG_NAME"
rm -f "$DMG"
hdiutil create -volname "PixelFlow v3.0" -srcfolder "$STAGING" -format UDZO -ov "$DMG" >/dev/null
hdiutil verify "$DMG"
find "$DIST" -maxdepth 1 -type f -name 'rw.*.dmg' -delete
echo "app: $DIST/$APP_NAME"
echo "dmg: $DMG"
