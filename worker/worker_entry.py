from __future__ import annotations

import multiprocessing


# PyInstaller's spawned child is re-entered through this module with
# ``--multiprocessing-fork``.  Handle that marker before importing/configuring
# the application, otherwise the child falls through to the worker stdin loop.
multiprocessing.freeze_support()

import os
import sys
from pathlib import Path


def configure_frozen_paths() -> None:
    if not getattr(sys, "frozen", False):
        return
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    resource_root = bundle_root / "pixelflow_core" / "legacy_engine" / "resources"
    if not resource_root.is_dir():
        resource_root = bundle_root / "resources"
    if resource_root.is_dir():
        os.environ.setdefault("PIXELFLOW_RESOURCE_ROOT", str(resource_root))


configure_frozen_paths()

from pixelflow_core.worker import main  # noqa: E402


if __name__ == "__main__":
    code = main()
    # Skip native teardown: onnxruntime's telemetry thread can abort the
    # process while static objects are destroyed.
    import logging

    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
