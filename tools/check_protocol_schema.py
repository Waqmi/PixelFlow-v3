#!/usr/bin/env python3
"""Keep the generated-facing TypeScript protocol lists aligned with the JSON schema."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "packages/protocol/schema.json"
PROTOCOL = ROOT / "apps/desktop/src/workerProtocol.ts"


def values(source: str, name: str) -> set[str]:
    match = re.search(rf"export const {name} = \[(.*?)\] as const", source, re.S)
    if not match:
        raise ValueError(f"missing {name}")
    return set(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))


def main() -> int:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    source = PROTOCOL.read_text(encoding="utf-8")
    schema_types = set(schema["properties"]["type"]["enum"])
    request_types = values(source, "WORKER_REQUEST_TYPES")
    event_types = values(source, "WORKER_EVENT_TYPES")
    expected = request_types | event_types
    if request_types & event_types or schema_types != expected:
        print(json.dumps({
            "schema_only": sorted(schema_types - expected),
            "typescript_only": sorted(expected - schema_types),
            "duplicates": sorted(request_types & event_types),
        }, ensure_ascii=False))
        return 1
    print(json.dumps({"protocol": sorted(schema_types), "passed": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
