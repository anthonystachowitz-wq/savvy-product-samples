"""Field-level evaluation for LP Ops.

The eval reads the recorded extractions, never makes a live call. Accuracy counts
60 field cells plus 5 exception checks: 65 checks. An exception check passes only
when the kind set and every severity match the administrator issue log. Severity
is part of the extraction schema, so it is part of the check.
"""
from __future__ import annotations

import json
from typing import Any

from ...platform import llm
from . import data, logic


def run() -> dict[str, Any]:
    case_payload = data.load_eval_cases()
    cases = case_payload.get("cases") if isinstance(case_payload.get("cases"), list) else []
    field_keys = [key for key in (case_payload.get("field_keys") or logic.FIELD_KEYS) if key]
    if not cases:
        return {
            "name": "Subscription packet extraction",
            "cases": 0,
            "checks": 0,
            "accuracy": 0.0,
            "failures": [],
            "failure_notes": ["No eval cases were found. Run scripts/generate_lpo.py first."],
        }

    per_field = {
        key: {"field": key, "label": logic.FIELD_LABELS.get(key, key), "correct": 0, "total": 0}
        for key in field_keys
    }
    failures: list[dict[str, Any]] = []
    failure_notes: list[str] = []
    models: set[str] = set()
    missing_recordings: list[str] = []
    field_correct = 0
    field_checks = 0
    exception_correct = 0
    exception_checks = 0
    kind_correct = 0
    severity_checks = 0
    severity_correct = 0

    for case in cases:
        case_id = str(case.get("case") or case.get("packet_id"))
        packet_id = str(case.get("packet_id") or case_id)
        expected = case.get("expected") or {}
        expected_exceptions = sorted(str(item) for item in (case.get("expected_exceptions") or []))
        expected_details = case.get("expected_exception_details")
        if not isinstance(expected_details, list):
            expected_details = [{"kind": kind, "severity": ""} for kind in expected_exceptions]
        expected_severity = {
            str(item.get("kind")): str(item.get("severity") or "")
            for item in expected_details
            if isinstance(item, dict) and item.get("kind")
        }

        result = llm.recorded("lpo", logic.extraction_key(packet_id))
        if result is None:
            missing_recordings.append(packet_id)
            extraction = None
            actual_fields = {key: logic.MISSING for key in field_keys}
            actual_by_kind: dict[str, str] = {}
        else:
            if result.model:
                models.add(result.model)
            extraction = logic.parse_extraction(result.text)
            actual_fields = extraction.get("fields") or {}
            actual_by_kind = {
                str(item.get("kind")): str(item.get("severity") or "")
                for item in extraction.get("exceptions", [])
                if item.get("kind")
            }
        actual_exceptions = sorted(actual_by_kind)

        for key in field_keys:
            field_checks += 1
            per_field[key]["total"] += 1
            expected_value = logic.normalize_field(key, expected.get(key, logic.MISSING))
            actual_value = actual_fields.get(key, logic.MISSING)
            passed = bool(extraction and extraction.get("ok")) and logic.field_equal(key, expected_value, actual_value)
            if passed:
                field_correct += 1
                per_field[key]["correct"] += 1
            else:
                failures.append(
                    {
                        "case": case_id,
                        "packet_id": packet_id,
                        "field": key,
                        "field_label": logic.FIELD_LABELS.get(key, key),
                        "expected": logic.display_value(key, expected_value),
                        "actual": logic.display_value(key, actual_value),
                    }
                )

        exception_checks += 1
        kind_match = actual_exceptions == expected_exceptions
        if kind_match:
            kind_correct += 1
        severity_mismatches: list[tuple[str, str, str]] = []
        for kind, expected_value in expected_severity.items():
            severity_checks += 1
            actual_value = actual_by_kind.get(kind, "")
            if expected_value and actual_value == expected_value:
                severity_correct += 1
            elif expected_value and actual_value:
                severity_mismatches.append((kind, expected_value, actual_value))
        exception_passed = bool(extraction and extraction.get("ok")) and kind_match and not severity_mismatches
        if exception_passed:
            exception_correct += 1
        else:
            if not kind_match:
                failures.append(
                    {
                        "case": case_id,
                        "packet_id": packet_id,
                        "field": "exceptions.kind",
                        "field_label": "Exception kinds",
                        "expected": ", ".join(expected_exceptions) if expected_exceptions else "none",
                        "actual": ", ".join(actual_exceptions) if actual_exceptions else "none",
                    }
                )
            for kind, expected_value, actual_value in severity_mismatches:
                failures.append(
                    {
                        "case": case_id,
                        "packet_id": packet_id,
                        "field": "exceptions.severity." + kind,
                        "field_label": "Exception severity: " + logic.kind_label(kind),
                        "expected": logic.SEVERITY_LABELS.get(expected_value, expected_value),
                        "actual": logic.SEVERITY_LABELS.get(actual_value, actual_value),
                    }
                )

    checks = field_checks + exception_checks
    correct = field_correct + exception_correct
    accuracy = round(correct / checks, 4) if checks else 0.0

    for failure in failures:
        if len(failure_notes) >= 7:
            break
        if failure["field"].startswith("exceptions.severity."):
            failure_notes.append(
                f"{failure['case']} {failure['field_label']}: expected {failure['expected']}, "
                f"model returned {failure['actual']}."
            )
        elif failure["field"] == "exceptions.kind":
            failure_notes.append(
                f"{failure['case']} exception kinds: expected {failure['expected']}, "
                f"model returned {failure['actual']}."
            )
        else:
            failure_notes.append(
                f"{failure['case']} {failure['field_label']}: expected {failure['expected']}, "
                f"model returned {failure['actual']}."
            )

    if missing_recordings:
        failure_notes.insert(
            0,
            "Missing recordings for " + ", ".join(missing_recordings) + ". Run scripts/record_lpo.py in live mode.",
        )
    failure_notes.append(
        "In-sample eval: the five packets were written for this test, so the score describes these documents, not the next subscription packet."
    )
    if not failures:
        failure_notes.insert(0, "No field or exception mismatches in this run.")

    return {
        "name": "Subscription packet extraction",
        "version": 1,
        "module": "lpo",
        "cases": len(cases),
        "field_checks": field_checks,
        "exception_checks": exception_checks,
        "checks": checks,
        "correct": correct,
        "accuracy": accuracy,
        "field_accuracy": round(field_correct / field_checks, 4) if field_checks else 0.0,
        "exception_accuracy": round(exception_correct / exception_checks, 4) if exception_checks else 0.0,
        "exception_kind_accuracy": round(kind_correct / exception_checks, 4) if exception_checks else 0.0,
        "severity_checks": severity_checks,
        "severity_correct": severity_correct,
        "severity_accuracy": round(severity_correct / severity_checks, 4) if severity_checks else 0.0,
        "failures": failures,
        "failure_notes": failure_notes,
        "per_field": [per_field[key] for key in field_keys],
        "models": sorted(models),
        "recorded_packets": sorted({str(case.get("packet_id")) for case in cases if case.get("packet_id")}),
    }


def write_report(report: dict[str, Any]) -> str:
    data.EVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    data.EVAL_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return str(data.EVAL_PATH)
