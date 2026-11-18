"""Model provider interface for the Savvy Transition Copilot sample.

The demo is deterministic by default: :class:`CachedProvider` replays text that was
written to ``data/cached/<key>.json`` during a warm run. A live Amazon Bedrock run
is opt-in through environment variables so nothing in the sample needs a key commit
or a console switch.

Configuration is environment only:

* ``SAVVY_LLM_MODE`` or ``LLM_MODE`` - ``cached`` (default) or ``bedrock``
* ``BEDROCK_MODEL_ID`` - required for the Bedrock provider
* ``AWS_REGION`` - required for the Bedrock provider
* ``SAVVY_DATA_DIR`` - optional data directory override (tests use it)
* ``SAVVY_CACHE_DIR`` - optional cache directory override (tests use it)

The module deliberately has no third-party import at module load. boto3 is imported
inside :class:`BedrockProvider` so the cached path and the unit tests stay light.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Cache roots and provenance vocabulary
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Honest provenance labels. These strings are shown in the demo UI, so they are
#: part of the product surface and should not be edited casually.
MODE_DETERMINISTIC_EXTRACT = "deterministic extractor"
MODE_DETERMINISTIC_TEMPLATE = "deterministic template"
MODE_RECORDED_MODEL = "live model, recorded"

#: Any label a recorded live model run may carry. Kept narrow on purpose: a cache
#: file that says "deterministic extractor" must never be read back as a model run.
MODEL_RUN_MODES = {
    MODE_RECORDED_MODEL,
    "live model",
    "bedrock",
    "recorded model run",
}


class ConfigurationError(RuntimeError):
    """Raised when a provider is asked for that is not configured."""


class CacheMissError(FileNotFoundError):
    """Raised when a cached key has no file (or the file has no text)."""


def _data_dir() -> Path:
    override = os.environ.get("SAVVY_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "data"


def cache_dir() -> Path:
    override = os.environ.get("SAVVY_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return _data_dir() / "cached"


def utc_now_iso() -> str:
    """Timestamp used in cache files and eval reports (UTC, second precision)."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_key(key: str) -> str:
    """Conservative key sanitiser: letters, digits, dot, underscore, dash."""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(key)).strip("-")
    return cleaned or "unnamed"


def cache_path(key: str, directory: Optional[Path] = None) -> Path:
    return (Path(directory) if directory is not None else cache_dir()) / f"{safe_key(key)}.json"


def load_cached(key: str, directory: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Return a cache entry dict, or None when the file is absent or unreadable."""

    path = cache_path(key, directory)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_cached(
    key: str,
    text: str,
    mode: str,
    generated_at: Optional[str] = None,
    directory: Optional[Path] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Write one cache entry and return it.

    The file always contains ``key``, ``generated_at``, ``mode`` and ``text``.
    ``extra`` is additive metadata (for example a word count for a draft) and never
    changes those four fields.
    """

    payload: dict[str, Any] = {
        "key": key,
        "generated_at": generated_at or utc_now_iso(),
        "mode": mode,
        "text": text,
    }
    if extra:
        for name, value in extra.items():
            if name not in payload:
                payload[name] = value
    path = cache_path(key, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def is_recorded_model_run(mode: Optional[str]) -> bool:
    """True only for a mode label that says a real model call happened.

    The extractor uses this to decide whether a cache file may replace the
    deterministic parser. A file written by the rule-based path is never treated
    as a model run, even if it sits at the model key.
    """

    if not mode:
        return False
    return str(mode).strip().lower() in MODEL_RUN_MODES


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class Provider:
    """Small protocol-style base class. ``complete`` is the only required method."""

    name = "provider"

    def complete(self, system: str, prompt: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "mode": getattr(self, "mode", self.name)}


class CachedProvider(Provider):
    """Replay a recorded completion from ``data/cached/<key>.json``.

    The demo uses this by default. A missing key is an error, not a silent empty
    string, because a blank completion would look like a model failure and make the
    provenance story dishonest.
    """

    name = "cached"

    def __init__(self, key: Optional[str] = None, directory: Optional[Path] = None):
        self.key = key
        self.directory = Path(directory) if directory is not None else None
        #: Mode of the last cache entry served, for provenance display.
        self.last_mode: Optional[str] = None
        self.last_generated_at: Optional[str] = None

    @property
    def mode(self) -> str:
        return "cached" if not self.last_mode else f"cached ({self.last_mode})"

    def for_key(self, key: str) -> "CachedProvider":
        return CachedProvider(key=key, directory=self.directory)

    def complete(self, system: str, prompt: str) -> str:
        del system, prompt  # a recorded completion ignores the live inputs by design
        if not self.key:
            raise ConfigurationError(
                "CachedProvider needs a cache key. Call CachedProvider('extract-doc-001') "
                "or get_provider('cached', key='...')."
            )
        entry = load_cached(self.key, self.directory)
        if entry is None:
            raise CacheMissError(
                f"No cache entry for {self.key!r} at {cache_path(self.key, self.directory)}. "
                "Run scripts/warm_cache.py first, or switch SAVVY_LLM_MODE=bedrock."
            )
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise CacheMissError(
                f"Cache entry {self.key!r} has no non-empty 'text' field."
            )
        self.last_mode = entry.get("mode")
        self.last_generated_at = entry.get("generated_at")
        return text


class BedrockProvider(Provider):
    """Amazon Bedrock Converse API provider.

    Disabled unless both ``BEDROCK_MODEL_ID`` and ``AWS_REGION`` are set. Credentials
    come from the normal AWS chain (instance role in the deployed demo). No key is
    read, written, or logged by this module.
    """

    name = "bedrock"

    def __init__(
        self,
        model_id: Optional[str] = None,
        region: Optional[str] = None,
        client: Any = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ):
        self.model_id = (model_id or os.environ.get("BEDROCK_MODEL_ID") or "").strip()
        self.region = (region or os.environ.get("AWS_REGION") or "").strip()
        missing = []
        if not self.model_id:
            missing.append("BEDROCK_MODEL_ID")
        if not self.region:
            missing.append("AWS_REGION")
        if missing:
            raise ConfigurationError(
                "Bedrock provider is not configured: set "
                + " and ".join(missing)
                + " (environment only; the demo stays on cached mode until then)."
            )
        self.max_tokens = max_tokens or int(os.environ.get("BEDROCK_MAX_TOKENS", "1200"))
        self.temperature = (
            temperature
            if temperature is not None
            else float(os.environ.get("BEDROCK_TEMPERATURE", "0.2"))
        )
        self._client = client

    @property
    def mode(self) -> str:
        return MODE_RECORDED_MODEL

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # imported lazily so cached mode has no boto3 dependency
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ConfigurationError(
                "boto3 is not installed. Install requirements.txt or stay in cached mode."
            ) from exc
        self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def complete(self, system: str, prompt: str) -> str:
        client = self._get_client()
        response = client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={
                "maxTokens": self.max_tokens,
                "temperature": self.temperature,
            },
        )
        blocks = response.get("output", {}).get("message", {}).get("content", [])
        parts = [block.get("text", "") for block in blocks if isinstance(block, dict)]
        text = "".join(parts).strip()
        if not text:
            raise ConfigurationError(
                "Bedrock returned an empty completion; check model access in "
                f"{self.region} for {self.model_id}."
            )
        return text


def get_provider(
    mode: Optional[str] = None,
    key: Optional[str] = None,
    directory: Optional[Path] = None,
) -> Provider:
    """Choose a provider. Cached is the default; Bedrock is opt-in.

    ``mode`` wins over the environment. Accepted cached aliases: cached, cache,
    recorded, replay. Accepted live aliases: bedrock, live.
    """

    selected = (
        mode
        or os.environ.get("SAVVY_LLM_MODE")
        or os.environ.get("LLM_MODE")
        or "cached"
    )
    selected = str(selected).strip().lower()
    if selected in {"cached", "cache", "recorded", "replay", "replay-cached"}:
        return CachedProvider(key=key, directory=directory)
    if selected in {"bedrock", "live", "live model", "model"}:
        return BedrockProvider()
    raise ConfigurationError(
        f"Unknown provider mode {selected!r}. Use 'cached' or 'bedrock'."
    )
