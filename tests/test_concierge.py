"""Client Concierge: data integrity, the deterministic checks, and the routes.

The tests that touch the routes point the state file and the audit database at a
temporary directory so the demo state is never written by a test run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.concierge import data, logic  # noqa: E402
from app.platform import audit, config, state  # noqa: E402

BOOK = json.loads((ROOT / "data" / "book.json").read_text(encoding="utf-8"))
RECORDS = data.records()
QUESTIONS = data.questions()
CASES = data.eval_cases()


def _household(household_id: str) -> dict:
    household = data.household(RECORDS, household_id)
    assert household is not None
    return household


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def test_three_households_and_the_book_is_the_source():
    assert [item["id"] for item in RECORDS["households"]] == ["H-1042", "H-1043", "H-1045"]
    assert RECORDS["adviser"] == "Dana Whitfield"
    for household in RECORDS["households"]:
        book_household = next(item for item in BOOK["households"] if item["id"] == household["id"])
        assert household["total_cents"] == int(book_household["aum"]) * 100


def test_account_values_come_from_positions():
    for household in RECORDS["households"]:
        total = 0
        for account in household["accounts"]:
            positions = [item for item in BOOK["positions"] if item["account_id"] == account["id"]]
            expected = sum(int(round(float(item["value"]) * 100)) for item in positions)
            assert account["value_cents"] == expected
            total += expected
        assert total == household["total_cents"]


def test_field_paths_are_unique_and_cover_every_number_in_the_record():
    for household in RECORDS["households"]:
        paths = [field["path"] for field in household["fields"]]
        assert len(paths) == len(set(paths))
        assert "household.total_value" in paths
        assert "performance.return_pct" in paths
        assert "fees.ytd_total" in paths
        for account in household["accounts"]:
            assert "accounts." + account["id"] + ".value" in paths


def test_performance_and_fee_arithmetic_is_internal():
    for household in RECORDS["households"]:
        performance = household["performance"]
        assert (
            performance["beginning_cents"]
            + performance["net_contributions_cents"]
            + performance["market_change_cents"]
            == performance["ending_cents"]
        )
        assert performance["ending_cents"] == household["total_cents"]
        fees = household["fees"]
        assert sum(item["amount_cents"] for item in fees["charges"]) == fees["ytd_total_cents"]
        assert fees["charges_billed_ytd"] == len(fees["charges"]) == 3


def test_consent_status_matches_the_book():
    for household in RECORDS["households"]:
        book_household = next(item for item in BOOK["households"] if item["id"] == household["id"])
        assert household["consent_status"] == book_household["consent_status"]
    assert logic.blocked_reason(_household("H-1042")) == ""
    assert "Broker Protocol" in logic.blocked_reason(_household("H-1043"))


def test_questions_and_recordings_are_complete():
    assert len(QUESTIONS["questions"]) == 6
    assert len([item for item in QUESTIONS["questions"] if item["preset"]]) == 5
    keys = data.key_index(QUESTIONS)
    assert len(keys) == 18
    for key, entry in keys.items():
        household = data.household(RECORDS, entry["household_id"])
        assert household is not None, key
        assert data.question_index(QUESTIONS).get(entry["question_id"]) is not None, key


def test_eval_cases_reference_real_fields():
    cases = CASES["cases"]
    assert len(cases) == 10
    assert {case["household_id"] for case in cases} == {"H-1042", "H-1043", "H-1045"}
    assert {case["expected_intent"] for case in cases} == {"explain", "advice", "transaction", "unsupported"}
    for case in cases:
        index = logic.field_index(_household(case["household_id"]))
        for field in case["required_fields"]:
            assert field in index, (case["id"], field)
        assert case["key"] in data.key_index(QUESTIONS)


def test_every_question_has_a_recording():
    cache = config.CACHE_DIR / logic.MODULE
    missing = [key for key in data.key_index(QUESTIONS) if not (cache / (key + ".json")).is_file()]
    assert missing == []


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_answer_accepts_strict_json():
    raw = json.dumps(
        {
            "intent": "explain",
            "answer": "The total is USD 6,180,000.00.",
            "citations": [{"field": "household.total_value", "value": "USD 6,180,000.00"}],
            "route_to_advisor": False,
        }
    )
    parsed = logic.parse_answer(raw)
    assert parsed["intent"] == "explain"
    assert parsed["parse_error"] == ""
    assert parsed["citations"] == [{"field": "household.total_value", "value": "USD 6,180,000.00"}]


def test_parse_answer_handles_a_code_fence_and_trailing_text():
    payload = json.dumps({"intent": "advice", "answer": "No.", "citations": [], "route_to_advisor": False})
    raw = "Here you go:" + chr(10) + "```" + "json" + chr(10) + payload + chr(10) + "```"
    parsed = logic.parse_answer(raw)
    assert parsed["intent"] == "advice"
    assert parsed["parse_error"] == ""
    assert parsed["route_to_advisor"] is True


def test_parse_answer_keeps_the_raw_text_on_bad_json():
    parsed = logic.parse_answer("This is not JSON at all.")
    assert parsed["parse_error"]
    assert parsed["raw"] == "This is not JSON at all."
    assert parsed["intent"] == "unsupported"


def test_parse_answer_flags_an_unknown_intent():
    parsed = logic.parse_answer(json.dumps({"intent": "smalltalk", "answer": "Hello.", "citations": []}))
    assert parsed["intent"] == "unsupported"
    assert "outside the four allowed labels" in parsed["parse_error"]


def test_parse_answer_strips_markdown_and_em_dashes():
    answer = "**Total** is fine " + chr(8212) + " and stable."
    parsed = logic.parse_answer(json.dumps({"intent": "explain", "answer": answer, "citations": []}))
    assert parsed["answer"] == "Total is fine, and stable."


# ---------------------------------------------------------------------------
# The deterministic checks
# ---------------------------------------------------------------------------


def test_number_check_passes_when_every_number_is_cited():
    household = _household("H-1042")
    index = logic.field_index(household)
    citations = [
        {"field": "performance.market_change", "value": "USD 189,000.00"},
        {"field": "performance.return_pct", "value": "3.18%"},
    ]
    answer = "The portfolio gained USD 189,000.00, a return of 3.18%."
    check = logic.guardrail(answer, citations, index)
    assert check["ok"] is True
    assert check["checked"] == 2
    assert check["uncovered"] == []


def test_number_check_fails_on_a_number_with_no_cited_field():
    household = _household("H-1042")
    index = logic.field_index(household)
    citations = [{"field": "household.total_value", "value": "USD 6,180,000.00"}]
    check = logic.guardrail("The total is USD 6,180,000.00 and the fee is USD 38,382.39.", citations, index)
    assert check["ok"] is False
    assert check["uncovered"] == ["38382.39"]


def test_number_check_ignores_dates_years_and_ids():
    household = _household("H-1042")
    index = logic.field_index(household)
    answer = "On 2026-09-30 in Q3 2026 the account H-1042 and A-2201 were reviewed."
    check = logic.guardrail(answer, [], index)
    assert check["checked"] == 0
    assert check["ok"] is True


def test_a_citation_to_an_unknown_field_fails_the_check():
    household = _household("H-1042")
    index = logic.field_index(household)
    citations = [{"field": "holdings.A-2201.NVDA.value", "value": "USD 22,000.00"}]
    resolved = logic.resolve_citations(citations, index)
    assert resolved[0]["status"] == "unknown"
    check = logic.guardrail("The position is USD 22,000.00.", citations, index)
    assert check["ok"] is False
    assert check["unresolved"] == ["holdings.A-2201.NVDA.value"]


def test_a_mismatched_citation_value_fails_the_check():
    household = _household("H-1042")
    index = logic.field_index(household)
    citations = [{"field": "fees.ytd_total", "value": "USD 40,000.00"}]
    resolved = logic.resolve_citations(citations, index)
    assert resolved[0]["status"] == "mismatch"
    assert resolved[0]["value"] == "USD 38,382.39"
    assert logic.guardrail("Fees were USD 40,000.00.", citations, index)["ok"] is False


def test_citation_values_compare_after_normalisation():
    assert logic.values_match("6,180,000.00", "USD 6,180,000.00") is True
    assert logic.values_match("0.85%", "0.85%") is True
    assert logic.values_match("three", "3") is False


def test_free_text_router_maps_each_preset_and_reports_a_gap():
    for question in QUESTIONS["questions"]:
        routed = logic.route_free_text(question["text"], QUESTIONS)
        assert routed["question_id"] == question["id"], question["text"]
    assert logic.route_free_text("How much are you charging me in fees?", QUESTIONS)["question_id"] == "q2"
    assert logic.route_free_text("Please wire money to my bank", QUESTIONS)["question_id"] == "q4"
    assert logic.route_free_text("What is the weather tomorrow?", QUESTIONS)["question_id"] == ""


def test_blocked_reason_only_names_pending_households():
    assert "Broker Protocol consent is pending" in logic.blocked_reason(_household("H-1043"))
    assert "Broker Protocol consent is pending" in logic.blocked_reason(_household("H-1045"))
    assert logic.blocked_reason(_household("H-1042")) == ""


# ---------------------------------------------------------------------------
# The evaluation runner
# ---------------------------------------------------------------------------


def test_forbidden_phrase_check():
    from app.modules.concierge import eval as concierge_eval

    assert concierge_eval.forbidden_hits("including the Nvidia position", ["nvidia"]) == ["nvidia"]
    assert concierge_eval.forbidden_hits("the record shows no such holding", ["nvidia"]) == []
    assert concierge_eval.forbidden_hits("", ["nvidia"]) == []


def test_the_eval_carries_a_forbidden_phrase_label():
    assert any(case.get("forbidden") for case in CASES["cases"])


def test_eval_runs_over_the_recordings_without_writing():
    from app.modules.concierge import eval as concierge_eval

    payload = concierge_eval.evaluate(write=False)
    assert payload["cases"] == 10
    assert 0.0 <= payload["accuracy"] <= 1.0
    assert payload["by_intent"]["advice"]["cases"] == 2
    assert 0 <= payload["by_intent"]["advice"]["passed"] <= 2
    assert len(payload["results"]) == 10
    for failure in payload["failures"]:
        assert failure["case"]
        assert failure["reasons"]


# ---------------------------------------------------------------------------
# Routes, against a temporary state file and audit database
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(state, "_path", lambda module: tmp_path / "state" / (module + ".json"))
    monkeypatch.setattr(audit, "DB_PATH", tmp_path / "app.db")
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_page_renders(client):
    response = client.get("/concierge")
    assert response.status_code == 200
    body = response.text
    for needle in ("Client Concierge", "Household record", "Ask about this record", "Adviser time returned", "Audit trail"):
        assert needle in body
    assert "USD 6,180,000.00" in body


def test_a_recorded_answer_renders_with_citations(client):
    response = client.get("/concierge?h=H-1042&q=q2-h-1042")
    assert response.status_code == 200
    body = response.text
    assert "fees.ytd_total" in body
    assert "USD 38,382.39" in body
    assert "Number check passed" in body
    assert "recorded live call" in body


def test_the_pending_household_answer_is_blocked(client):
    response = client.get("/concierge?h=H-1043&q=q5-h-1043")
    assert response.status_code == 200
    body = response.text
    assert "Release blocked by protocol consent" in body
    assert "Broker Protocol consent is pending for H-1043" in body


def test_ask_posts_and_redirects_to_the_recording(client):
    response = client.post(
        "/concierge/ask", data={"household": "H-1042", "question_id": "q3", "action": "ask"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "q=q3-h-1042" in response.headers["location"]


def test_free_text_is_matched_to_a_recording_or_reported_as_a_gap(client):
    matched = client.post(
        "/concierge/ask",
        data={"household": "H-1042", "free_text": "How much am I paying in fees?"},
        follow_redirects=False,
    )
    assert "q=q2-h-1042" in matched.headers["location"]

    gap = client.post(
        "/concierge/ask",
        data={"household": "H-1042", "free_text": "What is the weather?"},
        follow_redirects=False,
    )
    assert "msg=gap" in gap.headers["location"]


def test_hold_writes_state_and_an_audit_event(client):
    response = client.post("/concierge/hold", data={"household": "H-1042", "key": "q2-h-1042"}, follow_redirects=False)
    assert response.status_code == 303
    payload = state.load("concierge", {})
    assert len(payload["holds"]) == 1
    hold = payload["holds"][0]
    assert hold["status"] == "held"
    assert hold["intent"] == "explain"
    assert hold["guardrail"]["ok"] is True
    assert audit.count("concierge") == 1

    duplicate = client.post("/concierge/hold", data={"household": "H-1042", "key": "q2-h-1042"}, follow_redirects=False)
    assert "msg=already_held" in duplicate.headers["location"]
    assert len(state.load("concierge", {})["holds"]) == 1


def test_approval_writes_evidence_and_audit(client):
    client.post("/concierge/hold", data={"household": "H-1042", "key": "q2-h-1042"}, follow_redirects=False)
    hold_id = state.load("concierge", {})["holds"][0]["id"]

    missing_role = client.post(
        "/concierge/decision", data={"hold_id": hold_id, "action": "approve", "role": ""}, follow_redirects=False
    )
    assert "msg=role_required" in missing_role.headers["location"]

    approved = client.post(
        "/concierge/decision",
        data={"hold_id": hold_id, "action": "approve", "role": "Financial adviser", "note": "Checked"},
        follow_redirects=False,
    )
    assert "msg=approved" in approved.headers["location"]
    hold = state.load("concierge", {})["holds"][0]
    assert hold["status"] == "approved"
    assert hold["evidence"]["approver"] == "Dana Whitfield"
    assert hold["evidence"]["role"] == "Financial adviser"
    assert hold["evidence"]["retention_until"]
    assert hold["evidence"]["hash"] == hold["hash"]
    actions = [row["action"] for row in audit.recent("concierge", 10)]
    assert "answer_held" in actions and "answer_approved" in actions


def test_approval_is_blocked_for_pending_consent(client):
    client.post("/concierge/hold", data={"household": "H-1043", "key": "q5-h-1043"}, follow_redirects=False)
    hold = state.load("concierge", {})["holds"][0]
    assert hold["blocked_reason"]

    response = client.post(
        "/concierge/decision",
        data={"hold_id": hold["id"], "action": "approve", "role": "Supervising principal"},
        follow_redirects=False,
    )
    assert "msg=blocked" in response.headers["location"]
    hold = state.load("concierge", {})["holds"][0]
    assert hold["status"] == "held"
    assert hold["evidence"] is None
    assert "release_blocked" in [row["action"] for row in audit.recent("concierge", 10)]


def test_rejection_records_the_note(client):
    client.post("/concierge/hold", data={"household": "H-1042", "key": "q1-h-1042"}, follow_redirects=False)
    hold_id = state.load("concierge", {})["holds"][0]["id"]
    response = client.post(
        "/concierge/decision",
        data={"hold_id": hold_id, "action": "reject", "note": "Wrong household"},
        follow_redirects=False,
    )
    assert "msg=rejected" in response.headers["location"]
    hold = state.load("concierge", {})["holds"][0]
    assert hold["status"] == "rejected"
    assert hold["decision"]["note"] == "Wrong household"
