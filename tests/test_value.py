"""Account-based value arithmetic: 435 baseline, 109 to 152 returned, 4,360 to 6,080.

Every figure in the model is a modelled estimate. These tests check the arithmetic the
formulas show, not any timing measured in a production workflow.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline import load_book  # noqa: E402
from app.pipeline.plan import build_plan  # noqa: E402
from app.pipeline.value import value_model  # noqa: E402


# ---------------------------------------------------------------------------
# Reference book and the 435 hour baseline
# ---------------------------------------------------------------------------


def test_reference_book_is_100_clients_at_5_accounts_each():
    model = value_model({}, None)
    assert model["model"] == "account-based"
    assert model["reference_book"] == {
        "clients": 100,
        "accounts_per_household": 5,
        "accounts": 500,
        "minutes_per_account": 45,
        "account_hours": 375.0,
        "planning_hours": 60.0,
    }
    assert model["annual_transitions"] == 40
    assert model["days_to_funded_target"] == 45


def test_baseline_is_375_account_hours_plus_60_planning_hours():
    model = value_model({}, None)
    assert model["baseline_hours"] == 435.0
    reference = model["reference_book"]
    assert reference["account_hours"] == 375.0
    assert reference["planning_hours"] == 60.0
    assert reference["account_hours"] + reference["planning_hours"] == model["baseline_hours"]


def test_assisted_range_is_283_to_326_hours():
    model = value_model({}, None)
    assert model["assisted_hours_low"] == 283.0
    assert model["assisted_hours_high"] == 326.0


# ---------------------------------------------------------------------------
# Hours returned, midpoint, and the annual range
# ---------------------------------------------------------------------------


def test_hours_returned_range_is_109_to_152():
    model = value_model({}, None)
    assert model["hours_returned_low"] == 109.0
    assert model["hours_returned_high"] == 152.0
    # The ends add up: baseline minus each assisted end.
    assert model["baseline_hours"] - model["assisted_hours_high"] == 109.0
    assert model["baseline_hours"] - model["assisted_hours_low"] == 152.0


def test_midpoint_is_130_point_5_hours():
    model = value_model({}, None)
    assert model["hours_returned"] == 130.5
    assert model["assisted_hours"] == 304.5


def test_annual_range_is_4360_to_6080_hours():
    model = value_model({}, None)
    assert model["annual_hours_returned_low"] == 4360.0
    assert model["annual_hours_returned_high"] == 6080.0
    assert model["annual_hours_returned"] == 5220.0
    assert model["annual_hours_returned_low"] == 40 * 109.0
    assert model["annual_hours_returned_high"] == 40 * 152.0


# ---------------------------------------------------------------------------
# Formulas are readable and checkable
# ---------------------------------------------------------------------------


def test_formulas_show_each_step_of_the_arithmetic():
    formulas = "\n".join(value_model({}, None)["formulas"])
    assert "100 x 5 = 500 accounts" in formulas
    assert "500 x 45 / 60 = 375 hours" in formulas
    assert "375 + 60 = 435 hours per transition" in formulas
    assert "435 x 0.25 = 108.75, rounded to 109 hours" in formulas
    assert "435 x 0.35 = 152.25, rounded to 152 hours" in formulas
    assert "435 - 152 = 283 hours" in formulas
    assert "435 - 109 = 326 hours" in formulas
    assert "(109 + 152) / 2 = 130.5 hours" in formulas
    assert "40 x 109 = 4,360 hours" in formulas
    assert "40 x 152 = 6,080 hours" in formulas
    assert "40 x 130.5 = 5,220 hours" in formulas


def test_every_formula_is_labelled_a_modelled_estimate():
    formulas = value_model({}, None)["formulas"]
    assert formulas
    assert all(formula.lower().startswith("modelled estimate") for formula in formulas)
    assert all("modelled estimate" in formula.lower() for formula in formulas)


def test_legacy_flat_model_keys_are_still_present():
    model = value_model({}, None)
    for key in (
        "baseline_hours",
        "assisted_hours",
        "hours_returned",
        "annual_transitions",
        "annual_hours_returned",
        "days_to_funded_target",
        "nigo_caught",
        "formulas",
        "assumptions",
        # Interface names from the build spec.
        "hours_baseline",
        "hours_assisted",
    ):
        assert key in model


# ---------------------------------------------------------------------------
# Assumption overrides
# ---------------------------------------------------------------------------


def test_overrides_recompute_the_whole_model():
    model = value_model(
        {},
        {
            "clients": 50,
            "accounts_per_household": 4,
            "minutes_per_account": 60,
            "planning_hours": 40,
        },
    )
    assert model["reference_book"]["accounts"] == 200
    assert model["reference_book"]["account_hours"] == 200.0
    assert model["baseline_hours"] == 240.0
    assert model["hours_returned_low"] == 60.0
    assert model["hours_returned_high"] == 84.0
    assert model["hours_returned"] == 72.0
    assert model["annual_hours_returned_low"] == 2400.0
    assert model["annual_hours_returned_high"] == 3360.0
    assert "200 x 60 / 60 = 200 hours" in "\n".join(model["formulas"])


def test_alias_assumptions_are_accepted():
    model = value_model(
        {},
        {
            "transitions_per_year": 10,
            "target_days_to_funded": 60,
            "reduction_min": 0.2,
            "reduction_max": 0.4,
        },
    )
    assert model["annual_transitions"] == 10
    assert model["days_to_funded_target"] == 60
    assert model["hours_returned_low"] == 87.0
    assert model["hours_returned_high"] == 174.0
    assert model["annual_hours_returned"] == 10 * 130.5


# ---------------------------------------------------------------------------
# Seeded book
# ---------------------------------------------------------------------------


def test_seeded_book_scales_the_reference_rates_to_the_seeded_data():
    model = value_model(load_book(), None)
    seeded = model["seeded_book"]
    assert seeded["households"] == 12
    assert seeded["accounts"] == 18
    assert seeded["accounts_in_scope"] == 18
    assert seeded["account_hours"] == 13.5
    assert seeded["planning_hours"] == 2.16
    assert seeded["baseline_hours"] == 15.66  # 13.5 account hours + 2.16 scaled planning
    assert seeded["hours_returned_low"] == 4.0
    assert seeded["hours_returned_high"] == 5.0
    # The reference model itself does not move when the book is small.
    assert model["baseline_hours"] == 435.0


def test_seeded_book_formula_shows_the_scaling():
    formulas = "\n".join(value_model(load_book(), None)["formulas"])
    assert "18 x 45 / 60 + 60 x 18 / 500 = 13.5 + 2.16 = 15.66 hours" in formulas


def test_empty_book_has_no_seeded_scope():
    model = value_model({}, None)
    assert model["seeded_book"]["households"] == 0
    assert model["seeded_book"]["accounts_in_scope"] == 0
    assert model["seeded_book"]["baseline_hours"] == 0.0


# ---------------------------------------------------------------------------
# NIGO count
# ---------------------------------------------------------------------------


def test_nigo_caught_counts_open_high_and_medium_risks():
    book = {
        "book": {
            "advisor_name": "Dana Whitfield",
            "signed_at": "2026-11-01",
            "target_live_at": "2026-12-16",
        },
        "households": [
            {"id": "hh-1", "display_name": "Test Household", "aum": 1000000, "consent_status": "received"}
        ],
        "accounts": [
            {
                "id": "acct-1",
                "household_id": "hh-1",
                "custodian": "Alder Clearing",
                "account_type": "Irrevocable trust",
                "registration": "Irrevocable trust",
                "masked_number": "****1001",
                "hard_case": "Irrevocable trust",
                "status": "in flight",
            }
        ],
    }
    plan = build_plan(book)
    expected = sum(
        1
        for risk in plan["risks"]
        if risk["status"] in {"open", "monitor"} and risk["severity"] in {"high", "medium"}
    )
    assert expected >= 1
    assert value_model(book, None)["nigo_caught"] == expected


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"clients": 0},
        {"accounts_per_household": 0},
        {"minutes_per_account": -5},
        {"planning_hours": -1},
        {"reduction_low": -0.1},
        {"reduction_high": 1.5},
        {"reduction_low": 0.4, "reduction_high": 0.2},
        {"annual_transitions": -1},
        {"days_to_funded_target": 0},
    ],
)
def test_invalid_assumptions_raise(overrides):
    with pytest.raises(ValueError):
        value_model({}, overrides)


def test_non_numeric_assumption_raises():
    with pytest.raises(ValueError):
        value_model({}, {"minutes_per_account": "a lot"})
