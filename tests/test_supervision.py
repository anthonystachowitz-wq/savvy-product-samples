"""Supervision module: data, the source check, the gate, evidence, and the eval.

The tests read the committed recordings. They do not make a model call.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.platform import llm, state  # noqa: E402
from app.modules.supervision import data, eval as sup_eval, logic  # noqa: E402

MODULE = "supervision"
CLIENT = TestClient(app)


def _brief(brief_id: str) -> dict:
    return next(item for item in data.briefs_bundle()["briefs"] if item["id"] == brief_id)


def _evaluate(brief: dict, version: int = 1):
    fact_sheet = data.fact_sheet()
    draft = logic.load_draft(brief, version)
    claims = logic.load_claims(brief, fact_sheet, version)
    return draft, claims, logic.evaluate(brief, draft, claims, fact_sheet)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def test_data_covers_the_four_briefs_and_the_eval():
    bundle = data.briefs_bundle()
    assert len(bundle["briefs"]) == 4
    assert len(data.fact_sheet()["facts"]) == 11
    assert len(data.fact_sheet()["disclosures"]) == 6
    assert len(data.eval_cases()["cases"]) == 12


def test_every_brief_references_real_facts_and_disclosures():
    fact_ids = {fact["id"] for fact in data.fact_sheet()["facts"]}
    disclosure_ids = {item["id"] for item in data.fact_sheet()["disclosures"]}
    for brief in data.briefs_bundle()["briefs"]:
        assert set(brief["fact_ids"]) <= fact_ids
        assert set(brief["required_disclosures"]) <= disclosure_ids
        assert brief["draft_key"] and brief["claims_key"]


def test_consent_segment_excludes_pending_households():
    bundle = data.briefs_bundle()
    consented = set(bundle["audiences"][0]["household_ids"])
    all_book = bundle["audiences"][1]
    assert all_book["blocked"] is True
    assert not consented.intersection(all_book["pending_ids"])
    assert len(consented) == bundle["audiences"][0]["count"] == 8
    assert all_book["count"] == 12


# ---------------------------------------------------------------------------
# The deterministic source check
# ---------------------------------------------------------------------------


def test_numeric_tokens_read_percentages_and_currency():
    assert logic.numeric_tokens("12.4% and USD 42,300,000") == [
        ("percent", 12.4),
        ("currency", 42_300_000.0),
    ]
    assert logic.numeric_tokens("USD 42.3 million") == [("currency", 42_300_000.0)]
    assert logic.numeric_tokens("2026 and 12 households") == []


def test_wrong_number_on_a_cited_fact_is_downgraded():
    claims = logic.validate_claims(
        [
            {
                "text": "The practice manages USD 61,000,000.",
                "type": "factual",
                "support": "supported",
                "source": "F-01",
            }
        ],
        data.fact_sheet(),
    )
    assert claims[0]["support"] == "unsupported"
    assert claims[0]["adjusted"] is True
    assert "USD 61,000,000" in claims[0]["note"]


def test_a_disclosure_can_support_a_claim_that_carries_no_number():
    claims = logic.validate_claims(
        [
            {
                "text": "Index returns are unmanaged and do not reflect fees or expenses.",
                "type": "factual",
                "support": "supported",
                "source": "D-02",
            }
        ],
        data.fact_sheet(),
    )
    assert claims[0]["support"] == "supported"
    assert claims[0]["source_kind"] == "disclosure"


def test_a_disclosure_cannot_support_a_number():
    claims = logic.validate_claims(
        [
            {
                "text": "The fund returned 8.1% last year.",
                "type": "factual",
                "support": "supported",
                "source": "D-02",
            }
        ],
        data.fact_sheet(),
    )
    assert claims[0]["support"] == "unsupported"


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_blocked_draft_fails_on_three_checks():
    draft, claims, result = _evaluate(_brief("B-04"))
    assert draft["ok"] and claims["ok"]
    assert result["blocked"] is True
    assert result["risk_computed"] == "high"
    names = {check["name"] for check in result["blocking"]}
    assert {"Performance claim", "Unsupported claim", "Number outside the fact sheet"} == names


def test_revised_draft_passes_and_keeps_the_revision():
    brief = _brief("B-04")
    draft, claims, result = _evaluate(brief, version=2)
    assert draft["ok"] and claims["ok"]
    assert result["blocked"] is False
    assert result["counts"]["fail"] == 0
    assert draft["revision_note"]
    assert "14.2%" not in draft["body"]


def test_clean_briefs_pass_with_warnings_only():
    for brief_id in ("B-02", "B-03"):
        _, _, result = _evaluate(_brief(brief_id))
        assert result["blocked"] is False
        assert result["counts"]["fail"] == 0


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_retention_is_five_years_and_handles_a_leap_day():
    assert logic.retention_date(date(2026, 11, 19)) == date(2031, 11, 19)
    assert logic.retention_date(date(2024, 2, 29)) == date(2029, 2, 28)


def test_artifact_hash_is_stable_and_audience_sensitive():
    brief = _brief("B-01")
    draft, claims, _ = _evaluate(brief)
    first = logic.content_hash(brief, 1, draft, claims["claims"], "consent_received")
    second = logic.content_hash(brief, 1, draft, claims["claims"], "consent_received")
    other = logic.content_hash(brief, 1, draft, claims["claims"], "all_book")
    assert first == second
    assert first != other
    assert len(first) == 64


def test_sealed_evidence_carries_the_required_fields():
    brief = _brief("B-01")
    draft, claims, result = _evaluate(brief)
    approver = logic.find_approver("marta")
    record = logic.seal_evidence(
        brief,
        draft,
        claims["claims"],
        result["checks"],
        1,
        approver,
        "consent_received",
        "Clients with consent on file (8 households)",
        "checked",
    )
    for key in (
        "approver",
        "role",
        "approved_at",
        "version",
        "audience",
        "channel",
        "disclosures",
        "retention_until",
        "artifact_hash",
    ):
        assert record.get(key)
    assert record["role"] == "Chief Compliance Officer"
    approved_day = date.fromisoformat(record["approved_at"][:10])
    assert record["retention_until"] == logic.retention_date(approved_day).isoformat()
    assert len(record["policy_checks"]) == 7


def test_the_drafting_adviser_cannot_approve():
    person = logic.find_approver("dana")
    assert person["selectable"] is False
    assert person["role"] == "Adviser"


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


def test_eval_report_is_consistent():
    report = sup_eval.run_eval()
    assert report["cases"] == 12
    assert 0 < report["accuracy"] <= 1
    flags = report["flags"]
    assert flags["true_positive"] + flags["false_negative"] == flags["expected"]
    assert flags["true_positive"] + flags["false_positive"] == flags["actual"]
    assert report["exact"] == round(report["accuracy"] * report["cases"])
    assert len(report["failures"]) == report["cases"] - report["exact"]
    assert report["precision"] == round(
        flags["true_positive"] / (flags["true_positive"] + flags["false_positive"]), 4
    )
    assert report["recall"] == round(
        flags["true_positive"] / (flags["true_positive"] + flags["false_negative"]), 4
    )


def test_every_recording_the_page_needs_is_live():
    for key in data.recording_keys():
        result = llm.recorded(MODULE, key)
        assert result is not None, "missing recording: " + key
        assert result.mode == llm.MODE_LIVE, key


# ---------------------------------------------------------------------------
# Routes and the server-side gate
# ---------------------------------------------------------------------------


def test_page_renders_with_the_working_surface():
    state.reset(MODULE)
    response = CLIENT.get("/supervision?brief=B-01")
    assert response.status_code == 200
    body = response.text
    for text in (
        "Supervision in the loop",
        "Approved fact sheet",
        "Claim, type, support, source",
        "Policy checks",
        "Evidence file",
        "Audit",
        "How this is built",
    ):
        assert text in body


def test_blocked_approval_is_refused_by_the_route():
    state.reset(MODULE)
    response = CLIENT.post(
        "/supervision/approve",
        data={"brief": "B-04", "approver": "marta", "audience": "public"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=blocked" in response.headers["location"]
    assert state.load(MODULE, {}).get("decisions", {}) == {}


def test_adviser_self_review_is_refused_by_the_route():
    state.reset(MODULE)
    response = CLIENT.post(
        "/supervision/approve",
        data={"brief": "B-01", "approver": "dana", "audience": "consent_received"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=self_review" in response.headers["location"]


def test_all_book_audience_is_refused_by_the_route():
    state.reset(MODULE)
    response = CLIENT.post(
        "/supervision/approve",
        data={"brief": "B-02", "approver": "marta", "audience": "all_book"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=consent" in response.headers["location"]


def test_evidence_json_is_not_available_before_approval():
    state.reset(MODULE)
    response = CLIENT.get("/supervision/evidence/B-01.json")
    assert response.status_code == 404
