from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def materialize_symlinks(root: Path) -> None:
    """Replace PyInstaller's macOS symlinks so Tauri can copy onedir resources."""
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.is_symlink():
            continue
        target = path.resolve(strict=True)
        path.unlink()
        if target.is_dir():
            shutil.copytree(target, path, symlinks=False)
        else:
            shutil.copy2(target, path)


def resolve_python(root: Path) -> str:
    configured = os.environ.get("PIXELFLOW_PYTHON")
    if configured:
        return configured
    local = root / ".venv" / "bin" / "python"
    if local.is_file():
        return str(local)
    return sys.executable


def main() -> int:
    root = Path(__file__).resolve().parent
    mode = os.environ.get("PIXELFLOW_WORKER_MODE", "ondir").strip().lower()
    onedir = mode != "onefile"
    suffix = "-ondir" if onedir else ""
    output = root / f"dist{suffix}"
    work = root / f"build{suffix}"
    config = Path(os.environ.get("PYINSTALLER_CONFIG_DIR", root / f".pyinstaller-config{suffix}"))
    python = resolve_python(root)
    spec = root / ("pixelflow-worker-ondir.spec" if onedir else "pixelflow-worker.spec")
    command = [
        python,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(output),
        "--workpath",
        str(work),
        str(spec),
    ]
    environment = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(config)}
    result = subprocess.run(command, cwd=root, env=environment)
    if result.returncode == 0 and onedir:
        materialize_symlinks(output)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
