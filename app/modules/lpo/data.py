"""Data access and state helpers for the LP Ops sample.

The module reads three generated files under data/modules/lpo and writes demo
decisions to the shared per-module state store. It never writes generated data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...platform import config, nav, state

MODULE = "lpo"
DATA_DIR = config.DATA_DIR / "modules" / MODULE
LP_RECORDS_PATH = DATA_DIR / "lp_records.json"
PACKETS_PATH = DATA_DIR / "packets.json"
EVAL_CASES_PATH = DATA_DIR / "eval_cases.json"
EVAL_PATH = DATA_DIR / "eval.json"
BOOK_PATH = config.DATA_DIR / "book.json"
CACHE_DIR = config.CACHE_DIR / MODULE

STAGE_ORDER = ["document_intake", "exception_review", "ready_to_fund", "funded"]
STAGE_LABELS = {
    "document_intake": "Document intake",
    "exception_review": "Exception review",
    "ready_to_fund": "Ready to fund",
    "funded": "Funded",
}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_records() -> dict[str, Any]:
    payload = _read_json(LP_RECORDS_PATH)
    return payload if isinstance(payload, dict) else {}


def load_packets() -> dict[str, Any]:
    payload = _read_json(PACKETS_PATH)
    return payload if isinstance(payload, dict) else {}


def packets() -> list[dict[str, Any]]:
    items = load_packets().get("packets")
    return items if isinstance(items, list) else []


def packet_by_id(packet_id: str) -> dict[str, Any] | None:
    for packet in packets():
        if packet.get("id") == packet_id:
            return packet
    return None


def load_eval_cases() -> dict[str, Any]:
    payload = _read_json(EVAL_CASES_PATH)
    return payload if isinstance(payload, dict) else {}


def load_eval() -> dict[str, Any] | None:
    payload = _read_json(EVAL_PATH)
    if not isinstance(payload, dict) or not payload:
        return None
    report = dict(payload)
    accuracy = report.get("accuracy")
    if isinstance(accuracy, (int, float)):
        report["accuracy_pct"] = round(float(accuracy) * 100, 1)
    report["failure_count"] = len(report.get("failures") or [])
    report["note_count"] = len(report.get("failure_notes") or [])
    return report


def module_meta() -> dict[str, Any]:
    try:
        return nav.module(MODULE)
    except KeyError:
        return {
            "slug": MODULE,
            "label": "LP Ops",
            "lever": "Scale",
            "url": "/lpo",
            "status": "in build",
        }


def team() -> list[dict[str, str]]:
    records = load_records()
    items = records.get("team")
    return items if isinstance(items, list) else []


def household_consent(household_id: str) -> str:
    book = _read_json(BOOK_PATH)
    for household in book.get("households", []) if isinstance(book, dict) else []:
        if household.get("id") == household_id:
            return str(household.get("consent_status") or "unknown")
    return "unknown"


def household_label(household_id: str) -> str:
    book = _read_json(BOOK_PATH)
    for household in book.get("households", []) if isinstance(book, dict) else []:
        if household.get("id") == household_id:
            return str(household.get("display_name") or household_id)
    return household_id


def default_state() -> dict[str, Any]:
    return {
        "seq": 0,
        "resolved": {},
        "stages": {},
        "approvals": [],
        "notes": {},
    }


def load_state() -> dict[str, Any]:
    raw = state.load(MODULE, {})
    payload = default_state()
    if isinstance(raw, dict):
        payload.update(raw)
    if not isinstance(payload.get("resolved"), dict):
        payload["resolved"] = {}
    if not isinstance(payload.get("stages"), dict):
        payload["stages"] = {}
    if not isinstance(payload.get("approvals"), list):
        payload["approvals"] = []
    if not isinstance(payload.get("notes"), dict):
        payload["notes"] = {}
    try:
        payload["seq"] = int(payload.get("seq") or 0)
    except (TypeError, ValueError):
        payload["seq"] = 0
    return payload


def save_state(payload: dict[str, Any]) -> None:
    state.save(MODULE, payload)


def resolved_for(state_payload: dict[str, Any], packet_id: str) -> set[str]:
    values = state_payload.get("resolved", {}).get(packet_id, [])
    if not isinstance(values, list):
        return set()
    return {str(item) for item in values}


def known_exceptions(packet: dict[str, Any]) -> list[dict[str, Any]]:
    items = packet.get("known_exceptions")
    return items if isinstance(items, list) else []


def open_known_exceptions(packet: dict[str, Any], state_payload: dict[str, Any]) -> list[dict[str, Any]]:
    resolved = resolved_for(state_payload, str(packet.get("id")))
    return [item for item in known_exceptions(packet) if item.get("kind") not in resolved]


def lps_with_state(records: dict[str, Any], packet_list: list[dict[str, Any]], state_payload: dict[str, Any]) -> list[dict[str, Any]]:
    by_lp: dict[str, list[dict[str, Any]]] = {}
    for packet in packet_list:
        by_lp.setdefault(str(packet.get("lp_id")), []).append(packet)

    result: list[dict[str, Any]] = []
    for raw in records.get("lps", []):
        item = dict(raw)
        lp_id = str(item.get("id"))
        item["stage"] = str(state_payload.get("stages", {}).get(lp_id) or item.get("stage") or "document_intake")
        item["stage_label"] = records.get("stage_labels", {}).get(item["stage"], STAGE_LABELS.get(item["stage"], item["stage"]))
        open_count = 0
        packet_ids: list[str] = []
        for packet in by_lp.get(lp_id, []):
            packet_ids.append(str(packet.get("id")))
            open_count += len(open_known_exceptions(packet, state_payload))
        item["open_exceptions"] = open_count
        item["exception_count"] = open_count
        item["seeded_exception_count"] = int(item.get("exception_count") or 0)
        item["packet_ids"] = packet_ids
        item["has_packet"] = bool(packet_ids)
        commitment = int(item.get("commitment") or 0)
        funded = int(item.get("funded") or 0)
        item["funded_pct"] = round(funded / commitment * 100, 1) if commitment else 0.0
        result.append(item)
    return result


def stage_counts(lps: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"all": len(lps)}
    for stage in STAGE_ORDER:
        counts[stage] = sum(1 for item in lps if item.get("stage") == stage)
    return counts


def summary_with_state(records: dict[str, Any], lps: list[dict[str, Any]], packet_list: list[dict[str, Any]], state_payload: dict[str, Any]) -> dict[str, Any]:
    summary = dict(records.get("summary") or {})
    summary["lps"] = len(lps)
    summary["packets"] = len(packet_list)
    summary["open_exceptions"] = sum(int(item.get("open_exceptions") or 0) for item in lps)
    summary["packets_with_exceptions"] = sum(
        1 for packet in packet_list if open_known_exceptions(packet, state_payload)
    )
    summary["stage_counts"] = stage_counts(lps)
    return summary


def calendar(records: dict[str, Any]) -> list[dict[str, Any]]:
    items = records.get("calendar")
    return items if isinstance(items, list) else []


def model_stats() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(CACHE_DIR.glob("*.json")):
        payload = _read_json(path)
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            entries.append(payload)
    models = sorted({str(item.get("model") or "") for item in entries if item.get("model")})
    latencies = [int(item.get("latency_ms") or 0) for item in entries if item.get("latency_ms")]
    prompt_tokens = 0
    completion_tokens = 0
    for item in entries:
        usage = item.get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
    latest = max(entries, key=lambda item: str(item.get("created_at") or ""), default={})
    return {
        "count": len(entries),
        "models": models,
        "model": models[0] if models else "",
        "mean_latency_ms": int(round(sum(latencies) / len(latencies))) if latencies else 0,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latest_created_at": str(latest.get("created_at") or ""),
        "latest_model": str(latest.get("model") or ""),
        "keys": sorted(str(item.get("key") or "") for item in entries),
    }


def latest_approval(state_payload: dict[str, Any], packet_id: str | None = None) -> dict[str, Any] | None:
    for item in state_payload.get("approvals", []):
        if not isinstance(item, dict):
            continue
        if packet_id is None or item.get("packet_id") == packet_id:
            return item
    return None


def recent_approvals(state_payload: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    items = [item for item in state_payload.get("approvals", []) if isinstance(item, dict)]
    return items[:limit]


def note_decision(state_payload: dict[str, Any], packet_id: str) -> dict[str, Any] | None:
    value = state_payload.get("notes", {}).get(packet_id)
    return value if isinstance(value, dict) else None
