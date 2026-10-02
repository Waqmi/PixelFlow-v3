"""Small, versioned disk cache for v3 Pose/Seg evidence.

The public helpers keep the v2 call shape while accepting the v3 identity
fields.  A normal lookup still returns the cached value (or ``None``); callers
that need an explanation can use ``lookup_model_result`` or pass
``return_status=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Tuple


CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
CACHE_DIR_NAME = "PixelFlow/model-results"
# Bump whenever crop/composition metadata changes.
CACHE_VERSION = 8
COMPOSITION_RULES_VERSION = "v3-composition-10"


def model_cache_dir() -> Path:
    """Return the private cache directory, never a product output folder."""
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return root / CACHE_DIR_NAME


def _file_version(path: Optional[Path], *, include_digest: bool = False) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    try:
        resolved = Path(path).expanduser().resolve()
        stat = resolved.stat()
    except (OSError, TypeError, ValueError):
        return None
    version = {
        "path": str(resolved),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }
    if include_digest:
        digest = hashlib.sha256()
        try:
            with resolved.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
        version["sha256"] = digest.hexdigest()
    return version


def file_version(path: Optional[Path], *, include_digest: bool = False) -> Optional[Dict[str, Any]]:
    """Public read-only source/model version helper."""
    return _file_version(path, include_digest=include_digest)


def _purpose(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return "segmentation" if normalized in {"seg", "segmentation"} else normalized


def _output_size(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return "{}x{}".format(value[0], value[1])
    return str(value)


def build_cache_identity(
    source: Path,
    model_path: Optional[Path],
    purpose: str,
    category: Optional[str] = None,
    output_size: Any = None,
    composition_rules_version: str = COMPOSITION_RULES_VERSION,
    pose_model_path: Optional[Path] = None,
    seg_model_path: Optional[Path] = None,
    pose_model_version: Optional[Any] = None,
    seg_model_version: Optional[Any] = None,
    detection_model_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Build the complete cache identity or return ``None`` for missing files."""
    normalized_purpose = _purpose(purpose)
    if normalized_purpose not in {"pose", "segmentation"}:
        return None
    source_version = _file_version(source, include_digest=True)
    if source_version is None:
        return None

    if model_path is not None:
        model_path = Path(model_path)
        if normalized_purpose == "pose" and pose_model_path is None:
            pose_model_path = model_path
        if normalized_purpose == "segmentation" and seg_model_path is None:
            seg_model_path = model_path

    pose_version = (
        pose_model_version
        if pose_model_version is not None
        else _file_version(pose_model_path)
    )
    seg_version = (
        seg_model_version
        if seg_model_version is not None
        else _file_version(seg_model_path)
    )
    # The purpose-specific model is required for an inference cache.  Keeping
    # both model versions in the identity prevents a stale mixed Pose/Seg
    # result when a caller supplies both paths.
    selected_version = pose_version if normalized_purpose == "pose" else seg_version
    if selected_version is None:
        return None
    return {
        "cache_version": CACHE_VERSION,
        "source": source_version,
        "model": selected_version,
        "pose_model": pose_version,
        "seg_model": seg_version,
        # Optional shoe detector; None when absent so adding it invalidates.
        "detection_model": _file_version(detection_model_path),
        "purpose": normalized_purpose,
        "composition_rules_version": str(composition_rules_version),
        "category": str(category or ""),
        "output_size": _output_size(output_size),
    }


def _cache_key(
    source: Path,
    model_path: Optional[Path],
    purpose: str,
    category: Optional[str] = None,
    output_size: Any = None,
    composition_rules_version: str = COMPOSITION_RULES_VERSION,
    **kwargs: Any,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    identity = build_cache_identity(
        source,
        model_path,
        purpose,
        category,
        output_size,
        composition_rules_version,
        **kwargs,
    )
    if identity is None:
        return None
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), identity


def cache_key(*args: Any, **kwargs: Any) -> Optional[str]:
    keyed = _cache_key(*args, **kwargs)
    return keyed[0] if keyed is not None else None


def _cache_path(key: str) -> Path:
    return model_cache_dir() / (key + ".json")


@dataclass(frozen=True)
class CacheLookup:
    """Explain one lookup without forcing old callers to handle a wrapper."""

    result: Any = None
    status: str = "miss"
    reason: str = ""
    key: Optional[str] = None
    identity: Optional[Dict[str, Any]] = None

    @property
    def hit(self) -> bool:
        return self.status == "hit"

    @property
    def fallback(self) -> bool:
        return not self.hit

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "hit": self.hit,
            "fallback": self.fallback,
            "key": self.key,
            "identity": self.identity,
            "result": self.result,
        }


def lookup_model_result(
    source: Path,
    model_path: Optional[Path],
    purpose: str,
    category: Optional[str] = None,
    output_size: Any = None,
    composition_rules_version: str = COMPOSITION_RULES_VERSION,
    **kwargs: Any,
) -> CacheLookup:
    """Read a cache entry and report why a miss happened."""
    keyed = _cache_key(
        source,
        model_path,
        purpose,
        category,
        output_size,
        composition_rules_version,
        **kwargs,
    )
    if keyed is None:
        if _file_version(source) is None:
            return CacheLookup(status="source_missing", reason="source file is unavailable")
        normalized = _purpose(purpose)
        model = model_path
        if model is None:
            model = kwargs.get("pose_model_path") if normalized == "pose" else kwargs.get("seg_model_path")
        return CacheLookup(status="model_missing", reason="model file is unavailable") if _file_version(model) is None else CacheLookup(status="invalid_identity", reason="unsupported cache identity")
    key, identity = keyed
    path = _cache_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CacheLookup(status="miss", reason="cache entry does not exist", key=key, identity=identity)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        try:
            path.unlink()
        except OSError:
            pass
        return CacheLookup(status="invalid", reason="cache entry is unreadable: {}".format(error), key=key, identity=identity)
    try:
        created_at = datetime.fromisoformat(str(payload.get("created_at")))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created_at).total_seconds()
    except (TypeError, ValueError, AttributeError):
        age = CACHE_TTL_SECONDS + 1
    if age < 0 or age > CACHE_TTL_SECONDS:
        try:
            path.unlink()
        except OSError:
            pass
        return CacheLookup(status="expired", reason="cache entry exceeded TTL", key=key, identity=identity)
    if payload.get("identity") != identity or "result" not in payload:
        try:
            path.unlink()
        except OSError:
            pass
        return CacheLookup(status="invalid", reason="cache identity does not match", key=key, identity=identity)
    return CacheLookup(payload.get("result"), "hit", "", key, identity)


def load_model_result(
    source: Path,
    model_path: Optional[Path],
    purpose: str,
    category: Optional[str] = None,
    output_size: Any = None,
    composition_rules_version: str = COMPOSITION_RULES_VERSION,
    return_status: bool = False,
    **kwargs: Any,
) -> Any:
    """Compatibility loader; use ``return_status`` for explicit cache state."""
    lookup = lookup_model_result(
        source,
        model_path,
        purpose,
        category,
        output_size,
        composition_rules_version,
        **kwargs,
    )
    if return_status:
        return lookup
    return lookup.result if lookup.hit else None


def store_model_result(
    source: Path,
    model_path: Optional[Path],
    purpose: str,
    category: Optional[str],
    result: Any,
    output_size: Any = None,
    composition_rules_version: str = COMPOSITION_RULES_VERSION,
    **kwargs: Any,
) -> bool:
    keyed = _cache_key(
        source,
        model_path,
        purpose,
        category,
        output_size,
        composition_rules_version,
        **kwargs,
    )
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
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            temporary.unlink()
        except OSError:
            pass
        return False


def clear_model_result_cache() -> int:
    removed = 0
    cache_dir = model_cache_dir()
    for path in list(cache_dir.glob("*.json")) + list(cache_dir.glob("*.tmp")):
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


__all__ = [
    "CACHE_TTL_SECONDS",
    "CACHE_VERSION",
    "COMPOSITION_RULES_VERSION",
    "CacheLookup",
    "build_cache_identity",
    "cache_key",
    "clear_model_result_cache",
    "file_version",
    "load_model_result",
    "lookup_model_result",
    "model_cache_dir",
    "store_model_result",
]
