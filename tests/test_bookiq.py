"""Tests for Book IQ: data integrity, arithmetic, parsing, and guardrails."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.bookiq import data, logic
from app.modules.bookiq.routes import router

ROOT = Path(__file__).resolve().parents[1]


def test_generated_book_matches_the_seeded_book():
    seeded = json.loads((ROOT / "data" / "book.json").read_text(encoding="utf-8"))
    payload = data.households_payload()
    households = {item["id"]: item for item in payload["households"]}
    assert len(households) == 12
    for original in seeded["households"]:
        generated = households[original["id"]]
        assert generated["aum"] == original["aum"]
        assert generated["name"] == original["display_name"]
        assert generated["consent_status"] == original["consent_status"]
        assert generated["account_count"] >= 1


def test_eval_cases_cover_every_household():
    cases = data.eval_cases()
    items = cases["items"]
    assert cases["cases"] == 12
    assert {item["household_id"] for item in items} == {item["id"] for item in data.households()}
    for item in items:
        assert item["expected_play"] in logic.PLAYS
        assert item["required_signal"] in logic.SIGNAL_LABELS
        for alternative in item["alternatives"]:
            assert alternative["play"] in logic.PLAYS


def test_consolidation_arithmetic():
    household = data.household("H-1047")
    result = logic.opportunity(household, "consolidate_held_away")
    assert result["applicable"] is True
    assert result["input_usd"] == 1150000
    assert result["assets_usd"] == 287500
    assert result["fee_usd"] == int(round(287500 * logic.FEE_RATE))
    assert "25%" in result["formula"]


def test_referral_and_life_event_arithmetic():
    calloway = data.household("H-1043")
    referral = logic.opportunity(calloway, "referral")
    assert referral["assets_usd"] == 850000
    assert referral["fee_usd"] == 7650

    voss = data.household("H-1045")
    life = logic.opportunity(voss, "life_event")
    assert life["assets_usd"] == 650000
    assert life["fee_usd"] == 5850


def test_unsupported_play_models_zero():
    sato = data.household("H-1046")
    result = logic.opportunity(sato, "consolidate_held_away")
    assert result["applicable"] is False
    assert result["assets_usd"] == 0


def test_rule_check_prefers_the_larger_play():
    delgado = data.household("H-1047")
    assert logic.rule_play(delgado)["play"] == "consolidate_held_away"
    barrett = data.household("H-1044")
    assert logic.rule_play(barrett)["play"] == "no_action"
    lindqvist = data.household("H-1049")
    assert logic.rule_play(lindqvist)["play"] == "referral"


def test_parse_ranking_handles_fences_and_aliases():
    text = (
        "Here is the ranking:\n"
        "~~~json\n"
        '[{"household_id":"H-1042","play":"Consolidate held-away assets",'
        '"confidence":0.8,"reason":"USD 1,840,000 held away.","signal":"held_away"}]\n'
        "~~~\n"
    )
    rows, meta = logic.parse_ranking(text)
    assert meta["error"] == ""
    assert len(rows) == 1
    assert rows[0]["play"] == "consolidate_held_away"
    assert rows[0]["signal"] == "held_away"


def test_parse_ranking_survives_bad_json():
    rows, meta = logic.parse_ranking("[not json at all")
    assert rows == []
    assert meta["error"]


def test_parse_draft():
    draft, error = logic.parse_draft(
        '{"subject":"A review","body":"Your household AUM is USD 6,180,000.",'
        '"claims":[{"text":"USD 6,180,000","source_field":"aum"}]}'
    )
    assert error == ""
    assert draft["subject"] == "A review"
    assert draft["claims"][0]["source_field"] == "aum"


def test_guardrail_blocks_an_untraced_number():
    household = data.household("H-1042")
    draft = {
        "subject": "A review",
        "body": "Your held-away assets are USD 9,999,999.",
        "claims": [{"text": "USD 9,999,999", "source_field": "held_away_usd"}],
    }
    report = logic.guardrail(draft, household)
    assert report["ok"] is False
    assert any(row["number"] == "9,999,999" for row in report["untraced"])


def test_guardrail_blocks_a_performance_promise():
    household = data.household("H-1042")
    draft = {
        "subject": "A review",
        "body": "This is guaranteed to grow your household assets.",
        "claims": [],
    }
    report = logic.guardrail(draft, household)
    assert report["ok"] is False
    assert any(flag["term"] == "guaranteed" for flag in report["flags"])


def test_guardrail_accepts_a_traced_draft():
    household = data.household("H-1042")
    draft = {
        "subject": "A review",
        "body": "Your household AUM is USD 6,180,000 across 2 accounts.",
        "claims": [
            {"text": "USD 6,180,000", "source_field": "aum"},
            {"text": "2 accounts", "source_field": "account_count"},
        ],
    }
    report = logic.guardrail(draft, household)
    assert report["ok"] is True
    assert all(claim["status"] == "verified" for claim in report["claims"])


def test_pending_consent_blocks_approval():
    household = data.household("H-1043")
    draft = {"subject": "A review", "body": "Your household is recorded.", "claims": []}
    report = logic.guardrail(draft, household)
    assert logic.approval_block(household, draft, report) == "consent"


def test_evidence_record_fields():
    household = data.household("H-1042")
    draft = {"subject": "A review", "body": "Your household AUM is USD 6,180,000.", "claims": []}
    report = logic.guardrail(draft, household)
    evidence = logic.make_evidence(
        household=household,
        draft=draft,
        report=report,
        approver="Dana Whitfield",
        role="Adviser",
        note="Test note.",
        model="deepseek-flash",
        mode="live model, recorded",
        cache_key=logic.draft_key("H-1042"),
        sequence=3,
        decided_at=datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc),
    )
    assert evidence["id"] == "EV-BIQ-H-1042-03"
    assert evidence["retention_until"] == "2031-11-19"
    assert evidence["claims_total"] == 0
    assert evidence["channel"] == "Email draft, not sent"


def test_pages_render():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    queue = client.get("/bookiq")
    assert queue.status_code == 200
    assert "Next-best-action queue" in queue.text
    assert "H-1042" in queue.text
    assert "Modelled net new assets" in queue.text

    detail = client.get("/bookiq?h=H-1042")
    assert detail.status_code == 200
    assert "Adviser outreach draft" in detail.text
    assert "Evidence from the record" in detail.text

    blocked = client.get("/bookiq?h=H-1043")
    assert blocked.status_code == 200
    assert "Approval is blocked while consent is pending" in blocked.text

    missing = client.get("/bookiq?h=H-9999")
    assert missing.status_code == 200
    assert "That household is not in the book." in missing.text


def test_eval_file_is_in_range():
    report = data.eval_report()
    if not report:
        return
    assert 0.0 <= float(report["accuracy"]) <= 1.0
    assert int(report["cases"]) == 12
    assert len(report["results"]) == 12
