"""Run the Concierge evaluation against the recorded answers.

Ten labelled cases. A case passes when the recorded answer's intent matches the
label, every required field is cited, every citation resolves to the record, the
number check passes, and the route flag matches the intent.

    python3 scripts/eval_concierge.py
"""

from __future__ import annotations

import json
from typing import Any

from app.platform import config
from app.platform.llm import LLMUnavailable
from app.modules.concierge import data, logic

MODULE = "concierge"
OUT_PATH = config.DATA_DIR / "modules" / MODULE / "eval.json"


def _expected_route(intent: str) -> bool:
    return intent in logic.ROUTE_INTENTS


def forbidden_hits(answer: str, phrases: list[str]) -> list[str]:
    """Return the phrases from the case label that appear in the answer."""

    lowered = str(answer or "").lower()
    return [item for item in phrases if item and item.lower() in lowered]


def evaluate(write: bool = True) -> dict[str, Any]:
    cases_payload = data.eval_cases()
    cases = cases_payload.get("cases", [])
    records = {item["id"]: item for item in data.records().get("households", [])}
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    notes: list[str] = []
    by_intent: dict[str, dict[str, int]] = {}

    for case in cases:
        household = records.get(case["household_id"])
        expected_intent = str(case["expected_intent"])
        bucket = by_intent.setdefault(expected_intent, {"cases": 0, "passed": 0})
        bucket["cases"] += 1
        reasons: list[str] = []
        actual = "no answer"

        if household is None:
            reasons.append("The household is not in records.json.")
        else:
            bundle = None
            try:
                bundle = logic.ask(household, case["question"], key=case["key"], audit_live=False)
            except LLMUnavailable:
                reasons.append("No recording at data/cached/llm/concierge/" + str(case["key"]) + ".json.")
            if bundle is not None:
                cited = [item["field"] for item in bundle["citations"] if item["status"] == "match"]
                missing = [field for field in case["required_fields"] if field not in cited]
                if bundle["parse_error"]:
                    reasons.append(bundle["parse_error"])
                if bundle["intent"] != expected_intent:
                    reasons.append("intent was " + bundle["intent"] + ", expected " + expected_intent)
                if missing:
                    reasons.append("missing citation fields: " + ", ".join(missing))
                found = forbidden_hits(bundle["answer"], case.get("forbidden", []))
                if found:
                    reasons.append("the answer names a holding the record does not contain: " + ", ".join(found))
                if not bundle["guardrail"]["ok"]:
                    detail = []
                    if bundle["guardrail"]["uncovered"]:
                        detail.append("numbers with no cited field: " + ", ".join(bundle["guardrail"]["uncovered"]))
                    if bundle["guardrail"]["unresolved"]:
                        detail.append("citations that do not resolve: " + ", ".join(bundle["guardrail"]["unresolved"]))
                    reasons.append("number check failed, " + "; ".join(detail))
                expected_route = _expected_route(expected_intent)
                if bool(bundle["route_to_advisor"]) != expected_route:
                    reasons.append(
                        "route flag was " + str(bundle["route_to_advisor"]) + ", expected " + str(expected_route)
                    )
                actual = (
                    "intent="
                    + bundle["intent"]
                    + ", route="
                    + str(bundle["route_to_advisor"]).lower()
                    + ", citations="
                    + str(len(cited))
                    + ", numbers checked="
                    + str(bundle["guardrail"]["checked"])
                )

        passed = not reasons
        if passed:
            bucket["passed"] += 1
        else:
            failures.append(
                {
                    "case": case["id"],
                    "label": case["label"],
                    "household": case["household_name"],
                    "question": case["question"],
                    "expected": (
                        "intent="
                        + expected_intent
                        + ", cites "
                        + (", ".join(case["required_fields"]) or "none")
                        + (", avoids " + ", ".join(case["forbidden"]) if case.get("forbidden") else "")
                    ),
                    "actual": actual,
                    "reasons": reasons,
                }
            )
            notes.append(case["id"] + " " + case["household_name"] + ": " + reasons[0])
        results.append(
            {
                "case": case["id"],
                "label": case["label"],
                "household": case["household_name"],
                "expected_intent": expected_intent,
                "passed": passed,
                "reasons": reasons,
                "actual": actual,
            }
        )

    total = len(cases)
    passed_count = sum(1 for item in results if item["passed"])
    accuracy = round(passed_count / total, 4) if total else 0.0
    payload = {
        "name": cases_payload.get("name") or "Advice-boundary and citation accuracy",
        "module": MODULE,
        "cases": total,
        "accuracy": accuracy,
        "failures": failures,
        "failure_notes": notes,
        "rubric": cases_payload.get("rubric", ""),
        "by_intent": by_intent,
        "results": results,
        "recordings": len(data.recordings()),
        "stored_answers": "data/cached/llm/concierge",
    }

    if write:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload
