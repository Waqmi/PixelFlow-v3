from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
GENERATED_OUTPUT_MARKERS = {"唯品专享1440", "卖点图", "1440x1440", "1440x1920", "1125x1500"}
ClassificationProgress = Callable[[int, int, Path, "ClassificationDecision"], None]


@dataclass(frozen=True)
class ClassificationRequest:
    """Qt-free material classification request."""

    product_root: Path
    source_filter: set[str] | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    workers: int = 1

    def validate(self) -> None:
        if not self.product_root.is_dir():
            raise ValueError(f"product_root is not a directory: {self.product_root}")


@dataclass(frozen=True)
class ClassificationDecision:
    source: str
    source_type: str
    confidence: float
    classifier: str
    reason: str
    metadata: dict[str, object] | None = None


def _material_sources(product_root: Path) -> list[Path]:
    sources: list[Path] = []

    def looks_like_generated_output(directory: Path) -> bool:
        if directory.name not in {"成品主图", "产品主图", "模特图"}:
            return False
        try:
            child_names = {
                entry.name for entry in directory.rglob("*") if entry.is_dir()
            }
        except OSError:
            return False
        return bool(child_names & GENERATED_OUTPUT_MARKERS)

    def collect(directory: Path) -> None:
        if directory != product_root and looks_like_generated_output(directory):
            return
        try:
            entries = tuple(sorted(directory.iterdir(), key=lambda path: path.name))
        except OSError:
            return
        sources.extend(
            entry
            for entry in entries
            if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS
        )
        for entry in entries:
            if entry.is_dir() and not entry.is_symlink() and not entry.name.startswith("."):
                collect(entry)

    collect(product_root)
    return sorted(sources, key=lambda path: str(path))


def _matches_filter(source: Path, source_filter: set[str] | None) -> bool:
    return source_filter is None or any(
        key in source_filter for key in (str(source), source.as_posix())
    )


class V26ClassifierAdapter:
    """Use v2.6's rule classifier without importing Qt or its UI."""

    def __init__(self, v2_root: Path | None = None) -> None:
        self.v2_root = (v2_root or self._default_v2_root()).resolve()
        self._classifier_type = None

    @staticmethod
    def _default_v2_root() -> Path:
        configured = os.environ.get("PIXELFLOW_V2_ROOT")
        if configured:
            return Path(configured).expanduser()
        return Path(__file__).resolve().parents[3] / "PixelFlow-App-v2.0"

    def _load_classifier(self):
        if self._classifier_type is None:
            if not self.v2_root.is_dir():
                raise FileNotFoundError(f"v2.6 engine root not found: {self.v2_root}")
            root_text = str(self.v2_root)
            if root_text not in sys.path:
                sys.path.insert(0, root_text)
            from category_engine.source_type_classifier import RuleSourceTypeClassifier

            self._classifier_type = RuleSourceTypeClassifier
        return self._classifier_type

    def classify(
        self,
        request: ClassificationRequest,
        progress: ClassificationProgress | None = None,
    ) -> list[ClassificationDecision]:
        request.validate()
        sources = [
            source
            for source in _material_sources(request.product_root)
            if _matches_filter(source, request.source_filter)
        ]
        classifier = self._load_classifier()()
        decisions = classifier.classify_many(
            sources,
            overrides=request.overrides,
            workers=max(1, int(request.workers or 1)),
            progress=(
                lambda completed, total, source, decision: progress(
                    completed,
                    total,
                    source,
                    ClassificationDecision(
                        source=str(source),
                        source_type=decision.source_type,
                        confidence=float(decision.confidence),
                        classifier=decision.source,
                        reason=decision.reason,
                        metadata=decision.metadata,
                    ),
                )
                if progress is not None
                else None
            ),
        )
        return [
            ClassificationDecision(
                source=str(source),
                source_type=decision.source_type,
                confidence=float(decision.confidence),
                classifier=decision.source,
                reason=decision.reason,
                metadata=decision.metadata,
            )
            for source, decision in decisions.items()
        ]
