from __future__ import annotations

import os
import sys
import multiprocessing
from pathlib import Path

# ONNX Runtime ships an optional Microsoft telemetry client. PixelFlow is a
# local-only app, so disable it before any bundled runtime can initialize.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")


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
    multiprocessing.freeze_support()
    code = main()
    # os._exit skips atexit, so the model subprocess that Windows and Linux
    # spawn would outlive the worker and keep the bundled executables locked.
    # Close it explicitly; on macOS the model work runs in-process, so this is
    # a no-op. Imported lazily because the module may never have been loaded.
    model_service = sys.modules.get("pixelflow_core.model_service")
    if model_service is not None:
        model_service.get_model_process_service().close()
    # Skip native teardown: onnxruntime's telemetry thread can abort the
    # process while static objects are destroyed.
    import logging

    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
