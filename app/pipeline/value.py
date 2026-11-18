"""Account-based hours-returned model.

The model is account-based, per the build spec: about 45 minutes of manual operations
time per account, plus planning and reconciliation, and a 25 to 35 percent reduction
from the assisted workflow. The reference transition is 100 clients at 5 accounts each,
which is 500 accounts: 375 hours of account work plus 60 hours of planning and
reconciliation, so 435 hours per transition.

Every figure is computed from the assumptions in :data:`DEFAULTS` or from the seeded
book, and every formula is written out as a string a reader can check. All of it is a
modelled estimate. None of it is a timing measured in a production workflow, and the
Value screen says so next to the numbers.

``nigo_caught`` is the one counted figure: the open or monitoring high and medium NIGO
risks that :func:`pipeline.plan.build_plan` raises for the seeded book.
"""

from __future__ import annotations

from typing import Any, Optional

from . import accounts as book_accounts
from . import households, load_book
from .plan import build_plan

#: Seeded assumptions from the build spec. All are per transition.
DEFAULTS: dict[str, float] = {
    "clients": 100,  # clients in the reference transition
    "accounts_per_household": 5,  # accounts per client household
    "minutes_per_account": 45,  # manual operations minutes per account
    "planning_hours": 60,  # planning and reconciliation hours per transition
    "reduction_low": 0.25,  # low end of the assisted-work reduction
    "reduction_high": 0.35,  # high end of the assisted-work reduction
    "annual_transitions": 40,  # transitions per year
    "days_to_funded_target": 45,  # signature to funded account, target
}

ASSUMPTION_ALIASES = {
    "clients_per_transition": "clients",
    "accounts_per_client": "accounts_per_household",
    "planning_and_reconciliation_hours": "planning_hours",
    "reconciliation_hours": "planning_hours",
    "reduction_min": "reduction_low",
    "reduction_max": "reduction_high",
    "transitions_per_year": "annual_transitions",
    "annual_transition_count": "annual_transitions",
    "target_days_to_funded": "days_to_funded_target",
    "days_to_funded": "days_to_funded_target",
}

PROVENANCE = (
    "Seeded demo assumptions. Hours are modelled estimates for one transition at "
    "reference scale, not timings measured in a production workflow."
)


def value_model(
    book: Optional[dict[str, Any]] = None,
    assumptions: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return the account-based hours model, its assumptions, and the formulas.

    The return is a superset of the old flat model's keys: ``baseline_hours``,
    ``assisted_hours``, ``hours_returned``, ``annual_hours_returned``,
    ``annual_transitions``, ``days_to_funded_target``, ``nigo_caught``,
    ``formulas``, and ``assumptions`` are all still present. The single old
    ``assisted_hours`` figure is now the midpoint of the assisted range, and
    ``hours_baseline`` / ``hours_assisted`` alias the interface names in the
    build spec.
    """

    if book is None:
        book = load_book()
    merged = _merge_assumptions(assumptions or {})

    clients = merged["clients"]
    accounts_per_household = merged["accounts_per_household"]
    minutes = merged["minutes_per_account"]
    planning = merged["planning_hours"]
    reduction_low = merged["reduction_low"]
    reduction_high = merged["reduction_high"]
    transitions = merged["annual_transitions"]
    target_days = merged["days_to_funded_target"]
    _validate(merged)

    reference_accounts = clients * accounts_per_household
    reference_account_hours = _hours(reference_accounts * minutes / 60.0)
    baseline = _hours(reference_account_hours + planning)
    hours_returned_low = _whole_hours(baseline * reduction_low)
    hours_returned_high = _whole_hours(baseline * reduction_high)
    assisted_hours_low = _hours(baseline - hours_returned_high)
    assisted_hours_high = _hours(baseline - hours_returned_low)
    hours_returned = _hours((hours_returned_low + hours_returned_high) / 2)
    assisted_hours = _hours(baseline - hours_returned)
    annual_low = _hours(transitions * hours_returned_low)
    annual_high = _hours(transitions * hours_returned_high)
    annual_mid = _hours(transitions * hours_returned)

    seeded_accounts = len(book_accounts(book))
    seeded_account_hours = _hours(seeded_accounts * minutes / 60.0)
    seeded_planning = _hours(planning * seeded_accounts / reference_accounts)
    seeded_baseline = _hours(seeded_account_hours + seeded_planning)
    seeded_low = _whole_hours(seeded_baseline * reduction_low)
    seeded_high = _whole_hours(seeded_baseline * reduction_high)

    nigo_caught = _count_nigo(book)

    formulas = [
        "Modelled estimate: accounts = clients x accounts_per_household = "
        f"{_fmt(clients)} x {_fmt(accounts_per_household)} = {_fmt(reference_accounts)} accounts",
        "Modelled estimate: account_hours = accounts x minutes_per_account / 60 = "
        f"{_fmt(reference_accounts)} x {_fmt(minutes)} / 60 = {_fmt(reference_account_hours)} hours",
        "Modelled estimate: baseline_hours = account_hours + planning_hours = "
        f"{_fmt(reference_account_hours)} + {_fmt(planning)} = {_fmt(baseline)} hours per transition",
        "Modelled estimate: hours_returned_low = baseline_hours x reduction_low = "
        f"{_fmt(baseline)} x {_fmt(reduction_low)} = {_fmt(baseline * reduction_low)}, "
        f"rounded to {_fmt(hours_returned_low)} hours",
        "Modelled estimate: hours_returned_high = baseline_hours x reduction_high = "
        f"{_fmt(baseline)} x {_fmt(reduction_high)} = {_fmt(baseline * reduction_high)}, "
        f"rounded to {_fmt(hours_returned_high)} hours",
        "Modelled estimate: assisted_hours_low = baseline_hours - hours_returned_high = "
        f"{_fmt(baseline)} - {_fmt(hours_returned_high)} = {_fmt(assisted_hours_low)} hours",
        "Modelled estimate: assisted_hours_high = baseline_hours - hours_returned_low = "
        f"{_fmt(baseline)} - {_fmt(hours_returned_low)} = {_fmt(assisted_hours_high)} hours",
        "Modelled estimate: hours_returned = (hours_returned_low + hours_returned_high) / 2 = "
        f"({_fmt(hours_returned_low)} + {_fmt(hours_returned_high)}) / 2 = {_fmt(hours_returned)} hours",
        "Modelled estimate: annual_hours_returned_low = annual_transitions x "
        f"hours_returned_low = {_fmt(transitions)} x {_fmt(hours_returned_low)} = {_fmt(annual_low)} hours",
        "Modelled estimate: annual_hours_returned_high = annual_transitions x "
        f"hours_returned_high = {_fmt(transitions)} x {_fmt(hours_returned_high)} = {_fmt(annual_high)} hours",
        "Modelled estimate: annual_hours_returned = annual_transitions x hours_returned = "
        f"{_fmt(transitions)} x {_fmt(hours_returned)} = {_fmt(annual_mid)} hours",
        "Modelled estimate: days_to_funded_target = "
        f"{_fmt(target_days)} days from signature to a funded account (target, not a measurement)",
        "Modelled estimate: seeded_book.accounts_in_scope = "
        f"{_fmt(seeded_accounts)} accounts in the seeded book, every account counted once",
        "Modelled estimate: seeded_book.baseline_hours = accounts_in_scope x "
        "minutes_per_account / 60 + planning_hours x accounts_in_scope / accounts = "
        f"{_fmt(seeded_accounts)} x {_fmt(minutes)} / 60 + {_fmt(planning)} x "
        f"{_fmt(seeded_accounts)} / {_fmt(reference_accounts)} = "
        f"{_fmt(seeded_account_hours)} + {_fmt(seeded_planning)} = {_fmt(seeded_baseline)} hours",
        "Modelled estimate: seeded_book.hours_returned_low = seeded_book.baseline_hours x "
        f"reduction_low = {_fmt(seeded_baseline)} x {_fmt(reduction_low)} = "
        f"{_fmt(seeded_baseline * reduction_low)}, rounded to {_fmt(seeded_low)} hours",
        "Modelled estimate: seeded_book.hours_returned_high = seeded_book.baseline_hours x "
        f"reduction_high = {_fmt(seeded_baseline)} x {_fmt(reduction_high)} = "
        f"{_fmt(seeded_baseline * reduction_high)}, rounded to {_fmt(seeded_high)} hours",
        "Modelled estimate: nigo_caught = open or monitoring high and medium NIGO risks "
        f"raised by build_plan(book) = {nigo_caught} (a count of the seeded book, not a "
        "production measurement)",
    ]

    return {
        "model": "account-based",
        "baseline_hours": baseline,
        "assisted_hours_low": assisted_hours_low,
        "assisted_hours_high": assisted_hours_high,
        "hours_returned_low": hours_returned_low,
        "hours_returned_high": hours_returned_high,
        "hours_returned": hours_returned,
        "annual_transitions": _count(transitions),
        "annual_hours_returned_low": annual_low,
        "annual_hours_returned_high": annual_high,
        "annual_hours_returned": annual_mid,
        "days_to_funded_target": _count(target_days),
        "nigo_caught": nigo_caught,
        # Legacy keys kept so existing callers and screens keep working. The single
        # assisted figure is the midpoint of the assisted range, not a new assumption.
        "assisted_hours": assisted_hours,
        "hours_baseline": baseline,
        "hours_assisted": assisted_hours,
        "reference_book": {
            "clients": _count(clients),
            "accounts_per_household": _count(accounts_per_household),
            "accounts": _count(reference_accounts),
            "minutes_per_account": _count(minutes),
            "account_hours": reference_account_hours,
            "planning_hours": _hours(planning),
        },
        "seeded_book": {
            "households": len(households(book)),
            "accounts": seeded_accounts,
            "accounts_in_scope": seeded_accounts,
            "account_hours": seeded_account_hours,
            "planning_hours": seeded_planning,
            "baseline_hours": seeded_baseline,
            "hours_returned_low": seeded_low,
            "hours_returned_high": seeded_high,
        },
        "formulas": formulas,
        "assumptions": {
            "model": "account-based",
            "clients": _count(clients),
            "accounts_per_household": _count(accounts_per_household),
            "accounts": _count(reference_accounts),
            "minutes_per_account": _count(minutes),
            "planning_hours": _hours(planning),
            "reduction_low": round(reduction_low, 4),
            "reduction_high": round(reduction_high, 4),
            "baseline_hours": baseline,
            "assisted_hours": assisted_hours,
            "annual_transitions": _count(transitions),
            "days_to_funded_target": _count(target_days),
            "nigo_caught": nigo_caught,
            "provenance": PROVENANCE,
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


def _validate(merged: dict[str, float]) -> None:
    if merged["clients"] <= 0:
        raise ValueError("clients must be greater than zero")
    if merged["accounts_per_household"] <= 0:
        raise ValueError("accounts_per_household must be greater than zero")
    if merged["minutes_per_account"] < 0:
        raise ValueError("minutes_per_account cannot be negative")
    if merged["planning_hours"] < 0:
        raise ValueError("planning_hours cannot be negative")
    if not 0 <= merged["reduction_low"] <= 1:
        raise ValueError("reduction_low must be between 0 and 1")
    if not 0 <= merged["reduction_high"] <= 1:
        raise ValueError("reduction_high must be between 0 and 1")
    if merged["reduction_low"] > merged["reduction_high"]:
        raise ValueError("reduction_low cannot exceed reduction_high")
    if merged["annual_transitions"] < 0:
        raise ValueError("annual_transitions cannot be negative")
    if merged["days_to_funded_target"] <= 0:
        raise ValueError("days_to_funded_target must be greater than zero")


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
    text = f"{number:,.2f}"
    return text.rstrip("0").rstrip(".")


def _hours(value: Any) -> float:
    return round(float(value), 2)


def _whole_hours(value: float) -> float:
    """Round to the nearest whole hour, half up, so the printed arithmetic holds."""

    number = float(value)
    if number < 0:
        return -float(int(abs(number) + 0.5))
    return float(int(number + 0.5))


def _count(value: Any) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else round(number, 2)
