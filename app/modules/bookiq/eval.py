"""Book IQ evaluation: did the ranking choose the labelled play and signal?

The run scores the recorded ranking, so the number is a stable property of the
recording, not of the request. Accuracy is the share of the 12 labelled cases
where the top play matches (or matches an accepted alternative) and the cited
signal matches that play.

    python3 scripts/eval_bookiq.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.platform import llm

from . import data, logic

NAME = "Play and signal accuracy"


def _status_for_case(
    case: dict[str, Any],
    row: dict[str, Any] | None,
) -> tuple[bool, str, str]:
    expected = str(case.get("expected_play") or "")
    required = str(case.get("required_signal") or "")
    alternatives = case.get("alternatives") or []
    if row is None:
        return False, "no row", ""
    actual_play = str(row.get("play") or "unknown")
    actual_signal = str(row.get("signal") or "")
    if actual_play == expected and actual_signal == required:
        return True, actual_play, actual_signal
    for alternative in alternatives:
        if actual_play == alternative.get("play") and actual_signal in (alternative.get("signals") or []):
            return True, actual_play, actual_signal
    return False, actual_play, actual_signal


def _note_for_case(
    case: dict[str, Any],
    row: dict[str, Any] | None,
) -> str:
    household_id = case.get("household_id")
    household = case.get("household") or household_id
    expected = case.get("expected_play")
    required = case.get("required_signal")
    if row is None:
        return (
            f"{household_id} {household}: the ranking returned no row. "
            f"Expected {logic.plays_label(expected)} with the {required} signal."
        )
    actual_play = row.get("play") or "unknown"
    actual_signal = row.get("signal") or "no signal"
    if actual_play == expected:
        return (
            f"{household_id} {household}: the play was right ({expected}) but the model cited "
            f"{actual_signal} instead of the required {required}."
        )
    return (
        f"{household_id} {household}: the model chose {actual_play} and cited {actual_signal}. "
        f"Expected {expected} with {required}. {case.get('rationale') or ''}"
    ).strip()


def run() -> dict[str, Any]:
    cases_payload = data.eval_cases()
    cases = cases_payload.get("items") or []
    households = {household["id"]: household for household in data.households()}
    recorded = llm.recorded(logic.MODULE, logic.RANK_KEY)
    if recorded is None:
        return {
            "name": NAME,
            "module": logic.MODULE,
            "cases": len(cases) or 12,
            "accuracy": 0.0,
            "failures": [
                {
                    "case": "ranking recording",
                    "expected": "a recorded ranking",
                    "actual": "missing",
                }
            ],
            "failure_notes": [
                "The ranking recording is missing. Run SAVVY_LLM_MODE=live python3 scripts/record_bookiq.py."
            ],
            "results": [],
            "coverage": 0,
            "rule_agreement": 0,
            "status": "recording_missing",
        }

    rows, meta = logic.parse_ranking(recorded.text)
    by_id = {row["household_id"]: row for row in rows if row.get("play")}
    passed = 0
    failures: list[dict[str, str]] = []
    failure_notes: list[str] = []
    results: list[dict[str, Any]] = []
    for case in cases:
        household_id = str(case.get("household_id") or "")
        row = by_id.get(household_id)
        ok, actual_play, actual_signal = _status_for_case(case, row)
        status = "pass" if ok else "fail"
        if ok:
            passed += 1
        else:
            failures.append(
                {
                    "case": f"{household_id} {case.get('household') or ''}".strip(),
                    "expected": f"{case.get('expected_play')} + {case.get('required_signal')}",
                    "actual": f"{actual_play} + {actual_signal or 'no signal'}",
                }
            )
            failure_notes.append(_note_for_case(case, row))
        results.append(
            {
                "household_id": household_id,
                "household": case.get("household") or "",
                "expected": f"{case.get('expected_play')} + {case.get('required_signal')}",
                "actual": f"{actual_play} + {actual_signal or 'no signal'}"
                if row is not None
                else "no row",
                "status": status,
                "alternatives": case.get("alternatives") or [],
            }
        )

    rule_matches = 0
    rule_total = 0
    for household in households.values():
        row = by_id.get(household["id"])
        if row is None:
            continue
        rule_total += 1
        if row.get("play") == logic.rule_play(household)["play"]:
            rule_matches += 1

    accuracy = round(passed / len(cases), 4) if cases else 0.0
    return {
        "name": NAME,
        "module": logic.MODULE,
        "cases": len(cases),
        "accuracy": accuracy,
        "failures": failures,
        "failure_notes": failure_notes,
        "results": results,
        "coverage": len(by_id),
        "rule_agreement": f"{rule_matches} of {rule_total}" if rule_total else "n/a",
        "model": recorded.model,
        "mode": recorded.mode,
        "recorded_at": recorded.created_at,
        "recording_key": logic.RANK_KEY,
        "parse_error": meta.get("error") or "",
        "scoring": cases_payload.get("scoring") or "",
        "notes": [
            "Scored against the recorded ranking, so the number is stable between page views.",
            "The set is 12 households from one synthetic book. A perfect score describes this book, not the next one.",
            "The rule check uses the same opportunity arithmetic that the page shows. Agreement with the rule is not the same as agreement with a human adviser.",
        ],
    }


def write(report: dict[str, Any] | None = None) -> tuple[Path, dict[str, Any]]:
    payload = report or run()
    path = data.DATA_DIR / "eval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path, payload
