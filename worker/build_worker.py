from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    output = root / "dist"
    work = root / "build"
    config = Path(os.environ.get("PYINSTALLER_CONFIG_DIR", root / ".pyinstaller-config"))
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(output),
        "--workpath",
        str(work),
        str(root / "pixelflow-worker.spec"),
    ]
    environment = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(config)}
    return subprocess.run(command, cwd=root, env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main())
