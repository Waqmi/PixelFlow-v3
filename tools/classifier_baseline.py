#!/usr/bin/env python3
"""Snapshot material classification, or diff two snapshots.

The confirm window and the render fallback used to run two different
classifiers.  Before merging them this tool records what the production
classifier actually decides, so the merge can be proved byte-identical on the
categories that must not regress (shirt / shoes / pants / long_pants).

    # record the baseline from the current production adapter
    tools/classifier_baseline.py --adapter v26 --out /tmp/base.json

    # record the merged classifier and compare
    tools/classifier_baseline.py --adapter main --out /tmp/main.json
    tools/classifier_baseline.py --compare /tmp/base.json --out /tmp/main.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "worker"))

GATED_CATEGORIES = ("shirt", "shoes", "pants", "long_pants")
DEFAULT_SUITE = REPO.parent / "05_测试素材与输出"
# Every product folder that has a category word in its own name, plus the whole
# per-category white-background set.
DEFAULT_ROOTS = (
    "各品类产品白底图",
    "上衣",
    "鞋",
    "帽子",
    "手套",
    "护膝",
    "长袜",
    "热能长裤",
    "速干短裤",
    "冲锋裤主图",
)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _round(value):
    """Model output is float-heavy; 1e-6 is far below any visible difference."""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _round(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(item) for item in value]
    return value


def _plan_payload(plan) -> dict[str, object]:
    return _round(
        {
            "composition_mode": plan.composition_mode,
            "scale": plan.scale,
            "position_x": plan.position_x,
            "position_y": plan.position_y,
            "crop_ratio": plan.crop_ratio,
            "crop_box": list(plan.crop_box) if plan.crop_box is not None else None,
            "protected_regions": [
                {
                    "kind": region.kind,
                    "bbox": list(region.bbox),
                    "source": region.source,
                    "required": bool(region.required),
                    "confidence": region.confidence,
                }
                for region in plan.protected_regions
            ],
            "risk_flags": list(plan.risk_flags),
            "requires_review": bool(plan.requires_review),
            "skipped_by_scope": bool(plan.skipped_by_scope),
            "fallback": bool(plan.fallback),
            "fallback_reason": plan.fallback_reason,
        }
    )


def _decision_payload(decision, root: Path) -> dict[str, object]:
    source = Path(decision.source)
    try:
        relative = source.relative_to(root).as_posix()
    except ValueError:
        relative = source.as_posix()
    metadata = decision.metadata or {}
    evidence_keys = (
        "cut_sides",
        "model_view",
        "anchor_segmentation_used",
        "category",
        "pose_evidence",
        "seg_evidence",
        "segmentation_evidence",
        "body_visibility",
    )
    return _round(
        {
            "source": relative,
            "source_type": decision.source_type,
            "confidence": decision.confidence,
            "classifier": decision.classifier,
            "reason": decision.reason,
            "fallback": bool(decision.fallback),
            "fallback_reason": decision.fallback_reason,
            "skipped_by_scope": bool(decision.skipped_by_scope),
            "evidence": _jsonable({key: metadata.get(key) for key in evidence_keys if key in metadata}),
            "plans": {
                size: _plan_payload(plan)
                for size, plan in sorted(decision.composition_plans.items())
            },
        }
    )


def _has_images(directory: Path) -> bool:
    return any(
        entry.is_file() and entry.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        for entry in directory.iterdir()
    )


def _iter_roots(suite: Path, names) -> list[Path]:
    """Expand a suite folder into product roots.

    A folder is only split when every child is itself a product folder, i.e. it
    holds images directly.  ``冲锋裤主图`` also contains the generated
    ``成品主图`` tree, so it stays one root and ``_material_sources`` applies the
    production skip rule.
    """
    roots: list[Path] = []
    for name in names:
        candidate = suite / name
        if not candidate.is_dir():
            raise SystemExit(f"missing suite root: {candidate}")
        children = [entry for entry in sorted(candidate.iterdir()) if entry.is_dir()]
        if children and all(_has_images(child) for child in children):
            roots.extend(children)
        else:
            roots.append(candidate)
    return roots


def _build_adapter(kind: str):
    if kind == "v26":
        from pixelflow_core.classification import V26ClassifierAdapter

        return V26ClassifierAdapter()
    from pixelflow_core.material_classifier import MaterialClassifier

    return MaterialClassifier()


def _resolve_category(adapter, root: Path, sources, manual: str | None):
    """Mirror how each entry point resolves the category before Pose/Seg."""
    if hasattr(adapter, "resolve_category"):
        return adapter.resolve_category(root, sources, manual)
    from pixelflow_core.legacy_engine.category_engine.category_classifier import (
        RuleCategoryClassifier,
    )

    return RuleCategoryClassifier().classify(root, manual)


def snapshot(adapter_kind: str, roots, model_root: Path | None, workers: int) -> dict[str, object]:
    from pixelflow_core.classification import ClassificationRequest, _material_sources

    adapter = _build_adapter(adapter_kind)
    payload: dict[str, object] = {
        "adapter": adapter_kind,
        "model_root": str(model_root) if model_root else None,
        "roots": [],
    }
    for root in roots:
        started = time.perf_counter()
        sources = tuple(_material_sources(root))
        category = _resolve_category(adapter, root, sources, None)
        request = ClassificationRequest(
            product_root=root,
            sources=sources,
            category=category.category,
            local_model_enabled=model_root is not None,
            local_model_path=str(model_root) if model_root else None,
            workers=workers,
        )
        decisions = adapter.classify(request)
        payload["roots"].append(
            {
                "root": root.name,
                "category": {
                    "category": category.category,
                    "confidence": _round(category.confidence),
                    "source": category.source,
                    "matched_keyword": category.matched_keyword,
                },
                "count": len(decisions),
                "seconds": round(time.perf_counter() - started, 3),
                "decisions": [
                    _decision_payload(item, root)
                    for item in sorted(decisions, key=lambda entry: entry.source)
                ],
            }
        )
        print(
            f"{root.name}: {len(decisions)} 张 / {payload['roots'][-1]['seconds']}s / "
            f"{category.category}",
            file=sys.stderr,
        )
    return payload


def compare(baseline: dict, candidate: dict) -> dict[str, object]:
    def index(payload):
        return {entry["root"]: entry for entry in payload.get("roots", [])}

    base_roots, new_roots = index(baseline), index(candidate)
    report: dict[str, object] = {
        "baseline": baseline.get("adapter"),
        "candidate": candidate.get("adapter"),
        "missing_roots": sorted(set(base_roots) - set(new_roots)),
        "extra_roots": sorted(set(new_roots) - set(base_roots)),
        "roots": [],
        "gated_differences": [],
        "other_differences": [],
    }
    for name in sorted(set(base_roots) & set(new_roots)):
        base, new = base_roots[name], new_roots[name]
        category = base["category"]["category"]
        entry: dict[str, object] = {"root": name, "category": category, "differences": []}
        if base["category"] != new["category"]:
            entry["differences"].append(
                {"source": "<category>", "field": "category", "baseline": base["category"], "candidate": new["category"]}
            )
        base_items = {item["source"]: item for item in base["decisions"]}
        new_items = {item["source"]: item for item in new["decisions"]}
        for source in sorted(set(base_items) | set(new_items)):
            old, fresh = base_items.get(source), new_items.get(source)
            if old is None or fresh is None:
                entry["differences"].append(
                    {"source": source, "field": "presence", "baseline": old is not None, "candidate": fresh is not None}
                )
                continue
            for field in ("source_type", "classifier", "reason", "fallback", "evidence", "plans", "confidence"):
                if old[field] != fresh[field]:
                    entry["differences"].append(
                        {"source": source, "field": field, "baseline": old[field], "candidate": fresh[field]}
                    )
        if entry["differences"]:
            bucket = "gated_differences" if category in GATED_CATEGORIES else "other_differences"
            report[bucket].append(entry)
        report["roots"].append({"root": name, "category": category, "differences": len(entry["differences"])})
    report["identical"] = not report["gated_differences"] and not report["other_differences"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("v26", "main"), default="main")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--roots", nargs="*", default=list(DEFAULT_ROOTS))
    parser.add_argument("--models", type=Path, default=REPO / "models")
    parser.add_argument("--no-models", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--compare", type=Path, help="baseline snapshot to diff the fresh run against")
    args = parser.parse_args()

    model_root = None if args.no_models or not args.models.is_dir() else args.models.resolve()
    roots = _iter_roots(args.suite.resolve(), args.roots)
    payload = snapshot(args.adapter, roots, model_root, args.workers)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"snapshot: {args.out} ({sum(entry['count'] for entry in payload['roots'])} 张)", file=sys.stderr)

    if args.compare is None:
        return 0
    report = compare(json.loads(args.compare.read_text(encoding="utf-8")), payload)
    report_path = args.out.with_name(args.out.stem + "-diff.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "identical": report["identical"],
                "gated_roots": len(report["gated_differences"]),
                "other_roots": len(report["other_differences"]),
                "missing_roots": report["missing_roots"],
                "diff_file": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    for entry in report["gated_differences"][:10]:
        print(f"GATED {entry['root']} ({entry['category']}): {entry['differences'][:3]}", file=sys.stderr)
    for entry in report["other_differences"][:10]:
        print(f"OTHER {entry['root']} ({entry['category']}): {entry['differences'][:3]}", file=sys.stderr)
    return 0 if report["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
