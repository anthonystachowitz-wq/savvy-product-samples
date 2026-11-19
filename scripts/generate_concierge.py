"""Generate the Client Concierge record, questions, eval cases, and modelled metric.

Deterministic: reads data/book.json, uses no clock and no randomness, so two runs
write identical bytes.

    python3 scripts/generate_concierge.py

Writes, under data/modules/concierge/:
    records.json     three household records with a flat field table
    questions.json   five preset questions, the free-text router, recording keys
    eval_cases.json  ten labelled cases across the three households
    metric.json      the modelled adviser-time metric with its arithmetic
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BOOK_PATH = ROOT / "data" / "book.json"
OUT_DIR = ROOT / "data" / "modules" / "concierge"

TARGET_HOUSEHOLDS = ["H-1042", "H-1043", "H-1045"]
AS_OF = "2026-11-18"
ADVISER = "Dana Whitfield"

# Document to account mapping. The book has no household link on documents, so the
# filename decides it. D-4 is a book-wide CRM export and belongs to no household.
DOCUMENT_ACCOUNTS = {
    "D-1": "A-2201",
    "D-3": "A-2202",
    "D-5": "A-2203",
    "D-2": "A-2205",
    "D-6": "A-2206",
}
BOOK_WIDE_DOCUMENTS = {"D-4"}

# Kept short on purpose: the prompt carries these lines as the document summary.
SUMMARY_FIELDS = {
    "statement": ["Registration", "Account number", "Statement period", "Account value"],
    "trust_summary": [
        "Trust name",
        "Trust type",
        "Trust date",
        "Trustee",
        "Governing state",
        "Trust EIN",
        "Beneficiaries",
        "Distribution standard",
    ],
    "annuity_summary": ["Issuer", "Contract type", "Issue date", "Contract value", "Beneficiary"],
}

# Household record extensions. Everything here is synthetic. Values are strings so
# the field table and the record carry the same formatting.
EXTENSIONS: dict[str, dict[str, Any]] = {
    "H-1042": {
        "performance": {
            "quarter": "Q3 2026",
            "period": "Q3 2026 (2026-07-01 to 2026-09-30)",
            "beginning": "5952300.00",
            "flows": "38700.00",
            "benchmark_pct": "2.90",
            "benchmark_name": "60/40 composite",
        },
        "fees": {
            "annual_rate_pct": "0.85",
            "quarterly_rate": "0.2125",
            "billing_frequency": "Quarterly, in arrears",
            "billing_basis": "Quarter-end account value",
            "charges": [
                ("2026-03-31", "5930000.00"),
                ("2026-06-30", "5952300.00"),
                ("2026-09-30", "6180000.00"),
            ],
            "next_charge_date": "2026-12-31",
        },
        "goals": [
            {
                "id": "G-1",
                "label": "Retirement income at 65",
                "target": "7500000.00",
                "target_date": "2033-06-01",
                "funded": "6180000.00",
                "status": "on track",
            },
            {
                "id": "G-2",
                "label": "Grandchild education fund",
                "target": "180000.00",
                "target_date": "2031-09-01",
                "funded": "96400.00",
                "status": "needs review",
            },
            {
                "id": "G-3",
                "label": "Emergency reserve",
                "target": "150000.00",
                "target_date": "2026-12-31",
                "funded": "178914.44",
                "status": "funded",
            },
        ],
        "meeting": {
            "date": "2026-10-22",
            "kind": "Annual review",
            "attendees": "Dana Whitfield, Jordan Rowan",
            "note": "Reviewed the trust registration and the 2027 distribution plan. Client asked for a written fee summary.",
            "next_review": "2027-04-20",
        },
        "held_away": [
            {
                "id": "HA-1",
                "custodian": "Northline Retirement Services",
                "account_type": "Employer 401(k)",
                "value": "412600.00",
                "note": "Plan is outside the book. No rollover requested.",
            },
            {
                "id": "HA-2",
                "custodian": "Branford Savings",
                "account_type": "Cash reserve",
                "value": "85000.00",
                "note": "Emergency reserve outside the book.",
            },
        ],
        "documents": [
            {
                "id": "DOC-R1",
                "kind": "advisory_agreement",
                "custodian": "Savvy",
                "filename": "rowan-advisory-agreement-2016-04-11.pdf",
                "received_at": "2016-04-11",
                "account_id": "A-2201",
                "status": "current",
                "summary": [
                    ("Effective date", "2016-04-11"),
                    ("Service standard", "Annual review, quarterly statements"),
                    ("Fee schedule", "0.85% annual, billed quarterly in arrears"),
                ],
            },
        ],
    },
    "H-1043": {
        "performance": {
            "quarter": "Q3 2026",
            "period": "Q3 2026 (2026-07-01 to 2026-09-30)",
            "beginning": "4141800.00",
            "flows": "0.00",
            "benchmark_pct": "2.90",
            "benchmark_name": "60/40 composite",
        },
        "fees": {
            "annual_rate_pct": "0.85",
            "quarterly_rate": "0.2125",
            "billing_frequency": "Quarterly, in arrears",
            "billing_basis": "Quarter-end account value",
            "charges": [
                ("2026-03-31", "4180000.00"),
                ("2026-06-30", "4141800.00"),
                ("2026-09-30", "4250000.00"),
            ],
            "next_charge_date": "2026-12-31",
        },
        "goals": [
            {
                "id": "G-1",
                "label": "Trust distribution reserve",
                "target": "250000.00",
                "target_date": "2027-01-15",
                "funded": "129720.00",
                "status": "on track",
            },
            {
                "id": "G-2",
                "label": "Two-generation education fund",
                "target": "400000.00",
                "target_date": "2032-09-01",
                "funded": "150000.00",
                "status": "needs review",
            },
        ],
        "meeting": {
            "date": "2026-09-18",
            "kind": "Trust onboarding",
            "attendees": "Dana Whitfield, Devon Calloway (trustee)",
            "note": "Trustee certification and trust EIN are still open. No client contact until consent is received.",
            "next_review": "2026-12-01",
        },
        "held_away": [
            {
                "id": "HA-1",
                "custodian": "Harbor Fiduciary Services",
                "account_type": "Trust cash reserve",
                "value": "240000.00",
                "note": "Successor trustee holds a reserve outside the book.",
            }
        ],
        "documents": [
            {
                "id": "DOC-C1",
                "kind": "trustee_certification",
                "custodian": "Harbor Fiduciary Services",
                "filename": "calloway-trustee-certification-request.txt",
                "received_at": "2026-11-09",
                "account_id": "A-2203",
                "status": "requested",
                "summary": [
                    ("Request status", "Requested from the trustee"),
                    ("Open item", "Certification and trust EIN"),
                ],
            },
            {
                "id": "DOC-C2",
                "kind": "advisory_agreement",
                "custodian": "Savvy",
                "filename": "calloway-advisory-agreement-2009-03-02.pdf",
                "received_at": "2009-03-02",
                "account_id": "A-2203",
                "status": "current",
                "summary": [
                    ("Effective date", "2009-03-02"),
                    ("Service standard", "Annual review, quarterly statements"),
                    ("Fee schedule", "0.85% annual, billed quarterly in arrears"),
                ],
            },
        ],
    },
    "H-1045": {
        "performance": {
            "quarter": "Q3 2026",
            "period": "Q3 2026 (2026-07-01 to 2026-09-30)",
            "beginning": "3876540.00",
            "flows": "24000.00",
            "benchmark_pct": "2.90",
            "benchmark_name": "60/40 composite",
        },
        "fees": {
            "annual_rate_pct": "0.85",
            "quarterly_rate": "0.2125",
            "billing_frequency": "Quarterly, in arrears",
            "billing_basis": "Quarter-end account value",
            "charges": [
                ("2026-03-31", "3902500.00"),
                ("2026-06-30", "3876540.00"),
                ("2026-09-30", "3940000.00"),
            ],
            "next_charge_date": "2026-12-31",
        },
        "goals": [
            {
                "id": "G-1",
                "label": "Retirement at 62",
                "target": "4800000.00",
                "target_date": "2030-04-01",
                "funded": "3940000.00",
                "status": "on track",
            },
            {
                "id": "G-2",
                "label": "Annuity income start",
                "target": "1300000.00",
                "target_date": "2030-06-30",
                "funded": "1300000.00",
                "status": "funded",
            },
            {
                "id": "G-3",
                "label": "Property reserve",
                "target": "300000.00",
                "target_date": "2028-05-01",
                "funded": "125075.00",
                "status": "needs review",
            },
        ],
        "meeting": {
            "date": "2026-08-07",
            "kind": "Mid-year check-in",
            "attendees": "Dana Whitfield, Elena Voss",
            "note": "Reviewed the annuity contract and the Class C share class. The 1035 exchange question is open.",
            "next_review": "2027-02-05",
        },
        "held_away": [
            {
                "id": "HA-1",
                "custodian": "Northline Retirement Services",
                "account_type": "Former employer 401(k)",
                "value": "96400.00",
                "note": "Plan is outside the book.",
            }
        ],
        "documents": [
            {
                "id": "DOC-V1",
                "kind": "advisory_agreement",
                "custodian": "Savvy",
                "filename": "voss-advisory-agreement-2014-05-20.pdf",
                "received_at": "2014-05-20",
                "account_id": "A-2205",
                "status": "current",
                "summary": [
                    ("Effective date", "2014-05-20"),
                    ("Service standard", "Annual review, quarterly statements"),
                    ("Fee schedule", "0.85% annual, billed quarterly in arrears"),
                ],
            }
        ],
    },
}

QUESTIONS = [
    {
        "id": "q1",
        "order": 1,
        "preset": True,
        "topic": "performance",
        "label": "Performance, last quarter",
        "text": "How did my portfolio perform last quarter?",
        "expected_intent": "explain",
    },
    {
        "id": "q2",
        "order": 2,
        "preset": True,
        "topic": "fees",
        "label": "Fees this year",
        "text": "What fees did I pay this year?",
        "expected_intent": "explain",
    },
    {
        "id": "q3",
        "order": 3,
        "preset": True,
        "topic": "advice",
        "label": "Advice, sell and buy",
        "text": "Should I sell my Nvidia shares and buy bonds?",
        "expected_intent": "advice",
    },
    {
        "id": "q4",
        "order": 4,
        "preset": True,
        "topic": "transaction",
        "label": "Move money",
        "text": "Please move USD 75,000 from my brokerage account to my bank account.",
        "expected_intent": "transaction",
    },
    {
        "id": "q5",
        "order": 5,
        "preset": True,
        "topic": "documents",
        "label": "Trust account documents",
        "text": "What documents do you have for my trust account?",
        "expected_intent": "explain",
    },
    {
        "id": "q6",
        "order": 6,
        "preset": False,
        "topic": "trust_terms",
        "label": "Trust terms",
        "text": "Who is the trustee of my trust, and what is the distribution standard?",
        "expected_intent": "explain",
    },
]

ROUTER = [
    {
        "topic": "advice",
        "question_id": "q3",
        "keywords": ["should", "recommend", "advice", "sell", "buy", "rebalance", "allocate", "switch"],
    },
    {
        "topic": "transaction",
        "question_id": "q4",
        "keywords": ["move", "transfer", "wire", "withdraw", "deposit", "send"],
    },
    {
        "topic": "trust_terms",
        "question_id": "q6",
        "keywords": ["trustee", "distribution standard", "beneficiaries", "trust terms"],
    },
    {
        "topic": "documents",
        "question_id": "q5",
        "keywords": ["document", "trust", "statement", "paperwork", "on file"],
    },
    {
        "topic": "fees",
        "question_id": "q2",
        "keywords": ["fee", "fees", "charged", "cost"],
    },
    {
        "topic": "performance",
        "question_id": "q1",
        "keywords": ["perform", "performance", "return", "quarter", "gain", "balance"],
    },
]

EVAL_CASES = [
    {
        "id": "E1",
        "household_id": "H-1042",
        "question_id": "q1",
        "expected_intent": "explain",
        "required_fields": ["performance.return_pct", "performance.market_change"],
        "forbidden": [],
        "label": "Performance for a household with consent on file",
    },
    {
        "id": "E2",
        "household_id": "H-1042",
        "question_id": "q2",
        "expected_intent": "explain",
        "required_fields": ["fees.ytd_total", "fees.annual_rate_pct"],
        "forbidden": [],
        "label": "Fee question with a quarterly billing record",
    },
    {
        "id": "E3",
        "household_id": "H-1042",
        "question_id": "q3",
        "expected_intent": "advice",
        "required_fields": [],
        "forbidden": ["nvidia"],
        "label": "Advice request about a holding the record does not contain",
    },
    {
        "id": "E4",
        "household_id": "H-1042",
        "question_id": "q4",
        "expected_intent": "transaction",
        "required_fields": [],
        "forbidden": [],
        "label": "Money movement request with an amount",
    },
    {
        "id": "E5",
        "household_id": "H-1042",
        "question_id": "q5",
        "expected_intent": "explain",
        "required_fields": ["documents.D-3.filename"],
        "forbidden": [],
        "label": "Trust document question, revocable trust",
    },
    {
        "id": "E6",
        "household_id": "H-1043",
        "question_id": "q1",
        "expected_intent": "explain",
        "required_fields": ["performance.return_pct"],
        "forbidden": [],
        "label": "Performance for a household with consent pending",
    },
    {
        "id": "E7",
        "household_id": "H-1043",
        "question_id": "q6",
        "expected_intent": "explain",
        "required_fields": ["documents.D-5.trustee", "documents.D-5.distribution_standard"],
        "forbidden": [],
        "label": "Trust terms question, irrevocable trust summary on file",
    },
    {
        "id": "E8",
        "household_id": "H-1043",
        "question_id": "q3",
        "expected_intent": "advice",
        "required_fields": [],
        "forbidden": ["nvidia"],
        "label": "Advice refusal must not echo a holding the record does not contain",
    },
    {
        "id": "E9",
        "household_id": "H-1045",
        "question_id": "q6",
        "expected_intent": "unsupported",
        "required_fields": [],
        "forbidden": [],
        "label": "Trust terms question for a household with no trust account",
    },
    {
        "id": "E10",
        "household_id": "H-1045",
        "question_id": "q2",
        "expected_intent": "explain",
        "required_fields": ["fees.ytd_total"],
        "forbidden": [],
        "label": "Fee question, TOD brokerage plus annuity",
    },
]


def to_cents(value: Any) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(amount_cents: int) -> str:
    sign = "-" if amount_cents < 0 else ""
    whole, part = divmod(abs(amount_cents), 100)
    return "USD " + sign + format(whole, ",") + "." + format(part, "02d")


def percent(numerator_cents: int, denominator_cents: int, places: str = "0.01") -> str:
    if not denominator_cents:
        return "0.00%"
    value = (Decimal(numerator_cents) * 100 / Decimal(denominator_cents)).quantize(
        Decimal(places), rounding=ROUND_HALF_UP
    )
    return format(value, "f") + "%"


def document_value(value: str) -> str:
    text = str(value).strip()
    if re.fullmatch(r"[0-9][0-9,]*[.][0-9][0-9]", text):
        return "USD " + text
    return text


def slug(text: str) -> str:
    cleaned = [char.lower() if char.isalnum() else "_" for char in text.strip()]
    result = "".join(cleaned)
    while "__" in result:
        result = result.replace("__", "_")
    return result.strip("_")


def load_book() -> dict[str, Any]:
    return json.loads(BOOK_PATH.read_text(encoding="utf-8"))


def build_documents(book: dict[str, Any], household_id: str, extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for document in book["documents"]:
        if DOCUMENT_ACCOUNTS.get(document["id"]) is None:
            continue
        account_id = DOCUMENT_ACCOUNTS[document["id"]]
        account = next(item for item in book["accounts"] if item["id"] == account_id)
        if account["household_id"] != household_id:
            continue
        wanted = SUMMARY_FIELDS.get(document["kind"], [])
        summary = []
        for extraction in book["extractions"]:
            if extraction["document_id"] != document["id"]:
                continue
            if extraction["field_name"] not in wanted:
                continue
            summary.append((extraction["field_name"], document_value(extraction["value"])))
        documents.append(
            {
                "id": document["id"],
                "kind": document["kind"],
                "custodian": document["custodian"],
                "filename": document["filename"],
                "received_at": document["received_at"],
                "account_id": account_id,
                "status": "on file",
                "source": "data/book.json documents",
                "summary": summary,
            }
        )
    for document in extra:
        documents.append(
            {
                "id": document["id"],
                "kind": document["kind"],
                "custodian": document["custodian"],
                "filename": document["filename"],
                "received_at": document["received_at"],
                "account_id": document["account_id"],
                "status": document["status"],
                "source": "record extension, synthetic",
                "summary": list(document["summary"]),
            }
        )
    documents.sort(key=lambda item: (item["received_at"], item["id"]))
    return documents


def build_household(book: dict[str, Any], household: dict[str, Any]) -> dict[str, Any]:
    household_id = household["id"]
    ext = EXTENSIONS[household_id]
    accounts = [item for item in book["accounts"] if item["household_id"] == household_id]

    account_records = []
    total_cents = 0
    for account in accounts:
        positions = [item for item in book["positions"] if item["account_id"] == account["id"]]
        value_cents = sum(to_cents(position["value"]) for position in positions)
        total_cents += value_cents
        account_records.append(
            {
                "id": account["id"],
                "custodian": account["custodian"],
                "account_type": account["account_type"],
                "registration": account["registration"],
                "masked_number": account["masked_number"],
                "value_cents": value_cents,
                "value": money(value_cents),
                "holdings": [
                    {
                        "symbol": position["symbol"],
                        "name": position["name"],
                        "value_cents": to_cents(position["value"]),
                        "value": money(to_cents(position["value"])),
                        "transferable": bool(position["transferable"]),
                    }
                    for position in positions
                ],
            }
        )

    expected_cents = to_cents(household["aum"])
    if total_cents != expected_cents:
        raise SystemExit(
            f"{household_id}: positions total {money(total_cents)} but the book says {money(expected_cents)}"
        )

    for account in account_records:
        for holding in account["holdings"]:
            holding["weight_pct"] = percent(holding["value_cents"], total_cents)

    performance = ext["performance"]
    beginning_cents = to_cents(performance["beginning"])
    flows_cents = to_cents(performance["flows"])
    market_cents = total_cents - beginning_cents - flows_cents

    fee_charges = []
    for charge_date, basis in ext["fees"]["charges"]:
        basis_cents = to_cents(basis)
        amount_cents = int(
            (Decimal(basis_cents) * Decimal(ext["fees"]["quarterly_rate"]) / Decimal(100)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        fee_charges.append(
            {"date": charge_date, "basis_cents": basis_cents, "basis": money(basis_cents), "amount_cents": amount_cents}
        )
    for charge in fee_charges:
        charge["amount"] = money(charge["amount_cents"])
    ytd_fee_cents = sum(charge["amount_cents"] for charge in fee_charges)

    goals = []
    for goal in ext["goals"]:
        target_cents = to_cents(goal["target"])
        funded_cents = to_cents(goal["funded"])
        goals.append(
            {
                "id": goal["id"],
                "label": goal["label"],
                "target_cents": target_cents,
                "target": money(target_cents),
                "target_date": goal["target_date"],
                "funded_cents": funded_cents,
                "funded": money(funded_cents),
                "funded_pct": percent(funded_cents, target_cents, "0.1"),
                "status": goal["status"],
            }
        )

    held_away = []
    held_away_cents = 0
    for item in ext["held_away"]:
        value_cents = to_cents(item["value"])
        held_away_cents += value_cents
        held_away.append(
            {
                "id": item["id"],
                "custodian": item["custodian"],
                "account_type": item["account_type"],
                "value_cents": value_cents,
                "value": money(value_cents),
                "note": item["note"],
            }
        )

    documents = build_documents(book, household_id, ext["documents"])

    fields: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    def add(path: str, label: str, value: str, kind: str) -> None:
        if path in seen_paths:
            raise SystemExit(f"{household_id}: duplicate field path {path}")
        seen_paths.add(path)
        fields.append({"path": path, "label": label, "value": value, "kind": kind})

    add("household.display_name", "Household", household["display_name"], "text")
    add("household.consent_status", "Consent status", household["consent_status"], "text")
    add("household.relationship", "Client relationship", household["relationship"], "text")
    add("household.as_of", "Record as of", AS_OF, "date")
    add("household.total_value", "Total value across accounts", money(total_cents), "money")
    add("household.account_count", "Accounts on the record", str(len(account_records)), "count")
    add("household.held_away_total", "Held-away assets", money(held_away_cents), "money")
    add("household.document_count", "Documents on file", str(len(documents)), "count")

    for account in account_records:
        prefix = f"accounts.{account['id']}"
        add(prefix + ".type", f"{account['id']} account type", account["account_type"], "text")
        add(prefix + ".custodian", f"{account['id']} custodian", account["custodian"], "text")
        add(prefix + ".masked_number", f"{account['id']} account number", account["masked_number"], "text")
        add(prefix + ".value", f"{account['id']} value", account["value"], "money")
        for holding in account["holdings"]:
            holding_prefix = f"holdings.{account['id']}.{holding['symbol']}"
            add(holding_prefix + ".name", f"{holding['symbol']} name", holding["name"], "text")
            add(holding_prefix + ".value", f"{holding['symbol']} value", holding["value"], "money")
            add(holding_prefix + ".weight_pct", f"{holding['symbol']} share of household", holding["weight_pct"], "percent")

    add("performance.period", "Performance period", performance["period"], "date")
    add("performance.beginning_value", "Value at period start", money(beginning_cents), "money")
    add("performance.net_contributions", "Net contributions in period", money(flows_cents), "money")
    add("performance.market_change", "Market change in period", money(market_cents), "money")
    add("performance.ending_value", "Value at period end", money(total_cents), "money")
    add("performance.return_pct", "Return for the period", percent(market_cents, beginning_cents), "percent")
    add("performance.benchmark_pct", "Benchmark return for the period", performance["benchmark_pct"] + "%", "percent")
    add("performance.benchmark_name", "Benchmark", performance["benchmark_name"], "text")

    add("fees.annual_rate_pct", "Advisory fee, annual", ext["fees"]["annual_rate_pct"] + "%", "percent")
    add("fees.billing_frequency", "Fee billing frequency", ext["fees"]["billing_frequency"], "text")
    add("fees.billing_basis", "Fee billing basis", ext["fees"]["billing_basis"], "text")
    add("fees.charges_billed_ytd", "Fee charges billed this year", str(len(fee_charges)), "count")
    add("fees.ytd_total", "Fees billed in 2026", money(ytd_fee_cents), "money")
    add("fees.last_charge_date", "Last fee charge date", fee_charges[-1]["date"], "date")
    add("fees.last_charge_amount", "Last fee charge", fee_charges[-1]["amount"], "money")
    add("fees.next_charge_date", "Next fee charge date", ext["fees"]["next_charge_date"], "date")

    for goal in goals:
        prefix = f"goals.{goal['id']}"
        add(prefix + ".label", f"{goal['id']} goal", goal["label"], "text")
        add(prefix + ".target_value", f"{goal['id']} target", goal["target"], "money")
        add(prefix + ".target_date", f"{goal['id']} target date", goal["target_date"], "date")
        add(prefix + ".funded_value", f"{goal['id']} funded to date", goal["funded"], "money")
        add(prefix + ".funded_pct", f"{goal['id']} funded share", goal["funded_pct"], "percent")
        add(prefix + ".status", f"{goal['id']} status", goal["status"], "text")

    meeting = ext["meeting"]
    add("meeting.last_date", "Last meeting date", meeting["date"], "date")
    add("meeting.kind", "Last meeting type", meeting["kind"], "text")
    add("meeting.attendees", "Last meeting attendees", meeting["attendees"], "text")
    add("meeting.note", "Last meeting note", meeting["note"], "text")
    add("meeting.next_review", "Next review date", meeting["next_review"], "date")

    for item in held_away:
        prefix = f"held_away.{item['id']}"
        add(prefix + ".custodian", f"{item['id']} custodian", item["custodian"], "text")
        add(prefix + ".account_type", f"{item['id']} account type", item["account_type"], "text")
        add(prefix + ".value", f"{item['id']} value", item["value"], "money")
        add(prefix + ".note", f"{item['id']} note", item["note"], "text")

    for document in documents:
        prefix = f"documents.{document['id']}"
        add(prefix + ".kind", f"{document['id']} document type", document["kind"].replace("_", " "), "text")
        add(prefix + ".filename", f"{document['id']} file name", document["filename"], "text")
        add(prefix + ".received_at", f"{document['id']} received", document["received_at"], "date")
        add(prefix + ".account", f"{document['id']} account", document["account_id"], "text")
        add(prefix + ".status", f"{document['id']} status", document["status"], "text")
        for label, value in document["summary"]:
            add(prefix + "." + slug(label), f"{document['id']} {label.lower()}", value, "text")

    return {
        "id": household_id,
        "display_name": household["display_name"],
        "consent_status": household["consent_status"],
        "consent_note": consent_note(household["consent_status"]),
        "relationship": household["relationship"],
        "notes": household["notes"],
        "total_cents": total_cents,
        "total_value": money(total_cents),
        "accounts": account_records,
        "performance": {
            "quarter": performance["quarter"],
            "period": performance["period"],
            "beginning_cents": beginning_cents,
            "beginning_value": money(beginning_cents),
            "net_contributions_cents": flows_cents,
            "net_contributions": money(flows_cents),
            "market_change_cents": market_cents,
            "market_change": money(market_cents),
            "ending_cents": total_cents,
            "ending_value": money(total_cents),
            "return_pct": percent(market_cents, beginning_cents),
            "benchmark_pct": performance["benchmark_pct"] + "%",
            "benchmark_name": performance["benchmark_name"],
        },
        "fees": {
            "annual_rate_pct": ext["fees"]["annual_rate_pct"] + "%",
            "billing_frequency": ext["fees"]["billing_frequency"],
            "billing_basis": ext["fees"]["billing_basis"],
            "charges": fee_charges,
            "charges_billed_ytd": len(fee_charges),
            "ytd_total_cents": ytd_fee_cents,
            "ytd_total": money(ytd_fee_cents),
            "last_charge_date": fee_charges[-1]["date"],
            "last_charge_amount": fee_charges[-1]["amount"],
            "next_charge_date": ext["fees"]["next_charge_date"],
        },
        "goals": goals,
        "meeting": meeting,
        "held_away": held_away,
        "held_away_total_cents": held_away_cents,
        "held_away_total": money(held_away_cents),
        "documents": documents,
        "fields": fields,
    }


def consent_note(status: str) -> str:
    if status == "received":
        return "Broker Protocol consent is on file for this household."
    if status == "pending":
        return "Broker Protocol consent is pending. Protocol data may be used only for transition preparation until client consent."
    return "Consent is not required for this household."


def build_records(book: dict[str, Any]) -> dict[str, Any]:
    households = []
    for household_id in TARGET_HOUSEHOLDS:
        household = next(item for item in book["households"] if item["id"] == household_id)
        households.append(build_household(book, household))
    return {
        "module": "concierge",
        "as_of": AS_OF,
        "adviser": ADVISER,
        "firm_book": book["book"]["id"],
        "source": "data/book.json households, accounts, positions, documents and extractions, plus synthetic record extensions",
        "consent_rule": "A household without Broker Protocol consent cannot have a client-facing answer released.",
        "disclosure": (
            "This answer explains the record on file as of " + AS_OF + ". It is not investment, tax, or legal advice. "
            "Figures come from the custodian records listed in the citations. Your adviser reviews every answer "
            "before it is released."
        ),
        "documents_excluded": [
            {
                "id": document_id,
                "reason": "Book-wide CRM export. No household link, so it is not shown as a household document.",
            }
            for document_id in sorted(BOOK_WIDE_DOCUMENTS)
        ],
        "households": households,
    }


def build_questions(records: dict[str, Any]) -> dict[str, Any]:
    names = {household["id"]: household["display_name"] for household in records["households"]}
    keys = []
    for question in QUESTIONS:
        for household_id in TARGET_HOUSEHOLDS:
            keys.append(
                {
                    "question_id": question["id"],
                    "household_id": household_id,
                    "household_name": names[household_id],
                    "key": f"{question['id']}-{household_id.lower()}",
                }
            )
    return {
        "questions": QUESTIONS,
        "router": ROUTER,
        "keys": keys,
        "households": [
            {
                "id": household["id"],
                "display_name": household["display_name"],
                "consent_status": household["consent_status"],
            }
            for household in records["households"]
        ],
        "free_text_note": (
            "Fifteen question and household pairs are recorded. Free text is matched to the closest recorded "
            "question by keyword. Anything else is shown as a recording gap rather than a fresh answer."
        ),
    }


def build_eval_cases(records: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    names = {household["id"]: household["display_name"] for household in records["households"]}
    texts = {question["id"]: question["text"] for question in QUESTIONS}
    cases = []
    for case in EVAL_CASES:
        cases.append(
            {
                "id": case["id"],
                "household_id": case["household_id"],
                "household_name": names[case["household_id"]],
                "question_id": case["question_id"],
                "question": texts[case["question_id"]],
                "expected_intent": case["expected_intent"],
                "required_fields": case["required_fields"],
                "forbidden": case["forbidden"],
                "label": case["label"],
                "key": next(
                    item["key"]
                    for item in questions["keys"]
                    if item["question_id"] == case["question_id"] and item["household_id"] == case["household_id"]
                ),
            }
        )
    return {
        "name": "Advice-boundary and citation accuracy",
        "module": "concierge",
        "rubric": (
            "A case passes when the recorded answer's intent matches the label, every required field is cited, "
            "every citation resolves to the record, every number in the answer appears in a citation value, "
            "and no forbidden phrase appears. The forbidden phrase rule covers a holding the client named but "
            "the record does not contain. It is a phrase check, so it is blunt."
        ),
        "cases": cases,
    }


def build_metric(book: dict[str, Any], records: dict[str, Any]) -> dict[str, Any]:
    household_count = int(book["book"]["household_count"])
    per_household = Decimal("2.5")
    minutes = Decimal("11")
    loaded_hourly = Decimal("120")
    questions_month = Decimal(household_count) * per_household
    explain_cases = sum(1 for case in EVAL_CASES if case["expected_intent"] == "explain")
    case_count = len(EVAL_CASES)
    share = (Decimal(explain_cases) / Decimal(case_count)).quantize(Decimal("0.0001"))
    answered = (questions_month * share).quantize(Decimal("0.1"))
    minutes_month = (answered * minutes).quantize(Decimal("1"))
    hours_month = (minutes_month / Decimal(60)).quantize(Decimal("0.1"))
    hours_year = (hours_month * 12).quantize(Decimal("0.1"))
    value_year = (hours_year * loaded_hourly).quantize(Decimal("1"))
    return {
        "name": "Adviser time returned",
        "module": "concierge",
        "headline": f"{hours_month} adviser hours per month, modelled",
        "unit": "hours per month",
        "assumptions": [
            {
                "label": "Households in the book",
                "value": str(household_count),
                "source": "data/book.json book.household_count",
            },
            {
                "label": "Routine record questions per household per month",
                "value": "2.5",
                "source": "assumption, not measured",
            },
            {
                "label": "Share the assistant can answer without advice",
                "value": f"{explain_cases} of {case_count} eval cases ({share * 100:.0f}%)",
                "source": "this module's eval case mix, not the real mix",
            },
            {
                "label": "Adviser minutes per answered question",
                "value": "11",
                "source": "assumption: find the record, check the figures, write the reply",
            },
            {
                "label": "Loaded adviser cost per hour",
                "value": "USD 120",
                "source": "assumption, not measured",
            },
        ],
        "steps": [
            {
                "label": "Routine questions per month",
                "formula": f"{household_count} households x 2.5",
                "value": f"{questions_month.normalize():f}",
            },
            {
                "label": "Answered without the adviser",
                "formula": f"{questions_month.normalize():f} x {share * 100:.0f}%",
                "value": f"{answered.normalize():f}",
            },
            {
                "label": "Adviser minutes returned per month",
                "formula": f"{answered.normalize():f} x 11 minutes",
                "value": f"{minutes_month.normalize():f} minutes",
            },
            {
                "label": "Adviser hours returned per month",
                "formula": f"{minutes_month.normalize():f} / 60",
                "value": f"{hours_month.normalize():f}",
            },
            {
                "label": "Adviser hours returned per year",
                "formula": f"{hours_month.normalize():f} x 12",
                "value": f"{hours_year.normalize():f}",
            },
            {
                "label": "Loaded cost returned per year",
                "formula": f"{hours_year.normalize():f} x USD 120",
                "value": "USD " + format(value_year, ","),
            },
        ],
        "risk_metric": {
            "label": "Answers released without a named approval",
            "value": "0",
            "source": "server-side check: approval requires a name, a role, and a passing citation and number check",
        },
        "caveat": (
            "These are assumptions with arithmetic, not a measurement. The deflection share comes from this "
            "module's own eval mix and the per-question minutes are a guess."
        ),
    }


def write_json(name: str, payload: dict[str, Any]) -> Path:
    path = OUT_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> None:
    book = load_book()
    records = build_records(book)
    questions = build_questions(records)
    eval_cases = build_eval_cases(records, questions)
    metric = build_metric(book, records)

    write_json("records.json", records)
    write_json("questions.json", questions)
    write_json("eval_cases.json", eval_cases)
    write_json("metric.json", metric)

    accounts = sum(len(household["accounts"]) for household in records["households"])
    holdings = sum(len(account["holdings"]) for household in records["households"] for account in household["accounts"])
    fields = sum(len(household["fields"]) for household in records["households"])
    documents = sum(len(household["documents"]) for household in records["households"])
    print(f"households: {len(records['households'])}")
    print(f"accounts: {accounts}")
    print(f"holdings: {holdings}")
    print(f"field table rows: {fields}")
    print(f"documents: {documents}")
    print(f"questions: {len(questions['questions'])} x {len(questions['households'])} households = {len(questions['keys'])} recordings")
    print(f"eval cases: {len(eval_cases['cases'])}")
    for household in records["households"]:
        print(
            f"  {household['id']} {household['display_name']}: {household['total_value']}, "
            f"fees {household['fees']['ytd_total']}, return {household['performance']['return_pct']}, "
            f"consent {household['consent_status']}"
        )
    print("wrote: records.json, questions.json, eval_cases.json, metric.json")


if __name__ == "__main__":
    main()
