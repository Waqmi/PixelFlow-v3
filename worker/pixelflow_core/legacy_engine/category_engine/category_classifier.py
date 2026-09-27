"""Rule-first category classification with an optional AI provider boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CATEGORY_IDS = (
    "shirt",
    "pants",
    "long_pants",
    "shorts",
    "shoes",
    "hat",
    "socks",
    "gloves",
    "bag",
    "knee_pads",
    "other",
)


@dataclass(frozen=True)
class CategoryDecision:
    category: str
    confidence: float
    source: str
    matched_keyword: str | None = None


class AICategoryProvider(Protocol):
    """Future adapter for a local or remote AI classifier.

    The first release does not provide an implementation or make network calls.
    """

    def classify(self, product_root: Path) -> CategoryDecision | None:
        ...


class RuleCategoryClassifier:
    """Resolve manual selection, folder keywords, then optional AI."""

    KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("shoes", ("跑鞋", "越野鞋", "运动鞋", "鞋")),
        ("shorts", ("短裤", "短褲")),
        ("long_pants", ("长裤", "長褲")),
        ("pants", ("冲锋裤", "裤子", "裤")),
        ("socks", ("袜子", "袜")),
        ("hat", ("棒球帽", "帽子", "帽")),
        ("gloves", ("手套", "手套类")),
        ("bag", ("背包", "腰包", "包")),
        ("knee_pads", ("护膝", "护膝类", "膝套", "膝带")),
        ("shirt", ("冲锋衣", "外套", "夹克", "T恤", "卫衣", "上衣", "衣")),
    )

    def __init__(self, ai_provider: AICategoryProvider | None = None) -> None:
        self.ai_provider = ai_provider

    def classify(
        self,
        product_root: Path,
        manual_category: str | None = None,
    ) -> CategoryDecision:
        if manual_category and manual_category != "auto":
            if manual_category not in CATEGORY_IDS:
                raise ValueError(f"不支持的商品类别：{manual_category}")
            return CategoryDecision(manual_category, 1.0, "manual")

        name_texts = [product_root.name]
        try:
            name_texts.extend(
                path.relative_to(product_root).as_posix()
                for path in product_root.rglob("*")
                if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
            )
        except OSError:
            pass
        matches: dict[str, str] = {}
        for text in name_texts:
            for category, keywords in self.KEYWORDS:
                keyword = next((item for item in keywords if item in text), None)
                if keyword:
                    matches[category] = keyword
                    break
        if len(matches) == 1:
            category, keyword = next(iter(matches.items()))
            return CategoryDecision(category, 0.95, "name", keyword)
        if len(matches) > 1:
            return CategoryDecision(
                "other",
                0.0,
                "conflict",
                "、".join(sorted(matches)),
            )

        if self.ai_provider is not None:
            decision = self.ai_provider.classify(product_root)
            if decision is not None and decision.category in CATEGORY_IDS:
                return decision

        return CategoryDecision("other", 0.0, "fallback")
