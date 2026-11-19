"""Paths and environment for the suite platform.

The DeepSeek key is never stored in this repository. Locally it is read from
~/.config/savvy-demo/deepseek.env. In deployment it comes from the environment.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("SAVVY_DATA_DIR", str(REPO_ROOT / "data")))
STATE_DIR = DATA_DIR / "state"
CACHE_DIR = DATA_DIR / "cached" / "llm"
LOCAL_ENV = Path.home() / ".config" / "savvy-demo" / "deepseek.env"

_loaded_local_env = False


def _load_local_env() -> None:
    global _loaded_local_env
    if _loaded_local_env:
        return
    _loaded_local_env = True
    try:
        if not LOCAL_ENV.is_file():
            return
        for line in LOCAL_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
    except OSError:
        return


def deepseek() -> dict[str, Any]:
    _load_local_env()
    return {
        "api_key": os.environ.get("DEEPSEEK_API_KEY", "").strip(),
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/"),
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip(),
        "pro_model": os.environ.get("DEEPSEEK_PRO", "deepseek-v4-pro").strip(),
    }


def llm_mode() -> str:
    mode = os.environ.get("SAVVY_LLM_MODE", "").strip().lower()
    if mode in {"live", "cached"}:
        return mode
    return "live" if deepseek()["api_key"] else "cached"


def daily_llm_limit() -> int:
    try:
        return max(0, int(os.environ.get("SAVVY_LLM_DAILY_LIMIT", "200")))
    except ValueError:
        return 200
