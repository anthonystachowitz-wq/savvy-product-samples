"""Tiny per-module JSON state store for approvals and demo state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import config


def _path(module: str) -> Path:
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in module)
    return config.STATE_DIR / f"{safe}.json"


def load(module: str, default: Any = None) -> Any:
    path = _path(module)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default if default is not None else {}


def save(module: str, payload: Any) -> None:
    path = _path(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def reset(module: str) -> None:
    try:
        _path(module).unlink()
    except OSError:
        pass


def mutate(module: str, function: Callable[[Any], Any], default: Any = None) -> Any:
    payload = load(module, default)
    updated = function(payload)
    if updated is not None:
        payload = updated
    save(module, payload)
    return payload
