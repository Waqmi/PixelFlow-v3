# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


worker_root = Path(SPECPATH)
v2_root = worker_root.parent.parent / "PixelFlow-App-v2.0"
hiddenimports = [
    "generate_main_images",
    "local_model_assistant",
    "model_process_service",
    "model_result_cache",
    "output_validator",
    "ai_image_assistant",
    *collect_submodules("category_engine"),
]

a = Analysis(
    [str(worker_root / "worker_entry.py")],
    pathex=[str(worker_root), str(v2_root)],
    binaries=collect_dynamic_libs("onnxruntime"),
    datas=[(str(v2_root / "resources"), "v2_engine/resources")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "PyQt5", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pixelflow-worker",
    debug=False,
    console=True,
    strip=False,
    upx=False,
)
