# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


worker_root = Path(SPECPATH)
v3_resource_root = worker_root / "resources"
legacy_root = worker_root / "pixelflow_core" / "legacy_engine"
hiddenimports = [
    "pixelflow_core.renderer",
    "pixelflow_core.model_assistant",
    "pixelflow_core.legacy_engine.generate_main_images",
    "pixelflow_core.legacy_engine.local_model_assistant",
    "pixelflow_core.legacy_engine.model_process_service",
    "pixelflow_core.legacy_engine.model_result_cache",
    "pixelflow_core.legacy_engine.output_validator",
    "pixelflow_core.legacy_engine.ai_image_assistant",
    *collect_submodules("pixelflow_core.legacy_engine.category_engine"),
    *collect_submodules("pixelflow_core"),
]

datas = []
if v3_resource_root.is_dir():
    datas.append((str(v3_resource_root), "resources"))
if legacy_root.is_dir() and (legacy_root / "resources").is_dir():
    datas.append((str(legacy_root / "resources"), "pixelflow_core/legacy_engine/resources"))
pathex = [str(worker_root), str(legacy_root)]

a = Analysis(
    [str(worker_root / "worker_entry.py")],
    pathex=pathex,
    binaries=collect_dynamic_libs("onnxruntime"),
    datas=datas,
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
    [],
    exclude_binaries=True,
    name="pixelflow-worker",
    debug=False,
    console=False,
    strip=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pixelflow-worker",
)
