from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_frozen_paths() -> None:
    if not getattr(sys, "frozen", False):
        return
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    v2_root = bundle_root / "v2_engine"
    os.environ.setdefault("PIXELFLOW_V2_ROOT", str(v2_root))
    os.environ.setdefault("PIXELFLOW_RESOURCE_ROOT", str(v2_root / "resources"))
    if str(v2_root) not in sys.path:
        sys.path.insert(0, str(v2_root))


configure_frozen_paths()

from pixelflow_core.worker import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
