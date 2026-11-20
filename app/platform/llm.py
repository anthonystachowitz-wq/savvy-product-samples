"""DeepSeek client with a recorded-run cache.

Every model output shown in the suite was produced by a real call and saved to
data/cached/llm/<module>/<key>.json. The demo replays those recordings by
default so a reviewer never sees a spinner fail. A live call is attempted first
when the deployment is in live mode and the daily budget allows it.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import config, rate_limit

MODE_LIVE = "live model, recorded"


class LLMUnavailable(RuntimeError):
    """No mode can serve this request right now."""


@dataclass
class LLMResult:
    text: str
    mode: str
    model: str
    usage: dict[str, Any]
    latency_ms: int
    created_at: str
    key: str
    module: str

    @property
    def is_live(self) -> bool:
        return self.mode == MODE_LIVE


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _cache_path(module: str, key: str) -> Path:
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in key)[:80]
    return config.CACHE_DIR / module / f"{safe}.json"


def _post(url: str, payload: dict[str, Any], api_key: str, timeout: int = 120) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def live_complete(
    system: str,
    prompt: str,
    *,
    model: Optional[str] = None,
    max_tokens: int = 900,
    temperature: float = 0.3,
) -> tuple[str, dict[str, Any], str, int]:
    settings = config.deepseek()
    if not settings["api_key"]:
        raise LLMUnavailable("No DeepSeek key is configured for this deployment.")
    used_model = model or settings["model"]
    payload = {
        "model": used_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    last_error: Optional[Exception] = None
    for attempt in range(3):
        started = time.time()
        try:
            data = _post(settings["base_url"] + "/chat/completions", payload, settings["api_key"])
            text = str(data["choices"][0]["message"]["content"]).strip()
            usage = data.get("usage") or {}
            returned_model = str(data.get("model") or used_model)
            return text, usage, returned_model, int((time.time() - started) * 1000)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise LLMUnavailable(f"DeepSeek call failed: {last_error}")


def recorded(module: str, key: str) -> Optional[LLMResult]:
    payload = _read_json(_cache_path(module, key))
    if not payload or not isinstance(payload.get("text"), str):
        return None
    return LLMResult(
        text=payload["text"],
        mode=str(payload.get("mode") or "recorded"),
        model=str(payload.get("model") or ""),
        usage=payload.get("usage") or {},
        latency_ms=int(payload.get("latency_ms") or 0),
        created_at=str(payload.get("created_at") or ""),
        key=key,
        module=module,
    )


def snapshot(module: str, key: str) -> Optional[dict[str, Any]]:
    """Return the raw recorded payload, for restoring after a failed live refresh."""
    return _read_json(_cache_path(module, key))


def restore(payload: Optional[dict[str, Any]]) -> None:
    """Put a previously snapshot payload back on disk."""
    if not payload:
        return
    module = str(payload.get("module") or "")
    key = str(payload.get("key") or "")
    if module and key and isinstance(payload.get("text"), str):
        _write_json(_cache_path(module, key), payload)


def run(
    module: str,
    key: str,
    system: str,
    prompt: str,
    *,
    model: Optional[str] = None,
    max_tokens: int = 900,
    temperature: float = 0.3,
    refresh: bool = False,
) -> LLMResult:
    """Serve a cached recording, or make and record a live call."""

    cached = None if refresh else recorded(module, key)
    if cached is not None:
        return cached
    if config.llm_mode() != "live":
        raise LLMUnavailable("This deployment runs recorded model output only.")
    rate_limit.check_and_increment(module)
    text, usage, used_model, latency_ms = live_complete(
        system, prompt, model=model, max_tokens=max_tokens, temperature=temperature
    )
    created_at = _now()
    payload = {
        "module": module,
        "key": key,
        "mode": MODE_LIVE,
        "model": used_model,
        "usage": usage,
        "latency_ms": latency_ms,
        "created_at": created_at,
        "system_sha": _sha(system),
        "prompt_sha": _sha(prompt),
        "text": text,
    }
    _write_json(_cache_path(module, key), payload)
    return LLMResult(
        text=text,
        mode=MODE_LIVE,
        model=used_model,
        usage=usage,
        latency_ms=latency_ms,
        created_at=created_at,
        key=key,
        module=module,
    )


def availability() -> dict[str, Any]:
    settings = config.deepseek()
    return {
        "mode": config.llm_mode(),
        "key_present": bool(settings["api_key"]),
        "model": settings["model"],
        "daily_limit": config.daily_llm_limit(),
        "used_today": rate_limit.used_today(),
    }