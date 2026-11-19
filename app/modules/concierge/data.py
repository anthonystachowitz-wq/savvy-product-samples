"""Load the generated Concierge data files.

Missing files degrade to empty payloads so the page can still render. The pages
say which file is missing instead of raising.
"""

from __future__ import annotations

import json
from typing import Any

from app.platform import config

MODULE = "concierge"
DATA_DIR = config.DATA_DIR / "modules" / MODULE
CACHE_DIR = config.CACHE_DIR / MODULE


def _load(name: str, default: dict[str, Any]) -> dict[str, Any]:
    path = DATA_DIR / name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(default)
    return payload if isinstance(payload, dict) else dict(default)


def records() -> dict[str, Any]:
    return _load("records.json", {"households": [], "disclosure": "", "as_of": "", "documents_excluded": []})


def questions() -> dict[str, Any]:
    return _load("questions.json", {"questions": [], "router": [], "keys": [], "households": []})


def eval_cases() -> dict[str, Any]:
    return _load("eval_cases.json", {"name": "", "rubric": "", "cases": []})


def metric() -> dict[str, Any]:
    return _load("metric.json", {})


def eval_report() -> dict[str, Any]:
    return _load("eval.json", {})


def household(payload: dict[str, Any], household_id: str) -> dict[str, Any] | None:
    for item in payload.get("households", []):
        if item.get("id") == household_id:
            return item
    return None


def key_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["key"]: item for item in payload.get("keys", []) if item.get("key")}


def question_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in payload.get("questions", []) if item.get("id")}


def presets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in payload.get("questions", []) if item.get("preset")]


def recordings() -> list[dict[str, Any]]:
    """Read the recorded runs for the provenance panel."""

    rows = []
    if not CACHE_DIR.is_dir():
        return rows
    for path in sorted(CACHE_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        usage = payload.get("usage") or {}
        rows.append(
            {
                "key": str(payload.get("key") or path.stem),
                "model": str(payload.get("model") or ""),
                "mode": str(payload.get("mode") or ""),
                "created_at": str(payload.get("created_at") or ""),
                "latency_ms": int(payload.get("latency_ms") or 0),
                "tokens": int(usage.get("total_tokens") or 0),
                "live": payload.get("mode") == "live model, recorded",
            }
        )
    return rows
