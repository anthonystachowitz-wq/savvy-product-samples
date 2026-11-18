"""Transparent hours-returned model.

Every number is either a seeded assumption or simple arithmetic shown as a string a
reader can check. There is no measurement claim here. The demo labels these as modeled
assumptions on the Value screen and in the report.

Defaults (from the build spec):

* baseline specialist time: 34 hours per transition
* assisted time: 16 hours per transition
* transitions per year: 40
* target days from signature to a funded account: 45

``nigo_caught`` is real for the seeded book: it counts the open or monitoring high and
medium NIGO risks that :func:`pipeline.plan.build_plan` raises. It is not a claim about
production performance.
"""

from __future__ import annotations

from typing import Any, Optional

from . import load_book
from .plan import build_plan

DEFAULTS = {
    "baseline_hours": 34.0,
    "assisted_hours": 16.0,
    "annual_transitions": 40,
    "days_to_funded_target": 45,
}

ASSUMPTION_ALIASES = {
    "baseline_hours_per_transition": "baseline_hours",
    "assisted_hours_per_transition": "assisted_hours",
    "transitions_per_year": "annual_transitions",
    "annual_transition_count": "annual_transitions",
    "target_days_to_funded": "days_to_funded_target",
    "days_to_funded": "days_to_funded_target",
}


def value_model(
    book: Optional[dict[str, Any]] = None,
    assumptions: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return the hours model, its assumptions, and the formulas behind each number."""

    if book is None:
        book = load_book()
    merged = _merge_assumptions(assumptions or {})
    baseline = merged["baseline_hours"]
    assisted = merged["assisted_hours"]
    transitions = merged["annual_transitions"]
    target_days = merged["days_to_funded_target"]

    if baseline <= 0:
        raise ValueError("baseline_hours must be greater than zero")
    if assisted < 0:
        raise ValueError("assisted_hours cannot be negative")
    if assisted > baseline:
        raise ValueError("assisted_hours cannot exceed baseline_hours")
    if transitions < 0:
        raise ValueError("annual_transitions cannot be negative")
    if target_days <= 0:
        raise ValueError("days_to_funded_target must be greater than zero")

    hours_returned = baseline - assisted
    annual_hours_returned = hours_returned * transitions
    nigo_caught = _count_nigo(book)

    formulas = [
        f"baseline_hours = {_fmt(baseline)} hours per transition (seeded assumption)",
        f"assisted_hours = {_fmt(assisted)} hours per transition (seeded assumption)",
        (
            "hours_returned = baseline_hours - assisted_hours = "
            f"{_fmt(baseline)} - {_fmt(assisted)} = {_fmt(hours_returned)}"
        ),
        (
            "annual_hours_returned = annual_transitions x hours_returned = "
            f"{_fmt(transitions)} x {_fmt(hours_returned)} = {_fmt(annual_hours_returned)}"
        ),
        (
            "annual_transitions = "
            f"{_fmt(transitions)} transitions per year (seeded assumption)"
        ),
        (
            "days_to_funded_target = "
            f"{_fmt(target_days)} days from signature (seeded target, not a measurement)"
        ),
        (
            "nigo_caught = open or monitoring high and medium NIGO risks raised by "
            f"build_plan(book) = {nigo_caught}"
        ),
    ]

    return {
        "baseline_hours": _number(baseline),
        "assisted_hours": _number(assisted),
        "hours_returned": _number(hours_returned),
        "annual_transitions": _number(transitions),
        "annual_hours_returned": _number(annual_hours_returned),
        "days_to_funded_target": _number(target_days),
        "nigo_caught": nigo_caught,
        "formulas": formulas,
        "assumptions": {
            "baseline_hours": _number(baseline),
            "assisted_hours": _number(assisted),
            "annual_transitions": _number(transitions),
            "days_to_funded_target": _number(target_days),
            "nigo_caught": nigo_caught,
            "provenance": (
                "Seeded demo assumptions. Hours are specialist estimates for one "
                "transition, not timings measured in a production workflow."
            ),
        },
    }


def _merge_assumptions(overrides: dict[str, Any]) -> dict[str, float]:
    merged = dict(DEFAULTS)
    for key, value in overrides.items():
        name = ASSUMPTION_ALIASES.get(key, key)
        if name not in DEFAULTS:
            continue  # unknown assumptions are ignored rather than guessed at
        try:
            merged[name] = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Assumption {key!r} must be numeric") from exc
    return merged


def _count_nigo(book: dict[str, Any]) -> int:
    if not book:
        return 0
    plan = build_plan(book)
    count = 0
    for risk in plan.get("risks", []):
        if risk.get("status") in {"open", "monitor"} and risk.get("severity") in {"high", "medium"}:
            count += 1
    return count


def _fmt(value: Any) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _number(value: Any) -> float | int:
    number = float(value)
    return int(number) if number.is_integer() else round(number, 2)
