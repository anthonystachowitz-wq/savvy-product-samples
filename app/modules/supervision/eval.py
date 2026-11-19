"""Eval runner for the supervision claim classifier.

Twelve hand-labelled claims are classified by the recorded model call with key
"eval-claims", then passed through the same deterministic source check the page
uses. The score is the classifier the reviewer actually sees.
"""
from __future__ import annotations

from typing import Any

from app.platform import llm

from . import data, logic

MODULE = "supervision"


def _rules_type(text: str) -> str:
    lower = (text or "").lower()
    if any(token in lower for token in ("will earn", "guarantee", "guaranteed", "promise")):
        return "promised_return"
    if "client" in lower and any(token in lower for token in ("told", "say", "says", "report")):
        return "testimonial"
    if logic.numeric_tokens(text) and any(
        token in lower for token in ("return", "returned", "earned", "index", "performance", "s&p")
    ):
        return "performance"
    if any(token in lower for token in ("returned", "total return", "annual return")):
        return "performance"
    return "factual"


def _rules_verdict(claim: str, fact_sheet: dict[str, Any]) -> dict[str, str]:
    facts = logic.fact_index(fact_sheet)
    tokens = logic.numeric_tokens(claim)
    source = "none"
    for fact_id, fact in facts.items():
        if tokens and logic.fact_supports_numbers(fact, tokens):
            source = fact_id
            break
    if not tokens:
        source = "none"
    return {
        "type": _rules_type(claim),
        "support": "supported" if source != "none" else "unsupported",
        "source": source,
    }


def _model_items(result_text: str, cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    parsed, error = logic.parse_json(result_text)
    if isinstance(parsed, dict) and isinstance(parsed.get("claims"), list):
        items = [item for item in parsed["claims"] if isinstance(item, dict)]
    elif isinstance(parsed, list):
        items = [item for item in parsed if isinstance(item, dict)]
    else:
        return [], error or "The recording has no claims array."
    valid = {case["id"] for case in cases}
    by_id = {
        str(item.get("id") or "").strip().upper(): item
        for item in items
        if str(item.get("id") or "").strip().upper() in valid
    }
    if not by_id:
        by_id = {
            case["id"]: item
            for case, item in zip(cases, items)
        }
    ordered = [by_id[case["id"]] for case in cases if case["id"] in by_id]
    if not ordered:
        return [], "No claim ids matched the eval cases."
    return ordered, None


def _label_text(case: dict[str, Any]) -> str:
    return "{type}, {support}".format(
        type=case["expected_type"].replace("_", " "),
        support=case["expected_support"],
    )


def run_eval() -> dict[str, Any]:
    payload = data.eval_cases()
    cases = payload["cases"]
    fact_sheet = data.fact_sheet()
    key = payload.get("key", "eval-claims")
    result = llm.recorded(MODULE, key)

    source = "recorded model classification"
    model = ""
    recorded_at = ""
    latency_ms = 0
    prompt_tokens = 0
    completion_tokens = 0
    parse_error: str | None = None
    items: list[dict[str, Any]] = []

    if result is not None:
        model = result.model
        recorded_at = result.created_at
        latency_ms = int(result.latency_ms or 0)
        usage = result.usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        items, parse_error = _model_items(result.text, cases)

    if not items:
        source = "rules fallback, recording missing or unreadable"
        items = [
            {
                "id": case["id"],
                "text": case["claim"],
                **_rules_verdict(case["claim"], fact_sheet),
            }
            for case in cases
        ]

    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = str(item.get("id") or "").strip().upper()
        if item_id:
            by_id[item_id] = item
    positional = iter(items)

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    notes: list[str] = []
    true_positive = false_positive = false_negative = 0
    exact_count = 0
    expected_flags = 0
    actual_flags = 0
    by_label: dict[str, dict[str, int]] = {}

    for case in cases:
        item = by_id.get(case["id"])
        if item is None:
            item = next(positional, None)
        if not isinstance(item, dict):
            actual_type = "missing"
            actual_support = "unsupported"
            note = ""
            model_type = ""
            model_support = ""
        else:
            validated = logic.validate_claims([item], fact_sheet)
            if validated:
                claim = validated[0]
                actual_type = claim["type"]
                actual_support = claim["support"]
                note = claim["note"]
                model_type = claim["model_type"]
                model_support = claim["model_support"]
            else:
                actual_type = "missing"
                actual_support = "unsupported"
                note = ""
                model_type = ""
                model_support = ""

        expected_type = case["expected_type"]
        expected_support = case["expected_support"]
        expected_flag = case["label"] != "supported"
        actual_flag = actual_type in logic.FLAG_TYPES or actual_support == "unsupported"
        exact = (
            actual_type == expected_type
            and actual_support == expected_support
            and actual_flag == expected_flag
        )
        if exact:
            exact_count += 1
        expected_flags += 1 if expected_flag else 0
        actual_flags += 1 if actual_flag else 0
        if expected_flag and actual_flag:
            true_positive += 1
        elif actual_flag and not expected_flag:
            false_positive += 1
        elif expected_flag and not actual_flag:
            false_negative += 1

        bucket = by_label.setdefault(case["label"], {"cases": 0, "exact": 0})
        bucket["cases"] += 1
        if exact:
            bucket["exact"] += 1

        row = {
            "id": case["id"],
            "claim": case["claim"],
            "label": case["label"],
            "expected_type": expected_type,
            "expected_support": expected_support,
            "actual_type": actual_type,
            "actual_support": actual_support,
            "model_type": model_type,
            "model_support": model_support,
            "source": item.get("source", "none") if isinstance(item, dict) else "none",
            "exact": exact,
            "note": note,
        }
        rows.append(row)

        if not exact:
            failures.append(
                {
                    "case": "{id}: {claim}".format(id=case["id"], claim=case["claim"]),
                    "expected": _label_text(case),
                    "actual": "{type}, {support}".format(
                        type=actual_type.replace("_", " "), support=actual_support
                    ),
                    "note": _failure_note(case, actual_type, actual_support, model_type, model_support, note),
                }
            )
            notes.append(_failure_note(case, actual_type, actual_support, model_type, model_support, note))

    total = len(cases)
    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    if parse_error:
        notes.append("The eval recording could not be parsed: " + parse_error)
    if source.startswith("recorded"):
        notes.append(
            "The eval is in sample: the same model, the same fact sheet, and twelve cases chosen by hand."
        )

    return {
        "name": payload.get("name", "Claim flag accuracy"),
        "cases": total,
        "accuracy": round(exact_count / total, 4) if total else 0.0,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "exact": exact_count,
        "flags": {
            "expected": expected_flags,
            "actual": actual_flags,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
        },
        "by_label": by_label,
        "source": source,
        "model": model,
        "recorded_at": recorded_at,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cases_detail": rows,
        "failures": failures,
        "failure_notes": notes,
    }


def _failure_note(
    case: dict[str, Any],
    actual_type: str,
    actual_support: str,
    model_type: str,
    model_support: str,
    adjusted: str,
) -> str:
    case_id = case["id"]
    label = case["label"].replace("_", " ")
    if actual_type == "missing":
        return "{id} got no verdict. The classifier returned nothing for this claim.".format(id=case_id)
    expected_flag = case["label"] != "supported"
    actual_flag = actual_type in logic.FLAG_TYPES or actual_support == "unsupported"
    if expected_flag and not actual_flag:
        return "{id} was read as a supported factual claim, so the {label} flag never fired.".format(
            id=case_id, label=label
        )
    if actual_flag and not expected_flag:
        return "{id} was flagged as {type}, but the claim is supported and factual.".format(
            id=case_id, type=actual_type.replace("_", " ")
        )
    detail = ""
    if adjusted:
        detail = " The source check downgraded the model's label: " + adjusted
    if model_type and logic.normalise_type(model_type) == actual_type:
        return "{id} carried the right flag but the wrong type ({type}).{detail}".format(
            id=case_id, type=actual_type.replace("_", " "), detail=detail
        )
    return "{id} was typed {type} instead of {expected}.{detail}".format(
        id=case_id,
        type=actual_type.replace("_", " "),
        expected=case["expected_type"].replace("_", " "),
        detail=detail,
    )
