#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PIXELFLOW_PYTHON:-$ROOT/worker/.venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
  echo "PixelFlow worker build requires an executable Python: $PYTHON" >&2
  echo "Set PIXELFLOW_PYTHON or create worker/.venv (CI uses Python 3.12)." >&2
  exit 2
fi

"$PYTHON" -c 'import platform, sys; print(f"PixelFlow worker Python {platform.python_version()} ({sys.executable})")'
exec "$PYTHON" "$ROOT/worker/build_worker.py"
