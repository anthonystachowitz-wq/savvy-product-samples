"""Tests for the LP Ops sample. No model calls and no network."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.modules.lpo import data, logic

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "modules" / "lpo"


def _records() -> dict:
    return json.loads((DATA / "lp_records.json").read_text(encoding="utf-8"))


def _packets() -> list[dict]:
    return json.loads((DATA / "packets.json").read_text(encoding="utf-8"))["packets"]


def test_generated_files_exist() -> None:
    for name in ("lp_records.json", "packets.json", "eval_cases.json"):
        assert (DATA / name).is_file(), name


def test_lp_arithmetic() -> None:
    records = _records()
    lps = records["lps"]
    assert len(lps) == 6
    assert sum(item["commitment"] for item in lps) == 6_500_000
    assert sum(item["called"] for item in lps) == 1_950_000
    assert sum(item["funded"] for item in lps) == 1_695_000
    assert sum(item["outstanding"] for item in lps) == 255_000
    assert records["summary"]["unfunded"] == 4_550_000
    for lp in lps:
        assert lp["called"] == round(lp["commitment"] * 0.30)
        assert lp["funded"] <= lp["called"]
        assert lp["outstanding"] == lp["called"] - lp["funded"]
    assert records["stage_counts"]["all"] == 6
    assert records["stage_counts"]["funded"] == 2


def test_five_packets_and_mapping() -> None:
    packets = _packets()
    assert len(packets) == 5
    assert {packet["id"] for packet in packets} == {"P-01", "P-02", "P-03", "P-04", "P-05"}
    assert {packet["lp_id"] for packet in packets} == {"LP-01", "LP-02", "LP-03", "LP-04", "LP-05"}
    for packet in packets:
        assert packet["sha256"] == hashlib.sha256(packet["text"].encode("utf-8")).hexdigest()
        assert len(packet["sha_short"]) == 12
        assert set(packet["expected_fields"]) == set(logic.FIELD_KEYS)
        assert packet["expected_exceptions"] == sorted(
            {item["kind"] for item in packet["known_exceptions"]}
        )


def test_seeded_issues_are_present() -> None:
    packets = {packet["id"]: packet for packet in _packets()}
    assert packets["P-01"]["expected_exceptions"] == []
    assert packets["P-02"]["expected_fields"]["signature_present"] == "No"
    assert packets["P-02"]["expected_fields"]["tax_id_present"] == "No"
    assert packets["P-02"]["expected_exceptions"] == ["missing_kyc", "missing_signature"]
    assert packets["P-03"]["expected_fields"]["wire_instructions"] == "International wire"
    assert packets["P-03"]["expected_exceptions"] == ["bank_name_mismatch"]
    assert packets["P-04"]["expected_fields"]["commitment_amount"] == 975_000
    assert packets["P-04"]["expected_fields"]["closing_date"] < packets["P-04"]["expected_fields"]["subscription_date"]
    assert packets["P-04"]["expected_exceptions"] == ["commitment_mismatch", "date_anomaly"]
    assert packets["P-05"]["expected_fields"]["signature_present"] == "No"
    assert packets["P-05"]["expected_fields"]["qualified_status"] == "Not stated"
    assert packets["P-05"]["expected_fields"]["bank_details_present"] == "No"
    assert packets["P-05"]["expected_exceptions"] == ["missing_signature"]


def test_eval_cases_cover_fields_and_severities() -> None:
    payload = json.loads((DATA / "eval_cases.json").read_text(encoding="utf-8"))
    assert payload["name"] == "Subscription packet extraction"
    assert len(payload["cases"]) == 5
    for case in payload["cases"]:
        assert set(case["expected"]) == set(logic.FIELD_KEYS)
        assert len(case["expected"]) == 12
        assert len(case["expected_exception_details"]) == len(case["expected_exceptions"])
        for item in case["expected_exception_details"]:
            assert item["severity"] in {"high", "medium", "low"}


def test_calendar_and_next_event() -> None:
    records = _records()
    calendar = records["calendar"]
    assert len(calendar) == 6
    assert [item["date"] for item in calendar] == sorted(item["date"] for item in calendar)
    next_event = next(item for item in calendar if item["is_next"])
    assert next_event["id"] == "C-02"
    assert next_event["outstanding"] == 255_000
    assert next_event["amount"] == 650_000
    assert records["summary"]["next_event_id"] == "C-02"
    assert sum(1 for item in calendar if item["kind"] == "distribution") == 2


def test_metric_arithmetic() -> None:
    metric = _records()["metric"]
    assert metric["baseline_hours"] == 15.0
    assert metric["per_lp_hours"] == 5.25
    assert metric["current_hours"] == 31.5
    assert metric["target_hours"] == 315.0
    assert metric["target_fte"] == 0.17
    assert sum(line["hours"] for line in metric["task_lines"]) == 15.0


def test_normalisers() -> None:
    assert logic.normalize_money("USD 1,500,000.00") == 1_500_000
    assert logic.normalize_money("$975,000") == 975_000
    assert logic.normalize_date("28 October 2026") == "2026-10-28"
    assert logic.normalize_date("11/06/2026") == "2026-11-06"
    assert logic.normalize_yesno("present") == "Yes"
    assert logic.normalize_yesno("blank") == "No"
    assert logic.normalize_qualified("The trust is a qualified purchaser") == "Qualified purchaser"
    assert logic.normalize_qualified("not completed") == "Not stated"
    assert logic.normalize_wire("international wire") == "International wire"
    assert logic.normalize_wire("to follow") == "Not stated"
    assert logic.field_equal("lp_name", "Calloway Family Irrevocable Trust", "calloway family irrevocable trust")
    assert not logic.field_equal("commitment_amount", 1_000_000, 975_000)
    assert not logic.field_equal("signature_present", "No", logic.MISSING)


def test_evidence_spans_point_at_the_right_lines() -> None:
    packets = {packet["id"]: packet for packet in _packets()}
    p04 = packets["P-04"]
    closing = logic.locate_evidence(p04["text"], "closing_date", p04["expected_fields"]["closing_date"])
    assert closing is not None and "14 October 2026" in closing["text"]
    p02 = packets["P-02"]
    signature = logic.locate_evidence(p02["text"], "signature_present", "No")
    assert signature is not None and "Signature:" in signature["text"]
    p03 = packets["P-03"]
    custodian = logic.locate_evidence(p03["text"], "custodian", p03["expected_fields"]["custodian"])
    assert custodian is not None and "Morrow Trust" in custodian["text"]


def test_exception_merge_reports_matches_misses_and_extras() -> None:
    packets = {packet["id"]: packet for packet in _packets()}
    p04 = packets["P-04"]
    empty = logic.merge_exceptions(p04, {"exceptions": []}, set())
    assert empty["known_total"] == 2
    assert empty["matched"] == 0
    assert empty["missed"] == 2
    model = {
        "exceptions": [
            {"kind": "commitment_mismatch", "severity": "high", "detail": "commitment differs"},
            {"kind": "wire_issue", "severity": "low", "detail": "new flag"},
        ]
    }
    merged = logic.merge_exceptions(p04, model, set())
    assert merged["matched"] == 1
    assert merged["extra"] == 1
    assert any(row["origin"] == "model" for row in merged["rows"])
    resolved = logic.merge_exceptions(p04, model, {"commitment_mismatch"})
    approved = [row for row in resolved["rows"] if row.get("kind") == "commitment_mismatch"][0]
    assert approved["status"] == "approved"


def test_stage_and_actor_rules() -> None:
    assert logic.stage_next("document_intake") == "exception_review"
    assert logic.stage_next("exception_review") == "ready_to_fund"
    assert logic.stage_next("ready_to_fund") is None
    assert logic.stage_next("funded") is None
    team = data.team()
    assert logic.parse_actor("Priya Raman|Operations lead", team) == ("Priya Raman", "Operations lead")
    assert logic.parse_actor("Someone Else|Operations lead", team) == ("", "")
    assert logic.parse_actor("Priya Raman|Adviser of record", team) == ("", "")


def test_household_consent_reads_the_book() -> None:
    assert data.household_consent("H-1043") == "pending"
    assert data.household_consent("H-1042") == "received"
    assert data.household_consent("H-9999") == "unknown"


def test_recorded_extractions_parse() -> None:
    for packet in _packets():
        key = logic.extraction_key(packet["id"])
        path = data.CACHE_DIR / f"{key}.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        parsed = logic.parse_extraction(payload["text"])
        assert parsed["ok"], parsed["error"]
        assert set(parsed["fields"]) == set(logic.FIELD_KEYS)
        assert isinstance(parsed["exceptions"], list)


def test_route_renders() -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get("/lpo?packet=P-02")
    assert response.status_code == 200
    body = response.text
    assert "LP Ops" in body
    assert "Calloway Family Irrevocable Trust" in body
    assert "Capital calls and distributions" in body
    assert "Synthetic data only" in body
