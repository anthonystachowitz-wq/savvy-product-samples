"""Broker Protocol consent gating, the blocked client draft, and signature load.

The seeded book has consent received for 8 households, pending for H-1043, H-1044, and
H-1045, and not required for H-1053. These tests cover both consent paths, the named
check, the blocked draft, and the plan additions that carry the protocol work.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline import load_book  # noqa: E402
from app.pipeline.draft import draft  # noqa: E402
from app.pipeline.guardrails import CHECKS, check_draft, consent_blocked  # noqa: E402
from app.pipeline.plan import PHASE_TITLES, build_plan  # noqa: E402

PENDING_HOUSEHOLDS = ["H-1043", "H-1044", "H-1045"]
PERMITTED_FIELDS = ("client name", "address", "phone", "email", "account title")


def _book() -> dict:
    return load_book()


def _client_draft(household_id: str, body: str = "A plain note with no numbers.") -> dict:
    return {
        "kind": "client_update",
        "audience": "client",
        "body": body,
        "claims": [],
        "household_id": household_id,
    }


def _result(checks: list[dict], name: str) -> dict:
    return next(check for check in checks if check["check"] == name)


# ---------------------------------------------------------------------------
# The check list keeps its order and gains one name
# ---------------------------------------------------------------------------


def test_protocol_consent_is_appended_without_reordering_the_existing_six():
    assert CHECKS[:6] == (
        "advice_language",
        "promised_timing",
        "performance_claim",
        "testimonial",
        "uncited_number",
        "missing_disclosure",
    )
    assert CHECKS[6:] == ("protocol_consent",)


def test_check_draft_returns_every_check_in_the_stable_order():
    names = [check["check"] for check in check_draft(_client_draft("H-1043"), _book())]
    assert names == list(CHECKS)


# ---------------------------------------------------------------------------
# Pass and fail paths
# ---------------------------------------------------------------------------


def test_pending_consent_fails_a_client_facing_draft():
    result = _result(check_draft(_client_draft("H-1043"), _book()), "protocol_consent")
    assert result["result"] == "fail"
    detail = result["detail"].lower()
    for field in PERMITTED_FIELDS:
        assert field in detail
    assert "no client contact before resignation" in detail
    assert "H-1043" in result["detail"]


def test_received_consent_passes_a_client_facing_draft():
    result = _result(check_draft(_client_draft("H-1042"), _book()), "protocol_consent")
    assert result["result"] == "pass"
    assert "received" in result["detail"].lower()


def test_not_required_consent_passes_a_client_facing_draft():
    result = _result(check_draft(_client_draft("H-1053"), _book()), "protocol_consent")
    assert result["result"] == "pass"


def test_non_client_drafts_pass_with_pending_consent():
    for kind in ("custodian_cover", "internal_digest"):
        checks = check_draft(
            {"kind": kind, "body": "A plain letter.", "claims": [], "household_id": "H-1043"},
            _book(),
        )
        result = _result(checks, "protocol_consent")
        assert result["result"] == "pass"
        assert "not client-facing" in result["detail"].lower()


def test_a_client_draft_with_no_household_does_not_gate():
    result = _result(
        check_draft({"kind": "client_update", "body": "A plain note.", "claims": []}, _book()),
        "protocol_consent",
    )
    assert result["result"] == "pass"


# ---------------------------------------------------------------------------
# consent_blocked helper
# ---------------------------------------------------------------------------


def test_consent_blocked_reports_pending_households():
    blocked, detail = consent_blocked(_book(), "H-1043")
    assert blocked is True
    assert "pending" in detail.lower()
    for field in PERMITTED_FIELDS:
        assert field in detail.lower()


def test_consent_blocked_reports_received_and_not_required():
    blocked, detail = consent_blocked(_book(), "H-1042")
    assert blocked is False
    assert "received" in detail.lower()

    blocked, detail = consent_blocked(_book(), "H-1053")
    assert blocked is False
    assert "does not require" in detail.lower()


def test_consent_blocked_is_false_for_an_unknown_household():
    blocked, detail = consent_blocked(_book(), "H-9999")
    assert blocked is False
    assert "not in the book" in detail.lower()


def test_consent_blocked_is_false_without_a_book():
    blocked, detail = consent_blocked({}, "H-1043")
    assert blocked is False
    assert detail


# ---------------------------------------------------------------------------
# Drafts that must come back blocked
# ---------------------------------------------------------------------------


def test_pending_client_update_comes_back_blocked():
    payload = draft("client_update", "H-1043", _book())
    assert [item["check"] for item in payload["blocked"]] == ["protocol_consent"]
    assert 90 <= payload["word_count"] <= 160
    assert payload["claims"]
    assert [check["check"] for check in payload["policy_checks"]] == list(CHECKS)
    assert _result(payload["policy_checks"], "protocol_consent")["result"] == "fail"
    assert all(check["result"] == "pass" for check in payload["policy_checks"][:6])


def test_received_client_update_is_not_blocked():
    payload = draft("client_update", "H-1042", _book())
    assert payload["blocked"] == []
    assert all(check["result"] == "pass" for check in payload["policy_checks"])


def test_custodian_cover_and_digest_pass_for_a_pending_household():
    for kind in ("custodian_cover", "internal_digest"):
        payload = draft(kind, "H-1043", _book())
        result = _result(payload["policy_checks"], "protocol_consent")
        assert result["result"] == "pass", kind
        assert not any(item["check"] == "protocol_consent" for item in payload["blocked"])


# ---------------------------------------------------------------------------
# Plan: signature load, consent list, and wave 1
# ---------------------------------------------------------------------------


def test_plan_adds_one_signature_packet_task_per_account():
    book = _book()
    plan = build_plan(book)
    packets = [task for task in plan["tasks"] if task["id"].endswith("-signature-packet")]
    assert len(packets) == len(book["accounts"]) == 18
    assert {task["account_id"] for task in packets} == {account["id"] for account in book["accounts"]}
    assert {task["phase"] for task in packets} <= {"document_collection", "account_opening"}


def test_signature_packet_title_names_the_count_and_the_custodian():
    plan = build_plan(_book())
    task = next(task for task in plan["tasks"] if task["id"] == "task-A-2201-signature-packet")
    assert task["title"] == "Collect 2 signatures on Alder Clearing account opening packet"


def test_existing_phase_tasks_are_untouched():
    book = _book()
    plan = build_plan(book)
    for account in book["accounts"]:
        titles = {task["title"] for task in plan["tasks"] if task["account_id"] == account["id"]}
        assert set(PHASE_TITLES.values()) <= titles, account["id"]


def test_signature_load_totals_by_account_and_by_household():
    load = build_plan(_book())["signature_load"]
    assert load["total"] == 61
    assert load["by_account"]["A-2201"] == 2
    assert load["by_account"]["A-2203"] == 6
    assert load["by_household"]["H-1050"] == 8
    assert sum(load["by_account"].values()) == 61
    assert sum(load["by_household"].values()) == 61


def test_consent_pending_households_are_listed_in_book_order():
    assert build_plan(_book())["consent_pending_households"] == PENDING_HOUSEHOLDS


def test_signature_packet_for_a_pending_household_is_blocked():
    plan = build_plan(_book())
    pending = next(task for task in plan["tasks"] if task["id"] == "task-A-2203-signature-packet")
    assert pending["status"] == "blocked"
    assert "consent" in pending["blocker"].lower()

    received = next(task for task in plan["tasks"] if task["id"] == "task-A-2201-signature-packet")
    assert received["status"] == "open"
    assert received["blocker"] == ""


def test_summary_keeps_its_keys_and_wave1_uses_the_explicit_wave_field():
    book = _book()
    summary = build_plan(book)["summary"]
    assert set(summary) == {"accounts", "wave1", "blocked", "high_risks"}
    explicit = sum(1 for account in book["accounts"] if str(account.get("wave")) == "1")
    assert summary["wave1"] == explicit == 12


def test_a_book_without_wave_or_signature_data_still_plans():
    book = {
        "book": {"signed_at": "2026-11-01", "target_live_at": "2026-12-16"},
        "households": [{"id": "hh-1", "display_name": "Test Household"}],
        "accounts": [
            {
                "id": "acct-1",
                "household_id": "hh-1",
                "custodian": "Alder Clearing",
                "account_type": "Brokerage",
                "registration": "JTWROS",
                "masked_number": "****1001",
                "status": "in flight",
            }
        ],
    }
    plan = build_plan(book)
    assert plan["summary"]["wave1"] == 1
    assert plan["signature_load"] == {"total": 0, "by_account": {}, "by_household": {}}
    assert plan["consent_pending_households"] == []
