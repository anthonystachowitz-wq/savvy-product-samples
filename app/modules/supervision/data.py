"""Load the supervision module data files.

The three files under data/modules/supervision are written by
scripts/generate_supervision.py from data/book.json. They are read only.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.platform import config

MODULE = "supervision"
MODULE_DIR = config.DATA_DIR / "modules" / MODULE


def _read(name: str) -> Any:
    path = MODULE_DIR / name
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def briefs_bundle() -> dict[str, Any]:
    return _read("briefs.json")


@lru_cache(maxsize=1)
def fact_sheet() -> dict[str, Any]:
    return _read("fact_sheet.json")


@lru_cache(maxsize=1)
def eval_cases() -> dict[str, Any]:
    return _read("eval_cases.json")


@lru_cache(maxsize=1)
def book() -> dict[str, Any]:
    path = config.DATA_DIR / "book.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def households_by_id() -> dict[str, dict[str, Any]]:
    return {household["id"]: household for household in book().get("households", [])}


def eval_report() -> dict[str, Any] | None:
    """The eval output is a build artifact, so read it fresh each time."""

    path = MODULE_DIR / "eval.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def recording_keys() -> list[str]:
    """Keys the page needs, in the order the recorder writes them."""

    bundle = briefs_bundle()
    keys: list[str] = []
    for brief in bundle["briefs"]:
        keys.append(brief["draft_key"])
        keys.append(brief["claims_key"])
        if brief.get("revision_key"):
            keys.append(brief["revision_key"])
        if brief.get("revision_claims_key"):
            keys.append(brief["revision_claims_key"])
    keys.append(eval_cases().get("key", "eval-claims"))
    return keys


def recordings_present() -> int:
    cache = config.CACHE_DIR / MODULE
    if not cache.is_dir():
        return 0
    return len(list(cache.glob("*.json")))


def reload() -> None:
    for function in (briefs_bundle, fact_sheet, eval_cases, book):
        function.cache_clear()
