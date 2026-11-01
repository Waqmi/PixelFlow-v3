from __future__ import annotations

import hashlib
import os
import unicodedata
from pathlib import Path


def original_output_stem(source: Path) -> str:
    """Return the historical stem while preserving the input image kind."""
    if source.suffix.lower() == ".png":
        return f"{source.stem}_png"
    return f"{source.stem}_jpg"


def unique_output_stems(
    sources: list[Path], product_root: Path, base_stems: dict[Path, str]
) -> dict[Path, str]:
    """Keep familiar names and add deterministic suffixes only for collisions."""
    root = product_root.expanduser().absolute()
    path_keys: dict[Path, str] = {}
    for source in sources:
        absolute = source.expanduser().absolute()
        try:
            relative = absolute.relative_to(root)
        except ValueError:
            relative = absolute
        path_keys[source] = relative.as_posix()

    owners: dict[str, list[Path]] = {}
    for source in sources:
        key = unicodedata.normalize("NFD", base_stems[source]).casefold()
        owners.setdefault(key, []).append(source)

    colliding = {
        source
        for group in owners.values()
        if len(group) > 1
        for source in group
    }
    names = {
        source: base_stems[source]
        for source in sources
        if source not in colliding
    }
    used = {
        unicodedata.normalize("NFD", name).casefold()
        for name in names.values()
    }
    for source in sorted(colliding, key=lambda item: (path_keys[item], str(item))):
        digest = hashlib.sha256(os.fsencode(path_keys[source])).hexdigest()
        for length in range(8, 65, 4):
            candidate = f"{base_stems[source]}_{digest[:length]}"
            key = unicodedata.normalize("NFD", candidate).casefold()
            if key not in used:
                names[source] = candidate
                used.add(key)
                break
        else:
            raise ValueError("could not assign unique output stems")
    return names
