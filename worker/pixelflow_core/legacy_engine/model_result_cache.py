"""Small disk cache for repeatable Pose/Seg evidence during public testing."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
CACHE_DIR_NAME = "PixelFlow/model-results"
# Bump when composition metadata changes so old shoe crop boxes are not reused.
CACHE_VERSION = 7


def model_cache_dir() -> Path:
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / CACHE_DIR_NAME


def _file_version(path: Path) -> dict[str, object] | None:
    try:
        stat = path.expanduser().resolve().stat()
    except OSError:
        return None
    return {
        "path": str(path.expanduser().resolve()),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def _cache_key(
    source: Path,
    model_path: Path,
    purpose: str,
    category: str | None,
) -> tuple[str, dict[str, object]] | None:
    source_version = _file_version(source)
    model_version = _file_version(model_path)
    if source_version is None or model_version is None:
        return None
    identity = {
        "cache_version": CACHE_VERSION,
        "source": source_version,
        "model": model_version,
        "purpose": purpose,
        "category": category or "",
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), identity


def _cache_path(key: str) -> Path:
    return model_cache_dir() / f"{key}.json"


def load_model_result(
    source: Path,
    model_path: Path,
    purpose: str,
    category: str | None,
) -> Any | None:
    keyed = _cache_key(source, model_path, purpose, category)
    if keyed is None:
        return None
    key, identity = keyed
    path = _cache_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(str(payload.get("created_at")))
        age = (datetime.now(timezone.utc) - created_at).total_seconds()
        if age < 0 or age > CACHE_TTL_SECONDS or payload.get("identity") != identity:
            path.unlink(missing_ok=True)
            return None
        return payload.get("result")
    except (OSError, TypeError, ValueError, json.JSONDecodeError, AttributeError):
        return None


def store_model_result(
    source: Path,
    model_path: Path,
    purpose: str,
    category: str | None,
    result: Any,
) -> bool:
    keyed = _cache_key(source, model_path, purpose, category)
    if keyed is None:
        return False
    key, identity = keyed
    cache_dir = model_cache_dir()
    path = _cache_path(key)
    temporary = path.with_suffix(".tmp")
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "identity": identity,
        "result": result,
    }
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def clear_model_result_cache() -> int:
    removed = 0
    cache_dir = model_cache_dir()
    for path in cache_dir.glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    try:
        cache_dir.rmdir()
    except OSError:
        pass
    return removed
