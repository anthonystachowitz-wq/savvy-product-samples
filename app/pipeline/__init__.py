"""Shared helpers for the Transition Copilot pipeline.

Only two things live here: tolerant readers for the seeded JSON (the data files are
written by another workstream and their exact key spelling may vary) and a handful of
formatting helpers used by more than one pipeline module. No pipeline logic, so the
modules stay importable in any order and there are no circular imports.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Data directory, overridable with ``SAVVY_DATA_DIR`` for tests."""

    import os

    override = os.environ.get("SAVVY_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / "data"


def load_json(path: str | Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` instead of raising when it is missing."""

    candidate = Path(path)
    if not candidate.is_file():
        return default
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_book(path: Optional[str | Path] = None) -> dict[str, Any]:
    """Load ``data/book.json`` (or an explicit path). Empty dict when absent."""

    payload = load_json(path or (data_dir() / "book.json"), default={})
    return payload if isinstance(payload, dict) else {}


def load_documents(directory: Optional[str | Path] = None) -> list[dict[str, Any]]:
    """Load every JSON file under ``data/documents``.

    A file may hold one document object or a list of them. Non-dict items are
    ignored. Files are read in name order so runs are reproducible.
    """

    root = Path(directory) if directory is not None else (data_dir() / "documents")
    found: list[dict[str, Any]] = []
    if not root.is_dir():
        return found
    for path in sorted(root.glob("*.json")):
        payload = load_json(path, default=None)
        if isinstance(payload, dict):
            # A wrapper such as {"documents": [...]} is common; flatten it.
            if isinstance(payload.get("documents"), list):
                for item in payload["documents"]:
                    if isinstance(item, dict):
                        found.append(_with_document_id(item, path))
            else:
                found.append(_with_document_id(payload, path))
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    found.append(_with_document_id(item, path))
    return found


def _with_document_id(document: dict[str, Any], path: Path) -> dict[str, Any]:
    """Return a copy with ``id`` set, derived from the filename when missing."""

    if document.get("id") or document.get("document_id") or document.get("doc_id"):
        return document
    copy = dict(document)
    copy["id"] = path.stem
    return copy


# ---------------------------------------------------------------------------
# Tolerant book accessors
# ---------------------------------------------------------------------------


def book_meta(book: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The book-level record. Accepts top-level fields or a nested ``book`` object."""

    if not isinstance(book, dict):
        return {}
    nested = book.get("book")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in book.items():
            if key not in {"book"} and key not in merged:
                merged[key] = value
        return merged
    return book


def households(book: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Households, derived from accounts when the export omits the households list."""

    if isinstance(book, dict) and isinstance(book.get("households"), list):
        return [item for item in book["households"] if isinstance(item, dict)]
    derived: dict[str, dict[str, Any]] = {}
    for account in accounts(book):
        household_id = account.get("household_id") or account.get("household")
        if household_id is None:
            continue
        key = str(household_id)
        if key not in derived:
            derived[key] = {
                "id": household_id,
                "display_name": account.get("household_name") or account.get("client_name") or key,
            }
    return list(derived.values())


def accounts(
    book: Optional[dict[str, Any]], household_id: Optional[str] = None
) -> list[dict[str, Any]]:
    if not isinstance(book, dict):
        return []
    raw = book.get("accounts")
    if not isinstance(raw, list):
        meta = book_meta(book)
        raw = meta.get("accounts") if isinstance(meta.get("accounts"), list) else []
    result = [item for item in raw if isinstance(item, dict)]
    if household_id is not None:
        wanted = str(household_id)
        result = [
            item
            for item in result
            if str(item.get("household_id") or item.get("household") or "") == wanted
        ]
    return result


def positions(
    book: Optional[dict[str, Any]], account_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """Positions for one account, or every position in the book.

    Handles positions nested in an account and positions stored once at book level
    with an ``account_id`` link. The nested copy wins when both exist.
    """

    collected: list[dict[str, Any]] = []
    for account in accounts(book):
        nested = account.get("positions")
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    copy = dict(item)
                    copy.setdefault("account_id", account.get("id"))
                    copy.setdefault("symbol", item.get("ticker") or item.get("symbol"))
                    collected.append(copy)
    if isinstance(book, dict) and isinstance(book.get("positions"), list):
        for item in book["positions"]:
            if not isinstance(item, dict):
                continue
            exists = any(
                str(candidate.get("account_id")) == str(item.get("account_id"))
                and str(candidate.get("symbol") or candidate.get("ticker") or "")
                == str(item.get("symbol") or item.get("ticker") or "")
                for candidate in collected
            )
            if not exists:
                collected.append(item)
    if account_id is not None:
        wanted = str(account_id)
        collected = [
            item
            for item in collected
            if str(item.get("account_id") or item.get("account") or "") == wanted
        ]
    return collected


def documents(book: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(book, dict) and isinstance(book.get("documents"), list):
        return [item for item in book["documents"] if isinstance(item, dict)]
    return []


def find_household(book: Optional[dict[str, Any]], household_id: str) -> Optional[dict[str, Any]]:
    wanted = str(household_id)
    for household in households(book):
        if str(household.get("id")) == wanted:
            return household
    # Also accept a display name, which the UI may pass by mistake.
    for household in households(book):
        if str(household.get("display_name", "")).strip().lower() == wanted.strip().lower():
            return household
    return None


def find_account(book: Optional[dict[str, Any]], account_id: str) -> Optional[dict[str, Any]]:
    wanted = str(account_id)
    for account in accounts(book):
        if str(account.get("id")) == wanted:
            return account
    return None


# ---------------------------------------------------------------------------
# Path resolution and formatting
# ---------------------------------------------------------------------------


def get_path(payload: Any, path: str) -> tuple[bool, Any]:
    """Resolve ``accounts[0].masked_number``-style paths against a dict.

    Returns ``(found, value)``. A leading ``book.`` is stripped so claim source
    references can read naturally.
    """

    if not path:
        return False, None
    text = str(path).strip()
    if text.startswith("book."):
        text = text[len("book."):]
    if text in {"book", ""}:
        return True, payload
    tokens: list[Any] = []
    for part in re.split(r"\.|(?=\[)|(?<=\])", text):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"\[(\d+)\]", part)
        if match:
            tokens.append(int(match.group(1)))
        else:
            tokens.append(part)
    current = payload
    for token in tokens:
        if isinstance(token, int):
            if not isinstance(current, (list, tuple)) or token >= len(current):
                return False, None
            current = current[token]
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            return False, None
    return True, current


def money(value: Any) -> str:
    """Format a number as ``USD 42,300,000``; empty string when not numeric."""

    number = to_number(value)
    if number is None:
        return ""
    if float(number).is_integer():
        return f"USD {int(number):,}"
    return f"USD {number:,.2f}"


def to_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.\-]", "", value.replace("(", "-").replace(")", ""))
        if cleaned in {"", "-", ".", "-."}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def parse_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    text = re.sub(r"(?i)(as of|on|by)\s+", "", text).strip().rstrip(".")
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def word_count(text: Any) -> int:
    if not isinstance(text, str):
        return 0
    return len(text.split())


def slugify(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return text or "unknown"


def doc_id(document: dict[str, Any]) -> str:
    for key in ("id", "document_id", "doc_id", "key"):
        value = document.get(key)
        if value not in (None, ""):
            return str(value)
    filename = document.get("filename") or document.get("file") or "document"
    return Path(str(filename)).stem


def document_text(document: dict[str, Any]) -> str:
    """The statement text, accepting the key spellings the seed data may use."""

    for key in (
        "text",
        "body_text",
        "content",
        "body",
        "raw_text",
        "statement_text",
        "extracted_text",
        "lines",
    ):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = document_text(value)
            if nested.strip():
                return nested
        if isinstance(value, list):
            parts = [str(item) for item in value if item is not None]
            if parts:
                return "\n".join(parts)
    return ""


def iter_numbers(text: str) -> list[str]:
    """Numeric tokens used by the guardrail and the draft claim audit.

    Matches amounts, percentages, counts, and dates. Single digits are included so
    a stray "3 accounts" cannot slip through uncited. Standalone years and policy
    references are included too; the draft generator cites them.
    """

    if not isinstance(text, str):
        return []
    return re.findall(r"\d[\d,]*(?:\.\d+)?%?", text)


def unique(items: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
