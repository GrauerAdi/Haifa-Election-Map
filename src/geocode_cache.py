# -*- coding: utf-8 -*-
"""Resumable disk cache for geocoding results, keyed by full address string."""

import json
from pathlib import Path


def load_cache(path):
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
