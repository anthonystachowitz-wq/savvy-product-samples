"""Eval normalisation, FLAG matching, value arithmetic, and module integration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.llm import (  # noqa: E402
    BedrockProvider,
    CachedProvider,
    ConfigurationError,
    is_recorded_model_run,
    write_cached,
)
from app.pipeline.draft import draft  # noqa: E402
from app.pipeline.extract import extract_document  # noqa: E402
from app.pipeline.eval import (  # noqa: E402
    _canonical_flag,
    _normalise,
    _values_match,
    run_eval,
)
from app.pipeline.plan import build_plan  # noqa: E402
from app.pipeline.value import value_model  # noqa: E402

STATEMENT_TEXT = """ALDER CLEARING LLC
CONSOLIDATED CLIENT STATEMENT
Account Number: ****4821
Account Title: Dana R. Whitfield
Registration: Joint Tenants with Rights of Survivorship (JTWROS)
Statement Period: 10/01/2026 - 10/31/2026
Address on File:
Dana R. Whitfield
142 Maple Street
Colebrook, CT 06021
Beneficiary: Primary - Morgan Whitfield, 60%; Secondary - Ellis Whitfield, 40%

POSITIONS
Symbol  Description                              Quantity     Price       Value        Cost Basis
VTI     Vanguard Total Stock Market ETF          1,250.000    245.10      306,375.00   240,000.00
"""

TRUST_TEXT = """MORROW TRUST
TRUST ACCOUNT STATEMENT
Account Number: ****7788
Registration: Irrevocable Trust
Beneficiary: None designated
Statement Period: 10/01/2026 through 10/31/2026
"""


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_statement(base: Path, identifier: str, text: str) -> None:
    _write(base / "documents" / f"{identifier}.json", {"id": identifier, "kind": "statement", "text": text})


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_normalise_lowercases_trims_and_collapses_whitespace():
    assert _normalise("  Joint   Tenants  ") == "joint tenants"


def test_normalise_strips_currency_symbols_and_commas():
    assert _normalise("USD 42,300,000") == "42300000"
    assert _normalise("$4,200,000.00") == "4200000.00"


def test_values_match_amounts_with_different_formatting():
    assert _values_match("$306,375.00", 306375.0)
    assert _values_match("1,250.000", 1250)


def test_values_match_masked_account_number_to_digits():
    assert _values_match("****4821", "4821")
    assert not _values_match("****4821", "9921")


def test_values_match_date_ranges_written_differently():
    assert _values_match(
        "October 1, 2026 through October 31, 2026",
        "10/01/2026 to 10/31/2026",
    )


def test_values_match_iso_dates():
    assert _values_match("2026-10-01", "October 1, 2026")


def test_flag_category_is_normalised():
    assert _canonical_flag("FLAG: beneficiary_missing") == "beneficiary_missing"
    assert _canonical_flag("Missing beneficiary") == "beneficiary_missing"


# ---------------------------------------------------------------------------
# run_eval end to end
# ---------------------------------------------------------------------------


def test_run_eval_scores_a_perfect_label_set(tmp_path):
    _write_statement(tmp_path, "alder-01", STATEMENT_TEXT)
    _write(
        tmp_path / "eval" / "labeled.json",
        {
            "documents": [
                {
                    "id": "alder-01",
                    "fields": {
                        "registration": "Joint Tenants with Rights of Survivorship (JTWROS)",
                        "account_number": "****4821",
                        "statement_period": "October 1, 2026 through October 31, 2026",
                        "address": "142 Maple Street, Colebrook, CT 06021",
                        "beneficiary": (
                            "Primary - Morgan Whitfield, 60%; Secondary - Ellis Whitfield, 40%"
                        ),
                        "positions": [
                            {
                                "symbol": "VTI",
                                "quantity": "1,250.000",
                                "price": 245.10,
                                "value": 306375.0,
                                "cost_basis": 240000.0,
                            }
                        ],
                    },
                    "flags": [],
                }
            ]
        },
    )

    report = run_eval(data_dir_path=tmp_path)

    assert report["field_count"] == 10
    assert report["correct_count"] == 10
    assert report["accuracy"] == 1.0
    assert report["failures"] == []
    assert (tmp_path / "eval" / "report.json").is_file()


def test_run_eval_matches_flag_prefix_as_a_category(tmp_path):
    _write_statement(tmp_path, "morrow-01", TRUST_TEXT)
    _write(
        tmp_path / "eval" / "labeled.json",
        {
            "documents": [
                {
                    "id": "morrow-01",
                    "fields": {
                        "registration": "Irrevocable Trust",
                        "beneficiary": "FLAG: beneficiary_missing",
                    },
                }
            ]
        },
    )

    report = run_eval(data_dir_path=tmp_path)

    assert report["accuracy"] == 1.0
    assert report["field_count"] == 2
    assert any(row["field"].startswith("FLAG:") for row in report["per_field"])


def test_run_eval_reports_a_mismatch(tmp_path):
    _write_statement(tmp_path, "alder-01", STATEMENT_TEXT)
    _write(
        tmp_path / "eval" / "labeled.json",
        {
            "labels": {
                "alder-01": {
                    "registration": "Individual",
                    "account_number": "****4821",
                }
            }
        },
    )

    report = run_eval(data_dir_path=tmp_path)

    assert report["field_count"] == 2
    assert report["correct_count"] == 1
    assert report["accuracy"] == 0.5
    assert len(report["failures"]) == 1
    assert report["failures"][0]["field"] == "registration"


def test_run_eval_without_labels_returns_zero_and_a_note(tmp_path):
    report = run_eval(data_dir_path=tmp_path)
    assert report["field_count"] == 0
    assert report["accuracy"] == 0.0
    assert report["notes"]
    assert (tmp_path / "eval" / "report.json").is_file()


# ---------------------------------------------------------------------------
# Value model integration (the arithmetic is covered in tests/test_value.py)
# ---------------------------------------------------------------------------


def test_value_model_defaults_stay_wired_for_existing_callers():
    model = value_model({}, None)
    assert model["model"] == "account-based"
    assert model["baseline_hours"] == 435.0
    assert model["hours_returned_low"] == 109.0
    assert model["hours_returned_high"] == 152.0
    # Legacy key: the single assisted figure is the midpoint of the assisted range.
    assert model["assisted_hours"] == 304.5


# ---------------------------------------------------------------------------
# Plan and draft integration
# ---------------------------------------------------------------------------

HARD_CASES = (
    "irrevocable trust",
    "inherited IRA",
    "variable annuity 1035",
    "held-away 401k",
    "proprietary fund share class",
    "LP interest",
    "UTMA",
    "margin with options",
)


def _book() -> dict:
    accounts = [
        {
            "id": f"acct-{index:02d}",
            "household_id": "hh-1",
            "custodian": "Alder Clearing" if index % 2 else "Morrow Trust",
            "account_type": hard_case,
            "registration": hard_case,
            "masked_number": f"****{1000 + index}",
            "hard_case": hard_case,
        }
        for index, hard_case in enumerate(HARD_CASES, start=1)
    ]
    accounts.append(
        {
            "id": "acct-09",
            "household_id": "hh-1",
            "custodian": "Alder Clearing",
            "account_type": "Brokerage",
            "registration": "JTWROS",
            "masked_number": "****9009",
        }
    )
    return {
        "book": {
            "advisor_name": "Dana Whitfield",
            "firm_to": "Savvy (synthetic)",
            "transition_lead": "Priya Raman",
            "signed_at": "2026-11-01",
            "target_live_at": "2026-12-16",
            "aum_total": 42300000,
        },
        "households": [{"id": "hh-1", "display_name": "The Whitfield Household", "aum": 4200000}],
        "accounts": accounts,
    }


def test_plan_covers_every_hard_case_with_tasks_and_risks():
    plan = build_plan(_book())
    assert plan["summary"]["accounts"] == 9
    assert len(plan["critical_path"]) == 8
    assert plan["summary"]["high_risks"] >= 8
    titles = " ".join(task["title"] for task in plan["tasks"]).lower()
    assert "trustee certification" in titles
    assert "1035" in titles
    assert "rollover election" in titles
    assert "assignment" in titles
    assert "option transfer approval" in titles
    assert all(risk["rule_ref"].endswith("(synthetic)") for risk in plan["risks"])
    assert all(risk["clears_with"] for risk in plan["risks"])


def test_drafts_meet_word_limits_and_pass_guardrails():
    book = _book()
    for kind, low, high in (
        ("client_update", 90, 160),
        ("custodian_cover", 60, 120),
        ("internal_digest", 60, 120),
    ):
        payload = draft(kind, "hh-1", book)
        assert low <= payload["word_count"] <= high, (kind, payload["word_count"])
        assert payload["blocked"] == [], (kind, payload["blocked"])
        assert all(check["result"] == "pass" for check in payload["policy_checks"]), kind
        assert payload["claims"]


# ---------------------------------------------------------------------------
# Provider selection and provenance
# ---------------------------------------------------------------------------


def test_cached_provider_replays_text_and_mode(tmp_path):
    write_cached(
        "extract-demo",
        '{"fields": [], "flags": []}',
        "live model, recorded",
        directory=tmp_path,
    )
    provider = CachedProvider("extract-demo", directory=tmp_path)
    assert provider.complete("system", "prompt") == '{"fields": [], "flags": []}'
    assert provider.last_mode == "live model, recorded"


def test_deterministic_mode_is_not_a_model_run():
    assert is_recorded_model_run("deterministic extractor") is False
    assert is_recorded_model_run("live model, recorded") is True


def test_bedrock_provider_raises_a_clear_configuration_error(monkeypatch):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    with pytest.raises(ConfigurationError) as error:
        BedrockProvider()
    assert "BEDROCK_MODEL_ID" in str(error.value)
    assert "AWS_REGION" in str(error.value)


def test_bedrock_provider_uses_converse(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    class FakeClient:
        def __init__(self):
            self.kwargs = None

        def converse(self, **kwargs):
            self.kwargs = kwargs
            return {"output": {"message": {"content": [{"text": "hello"}]}}}

    client = FakeClient()
    provider = BedrockProvider(client=client)
    assert provider.complete("system prompt", "user prompt") == "hello"
    assert client.kwargs["modelId"] == "test-model"
    assert client.kwargs["system"] == [{"text": "system prompt"}]
    assert client.kwargs["messages"][0]["content"][0]["text"] == "user prompt"


# ---------------------------------------------------------------------------
# Extractor provenance: only a recorded model run may override the parser
# ---------------------------------------------------------------------------


def _sentinel_payload() -> str:
    return json.dumps(
        {
            "fields": [
                {
                    "name": "from_model_cache",
                    "value": "model",
                    "confidence": 0.91,
                    "source_span": "",
                }
            ],
            "flags": [],
        }
    )


def test_extractor_prefers_a_recorded_model_run(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVVY_CACHE_DIR", str(tmp_path))
    write_cached(
        "extract-D-99",
        _sentinel_payload(),
        "live model, recorded",
        directory=tmp_path,
    )
    result = extract_document({"id": "D-99", "text": STATEMENT_TEXT})
    assert result["method"] == "live model, recorded (cached)"
    assert any(field["name"] == "from_model_cache" for field in result["fields"])


def test_extractor_ignores_a_deterministic_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SAVVY_CACHE_DIR", str(tmp_path))
    write_cached(
        "extract-D-99",
        _sentinel_payload(),
        "deterministic extractor",
        directory=tmp_path,
    )
    result = extract_document({"id": "D-99", "text": STATEMENT_TEXT})
    assert result["method"] == "deterministic extractor"
    assert not any(field["name"] == "from_model_cache" for field in result["fields"])


def test_run_eval_accepts_flat_label_rows(tmp_path):
    _write_statement(tmp_path, "alder-01", STATEMENT_TEXT)
    _write(
        tmp_path / "eval" / "labeled.json",
        [
            {
                "document_id": "alder-01",
                "field_name": "Registration",
                "expected": "Joint Tenants with Rights of Survivorship (JTWROS)",
                "source_span": "Registration: Joint Tenants with Rights of Survivorship (JTWROS)",
            },
            {
                "document_id": "alder-01",
                "field_name": "Account number",
                "expected": "****4821",
                "source_span": "Account Number: ****4821",
            },
            {
                "document_id": "alder-01",
                "field_name": "FLAG: beneficiary missing",
                "expected": "FLAG: no beneficiary",
                "source_span": "Beneficiary: Morgan Whitfield",
            },
        ],
    )

    report = run_eval(data_dir_path=tmp_path)

    assert report["field_count"] == 3
    assert report["accuracy"] == 0.6667
    assert len(report["failures"]) == 1
    assert report["failures"][0]["field"] == "FLAG: beneficiary_missing"
