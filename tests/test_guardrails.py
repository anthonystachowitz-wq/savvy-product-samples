"""Positive and negative cases for each named guardrail check."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline.guardrails import CHECKS, check_draft  # noqa: E402

DISCLOSURE = (
    "This note is for explanation only and it is not investment advice. "
    "Your advisor can answer questions."
)


def _draft(body: str, claims=None, kind: str = "client_update") -> dict:
    return {"kind": kind, "body": body, "claims": claims or []}


def _results(draft: dict) -> dict:
    return {check["check"]: check for check in check_draft(draft)}


def test_returns_all_named_checks_in_order():
    checks = check_draft(_draft("A plain note with no numbers."))
    assert [check["check"] for check in checks] == list(CHECKS)
    assert all(check["result"] in {"pass", "warn", "fail"} for check in checks)
    assert all(check["detail"] for check in checks)


# ---------------------------------------------------------------------------
# advice_language
# ---------------------------------------------------------------------------


def test_advice_language_fails_on_recommendation():
    result = _results(_draft("We recommend selling the position."))["advice_language"]
    assert result["result"] == "fail"
    assert "recommend" in result["detail"].lower()


def test_advice_language_fails_on_transaction_verb():
    result = _results(_draft("You should buy the fund before the close."))["advice_language"]
    assert result["result"] == "fail"


def test_advice_language_fails_on_guarantee():
    result = _results(_draft("This allocation is guaranteed to protect the account."))[
        "advice_language"
    ]
    assert result["result"] == "fail"


def test_advice_language_passes_on_explanation():
    body = (
        "This note explains what the statement shows. It does not recommend any change "
        "to how your accounts are invested. " + DISCLOSURE
    )
    assert _results(_draft(body))["advice_language"]["result"] == "pass"


# ---------------------------------------------------------------------------
# promised_timing
# ---------------------------------------------------------------------------


def test_promised_timing_fails_on_service_window():
    result = _results(_draft("The transfer will be completed within 5 business days."))[
        "promised_timing"
    ]
    assert result["result"] == "fail"


def test_promised_timing_fails_on_deadline():
    result = _results(_draft("The account will be funded by next Friday."))["promised_timing"]
    assert result["result"] == "fail"


def test_promised_timing_passes_when_timeline_is_not_promised():
    body = (
        "The receiving custodian controls the review timeline. "
        "No completion date is set in this note. " + DISCLOSURE
    )
    assert _results(_draft(body))["promised_timing"]["result"] == "pass"


# ---------------------------------------------------------------------------
# performance_claim
# ---------------------------------------------------------------------------


def test_performance_claim_fails_on_return():
    result = _results(_draft("The account returned 8.4% in 2025."))["performance_claim"]
    assert result["result"] == "fail"


def test_performance_claim_fails_on_relative_performance():
    result = _results(_draft("The portfolio outperformed the benchmark."))["performance_claim"]
    assert result["result"] == "fail"


def test_performance_claim_passes_on_holdings_explanation():
    body = "The statement lists the positions and their market value. " + DISCLOSURE
    assert _results(_draft(body))["performance_claim"]["result"] == "pass"


# ---------------------------------------------------------------------------
# testimonial
# ---------------------------------------------------------------------------


def test_testimonial_fails_on_client_quote():
    result = _results(_draft("Our clients love the service."))["testimonial"]
    assert result["result"] == "fail"


def test_testimonial_fails_on_rating():
    result = _results(_draft("The firm is a five-star rated advisor."))["testimonial"]
    assert result["result"] == "fail"


def test_testimonial_passes_on_plain_status_update():
    body = "The statement lists the positions in the account. " + DISCLOSURE
    assert _results(_draft(body))["testimonial"]["result"] == "pass"


# ---------------------------------------------------------------------------
# uncited_number
# ---------------------------------------------------------------------------


def test_uncited_number_fails_with_no_claims():
    result = _results(_draft("The book has 17 accounts."))["uncited_number"]
    assert result["result"] == "fail"
    assert "17" in result["detail"]


def test_uncited_number_passes_with_cited_claim():
    claims = [
        {
            "text": "17 accounts",
            "source_type": "book",
            "source_ref": "book.accounts",
            "status": "cited",
        }
    ]
    result = _results(_draft("The book has 17 accounts.", claims))["uncited_number"]
    assert result["result"] == "pass"


def test_uncited_number_fails_when_claim_has_no_source():
    claims = [
        {"text": "17 accounts", "source_type": "book", "source_ref": "", "status": "cited"}
    ]
    result = _results(_draft("The book has 17 accounts.", claims))["uncited_number"]
    assert result["result"] == "fail"


def test_uncited_number_does_not_match_inside_another_number():
    claims = [
        {
            "text": "148219",
            "source_type": "book",
            "source_ref": "book.accounts",
            "status": "cited",
        }
    ]
    result = _results(_draft("The account ends 4821.", claims))["uncited_number"]
    assert result["result"] == "fail"


def test_uncited_number_passes_when_body_has_no_numbers():
    result = _results(_draft("No amounts appear here. " + DISCLOSURE))["uncited_number"]
    assert result["result"] == "pass"


# ---------------------------------------------------------------------------
# missing_disclosure
# ---------------------------------------------------------------------------


def test_missing_disclosure_fails_for_client_note():
    result = _results(_draft("The statement lists two positions."))["missing_disclosure"]
    assert result["result"] == "fail"


def test_missing_disclosure_warns_when_advisor_route_is_absent():
    body = "This note is for explanation only and is not investment advice."
    result = _results(_draft(body))["missing_disclosure"]
    assert result["result"] == "warn"


def test_missing_disclosure_passes_for_client_note():
    result = _results(_draft("This note explains the account. " + DISCLOSURE))[
        "missing_disclosure"
    ]
    assert result["result"] == "pass"


def test_missing_disclosure_fails_for_custodian_cover():
    draft = _draft("Enclosed are the transfer forms.", kind="custodian_cover")
    result = _results(draft)["missing_disclosure"]
    assert result["result"] == "fail"


def test_missing_disclosure_passes_for_processing_only_cover():
    body = "Enclosed are the transfer forms. This letter is for transfer processing only."
    draft = _draft(body, kind="custodian_cover")
    assert _results(draft)["missing_disclosure"]["result"] == "pass"


def test_missing_disclosure_warns_for_unmarked_internal_digest():
    draft = _draft("Twelve accounts are open.", kind="internal_digest")
    assert _results(draft)["missing_disclosure"]["result"] == "warn"


def test_missing_disclosure_passes_for_marked_internal_digest():
    draft = _draft("Internal use only. Twelve accounts are open.", kind="internal_digest")
    assert _results(draft)["missing_disclosure"]["result"] == "pass"


# ---------------------------------------------------------------------------
# A clean draft passes every check
# ---------------------------------------------------------------------------


def test_clean_client_draft_passes_every_check():
    body = (
        "This note explains the accounts received for the household. "
        "It is not investment advice and it does not recommend any change. "
        "Your advisor can answer questions about the documents."
    )
    checks = check_draft(_draft(body))
    assert [check["result"] for check in checks] == ["pass"] * len(CHECKS)
