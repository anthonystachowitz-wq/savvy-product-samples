#!/usr/bin/env python3
"""Warm the deterministic cache the demo reads.

Writes ``data/cached/<key>.json`` for every document and for the three draft kinds.
Each file carries ``key``, ``generated_at``, ``mode``, and ``text``. The mode is
the provenance the demo displays:

* deterministic extractions: "deterministic extractor"
* layouts and drafts built by these rules: "deterministic template"
* live model extraction: "live model, recorded" - only after a real Bedrock call

Drafts are always assembled from seeded facts by this script; passing ``--mode live``
affects document extraction only. That keeps the claims table and the guardrails tied
to facts a reader can check.

Usage:
    python3 scripts/warm_cache.py
    python3 scripts/warm_cache.py --mode live
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.llm import (  # noqa: E402
    MODE_DETERMINISTIC_EXTRACT,
    MODE_DETERMINISTIC_TEMPLATE,
    MODE_RECORDED_MODEL,
    BedrockProvider,
    ConfigurationError,
    write_cached,
)
from app.pipeline import (  # noqa: E402
    accounts,
    data_dir,
    doc_id,
    document_text,
    households,
    load_book,
    load_documents,
)
from app.pipeline.draft import draft  # noqa: E402
from app.pipeline.extract import extract_document  # noqa: E402

SYSTEM_PROMPT = (
    "You extract fields from synthetic financial statement text for a product demo. "
    "Return only a JSON object with two arrays: fields (name, value, confidence, "
    "source_span) and flags (kind, detail, severity). Do not add commentary."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Warm data/cached for the demo.")
    parser.add_argument(
        "--mode",
        choices=("deterministic", "live"),
        default="deterministic",
        help="Extraction mode. Deterministic is the default and needs no AWS access.",
    )
    parser.add_argument("--data-dir", default=None, help="Defaults to <repo>/data.")
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Defaults to <data-dir>/cached.",
    )
    args = parser.parse_args(argv)

    base = Path(args.data_dir).expanduser().resolve() if args.data_dir else data_dir()
    cache_dir = (
        Path(args.cache_dir).expanduser().resolve() if args.cache_dir else base / "cached"
    )

    book = load_book(base / "book.json")
    documents = load_documents(base / "documents")
    if not documents:
        print(f"No documents found under {base / 'documents'}.", file=sys.stderr)
        return 1

    provider = None
    if args.mode == "live":
        try:
            provider = BedrockProvider()
        except ConfigurationError as exc:
            print(f"Live mode unavailable: {exc}", file=sys.stderr)
            return 2

    written: list[dict[str, str]] = []
    seen: set[str] = set()
    for document in documents:
        identifier = doc_id(document)
        if identifier in seen:
            continue
        seen.add(identifier)
        if provider is not None:
            result = _live_extraction(provider, document)
            mode = MODE_RECORDED_MODEL
        else:
            result = extract_document(document)
            mode = MODE_DETERMINISTIC_EXTRACT
        entry = write_cached(
            f"extract-{identifier}",
            json.dumps(result, indent=2, ensure_ascii=False),
            mode,
            directory=cache_dir,
            extra={"document_id": identifier},
        )
        written.append({"key": entry["key"], "mode": entry["mode"]})

    household_id = _pick_household(book)
    if household_id:
        for kind in ("client_update", "custodian_cover"):
            payload = draft(kind, household_id, book)
            entry = write_cached(
                f"draft-{kind}-{household_id}",
                json.dumps(payload, indent=2, ensure_ascii=False),
                MODE_DETERMINISTIC_TEMPLATE,
                directory=cache_dir,
                extra={"word_count": payload["word_count"]},
            )
            written.append({"key": entry["key"], "mode": entry["mode"]})
    else:
        print("No household found; skipped client drafts.", file=sys.stderr)

    digest = draft("internal_digest", "", book)
    entry = write_cached(
        "draft-internal_digest-book",
        json.dumps(digest, indent=2, ensure_ascii=False),
        MODE_DETERMINISTIC_TEMPLATE,
        directory=cache_dir,
        extra={"word_count": digest["word_count"]},
    )
    written.append({"key": entry["key"], "mode": entry["mode"]})

    print(f"Warmed {len(written)} cache files under {cache_dir}")
    for item in written:
        print(f"  {item['key']}: {item['mode']}")
    if args.mode == "live":
        print("Drafts stayed on the deterministic template path by design.")
    return 0


def _pick_household(book: dict) -> str:
    """Household with the most accounts; ties keep book order."""

    candidates = households(book)
    best_id = ""
    best_count = -1
    for household in candidates:
        identifier = str(household.get("id") or "")
        count = len(accounts(book, identifier))
        if count > best_count:
            best_id, best_count = identifier, count
    return best_id


def _live_extraction(provider: BedrockProvider, document: dict) -> dict:
    identifier = doc_id(document)
    text = document_text(document)
    raw = provider.complete(
        SYSTEM_PROMPT,
        f"Document id: {identifier}\n"
        "Extract registration, account_number, statement_period, address, beneficiary, "
        "and one set of fields per position.\n\n"
        f"--- document text ---\n{text}\n--- end ---",
    )
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        payload = json.loads(cleaned)
    except ValueError as exc:
        raise RuntimeError(f"Model returned non-JSON for {identifier}: {raw[:200]}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), list):
        raise RuntimeError(f"Model returned an unexpected shape for {identifier}")
    return {
        "fields": payload["fields"],
        "flags": payload.get("flags", []),
        "document_id": identifier,
        "method": MODE_RECORDED_MODEL,
    }


if __name__ == "__main__":
    raise SystemExit(main())
