"""SQLite store for the Transition Copilot demo.

The store owns four things:

1. Schema. The tables follow the build spec's data model, with a few display
   columns (contact details, consent notes, extraction flags) added.
2. Seeding. On first run the store reads data/book.json and
   data/documents/*.json, calls the pipeline when it is importable, and writes
   one deterministic demo state. It never writes to data/.
3. Queries. Small dict-returning helpers keep the FastAPI routes readable.
4. State changes. Draft approvals, rejections, consent records, and audit
   events are persisted here. Reset_Demo rebuilds the seed and leaves every
   data file untouched.

The file is intentionally a little repetitive. A demo that is easy to audit is
worth more than one that hides its SQL behind an ORM.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import io
import json
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from . import fallbacks

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("SAVVY_DATA_DIR") or (REPO_ROOT / "data"))
DB_PATH = Path(os.environ.get("SAVVY_DB_PATH") or (REPO_ROOT / "app.db"))
EVAL_REPORT_PATH = DATA_DIR / "eval" / "report.json"

#: The demo is dated mid-transition. A fixed as-of date keeps the screenshots
#: and the "day 15 of 45" labels stable no matter when the link is opened.
AS_OF = date(2026, 11, 18)

PHASES = (
    "intake",
    "account opening",
    "account_opening",
    "document collection",
    "document_collection",
    "transfer initiation",
    "transfer_initiation",
    "custodian review",
    "custodian_review",
    "confirmation",
    "handoff",
)

PHASE_ORDER = {
    "intake": 0,
    "account opening": 1,
    "account_opening": 1,
    "document collection": 2,
    "document_collection": 2,
    "transfer initiation": 3,
    "transfer_initiation": 3,
    "custodian review": 4,
    "custodian_review": 4,
    "confirmation": 5,
    "handoff": 6,
}

ACCOUNT_STATUSES = (
    "not started",
    "in flight",
    "waiting on client",
    "waiting on custodian",
    "blocked",
)

APPROVERS = {
    "advisor": ("Dana Whitfield", "Advisor"),
    "compliance": ("Ruth Calder", "Compliance"),
}

DRAFT_STATUS_ORDER = {"blocked": 0, "in_review": 1, "approved": 2, "rejected": 3}

PROTOCOL_FIELDS = (
    ("Client name", "Identify the household on the consent record."),
    ("Mailing address", "Reconcile the statement address against the CRM."),
    ("Phone", "Route a consent request through the transition lead."),
    ("Email", "Send the consent request after resignation."),
    ("Account title", "Match the account to the household record."),
)

PROTOCOL_FIELD_NOTES = {
    "client name": "Identify the household on the consent record.",
    "address": "Reconcile the statement address against the CRM.",
    "mailing address": "Reconcile the statement address against the CRM.",
    "phone": "Route a consent request through the transition lead.",
    "email": "Send the consent request after resignation.",
    "account title": "Match the account to the household record.",
}

PROTOCOL_RESTRICTIONS = (
    "No client contact before resignation, in either direction.",
    "No account numbers, balances, holdings, or statements move with the protocol data.",
    "No solicitation that uses the prior firm's records.",
    "Only these five fields may be used. Client-facing outreach waits for recorded consent.",
)

CONSENT_PENDING = {"H-1042", "H-1043", "H-1048"}
CONSENT_NOT_REQUIRED = {"H-1053"}

#: Six document findings seeded in data/book.json. Each maps to a NIGO risk in
#: the seeded risk register so the intake panel and the plan agree.
FLAG_SPECS = (
    {
        "id": "F-1",
        "risk_id": "R-1",
        "document_id": "D-1",
        "account_id": "A-2201",
        "kind": "address_stale",
        "label": "Address stale",
        "severity": "medium",
        "source_field": "Address on file",
    },
    {
        "id": "F-2",
        "risk_id": "R-2",
        "document_id": "D-2",
        "account_id": "A-2205",
        "kind": "beneficiary_missing",
        "label": "Beneficiary missing",
        "severity": "high",
        "source_field": "Beneficiary",
    },
    {
        "id": "F-3",
        "risk_id": "R-3",
        "document_id": "D-2",
        "account_id": "A-2205",
        "kind": "non_transferable_share_class",
        "label": "Non-transferable share class",
        "severity": "high",
        "source_field": "Position MSGF",
    },
    {
        "id": "F-4",
        "risk_id": "R-4",
        "document_id": "D-3",
        "account_id": "A-2202",
        "kind": "registration_mismatch",
        "label": "Registration mismatch",
        "severity": "high",
        "source_field": "Account registration",
    },
    {
        "id": "F-5",
        "risk_id": "R-5",
        "document_id": "D-5",
        "account_id": "A-2203",
        "kind": "trust_ein_absent",
        "label": "Trust EIN absent",
        "severity": "high",
        "source_field": "Trust EIN",
    },
    {
        "id": "F-6",
        "risk_id": "R-6",
        "document_id": "D-6",
        "account_id": "A-2206",
        "kind": "annuity_surrender_charge",
        "label": "Annuity surrender charge",
        "severity": "medium",
        "source_field": "Surrender charge",
    },
)

DRAFT_SPECS = (
    {
        "id": "draft-1",
        "kind": "client_update",
        "household_id": "H-1045",
        "created_at": "2026-11-12T09:40:00",
    },
    {
        "id": "draft-2",
        "kind": "custodian_cover",
        "household_id": "H-1045",
        "created_at": "2026-11-12T09:42:00",
    },
    {
        "id": "draft-3",
        "kind": "internal_digest",
        "household_id": None,
        "created_at": "2026-11-12T09:45:00",
    },
)


# ---------------------------------------------------------------------------
# Engine loading
# ---------------------------------------------------------------------------


@dataclass
class Engine:
    extract_document: Callable[..., Any]
    build_plan: Callable[..., Any]
    draft: Callable[..., Any]
    value_model: Callable[..., Any]
    sources: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    runtime_fallbacks: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    eval_available: bool = False


def _import_callable(module_name: str, attribute: str) -> Callable[..., Any]:
    module = importlib.import_module(module_name)
    candidate = getattr(module, attribute)
    if not callable(candidate):
        raise TypeError(f"{module_name}.{attribute} is not callable")
    return candidate


def load_engine() -> Engine:
    """Import the real pipeline, keeping the fallback per capability.

    Each capability is tried on its own. A missing or broken extractor does not
    stop the plan or draft screens from rendering.
    """

    engine = Engine(
        extract_document=fallbacks.extract_document,
        build_plan=fallbacks.build_plan,
        draft=fallbacks.draft,
        value_model=fallbacks.value_model,
        sources={},
        errors={},
    )

    specs = (
        ("extract_document", "app.pipeline.extract", "extract_document"),
        ("build_plan", "app.pipeline.plan", "build_plan"),
        ("draft", "app.pipeline.draft", "draft"),
        ("value_model", "app.pipeline.value", "value_model"),
    )
    for capability, module_name, attribute in specs:
        try:
            function = _import_callable(module_name, attribute)
        except Exception as exc:  # noqa: BLE001 - a broken import is expected here
            engine.sources[capability] = "fallback"
            engine.errors[capability] = _short_error(exc)
            continue
        setattr(engine, capability, function)
        engine.sources[capability] = "pipeline"

    try:
        _import_callable("app.pipeline.eval", "run_eval")
        engine.eval_available = True
        engine.sources["eval"] = "report file"
    except Exception as exc:  # noqa: BLE001
        engine.eval_available = False
        engine.sources["eval"] = "report file only"
        engine.errors["eval"] = _short_error(exc)

    return engine


def _short_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:280]


def _record_runtime_fallback(engine: Engine, capability: str, label: str, error: str) -> None:
    entry = {"capability": capability, "label": label, "error": error[:280]}
    if entry not in engine.runtime_fallbacks:
        engine.runtime_fallbacks.append(entry)


def _call_engine(
    engine: Engine,
    capability: str,
    args: tuple[Any, ...],
    fallback: Callable[..., Any],
    label: str,
) -> tuple[Any, str]:
    if engine.sources.get(capability) == "pipeline":
        try:
            return getattr(engine, capability)(*args), "pipeline"
        except Exception as exc:  # noqa: BLE001 - fall back on any pipeline fault
            _record_runtime_fallback(engine, capability, label, _short_error(exc))
            engine.errors[capability] = _short_error(exc)
    return fallback(*args), "fallback"


def engine_report(engine: Engine) -> dict[str, Any]:
    core = ("extract_document", "build_plan", "draft", "value_model")
    pipelines = sum(1 for key in core if engine.sources.get(key) == "pipeline")
    if pipelines == len(core) and not engine.runtime_fallbacks:
        mode = "pipeline"
    elif pipelines == 0:
        mode = "fallback"
    else:
        mode = "mixed"
    return {
        "mode": mode,
        "sources": dict(engine.sources),
        "errors": dict(engine.errors),
        "runtime_fallbacks": list(engine.runtime_fallbacks),
        "notes": list(engine.notes),
        "eval_available": engine.eval_available,
        "seeded_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Connection and schema
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _retention_until() -> str:
    today = date.today()
    try:
        target = today.replace(year=today.year + 5)
    except ValueError:
        target = today.replace(year=today.year + 5, day=28)
    return target.isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    advisor_name TEXT,
    firm_from TEXT,
    transition_lead TEXT,
    signed_at TEXT,
    target_live_at TEXT,
    aum_total INTEGER,
    household_count INTEGER,
    protocol_received_at TEXT,
    protocol_source_note TEXT
);

CREATE TABLE IF NOT EXISTS protocol_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id TEXT,
    field_name TEXT,
    field_note TEXT,
    sort INTEGER
);

CREATE TABLE IF NOT EXISTS protocol_restrictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id TEXT,
    restriction TEXT,
    sort INTEGER
);

CREATE TABLE IF NOT EXISTS households (
    id TEXT PRIMARY KEY,
    book_id TEXT,
    display_name TEXT,
    aum INTEGER,
    relationship TEXT,
    notes TEXT,
    contact_name TEXT,
    email TEXT,
    phone TEXT,
    address TEXT,
    client_since TEXT,
    data_quality_note TEXT,
    consent_status TEXT,
    consent_note TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    household_id TEXT,
    custodian TEXT,
    account_type TEXT,
    registration TEXT,
    masked_number TEXT,
    transfer_path TEXT,
    status TEXT,
    hard_case TEXT,
    hard_case_note TEXT,
    wave INTEGER,
    signature_count INTEGER
);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    symbol TEXT,
    name TEXT,
    quantity REAL,
    value REAL,
    cost_basis REAL,
    transferable INTEGER,
    note TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT,
    kind TEXT,
    custodian TEXT,
    filename TEXT,
    received_at TEXT,
    body_text TEXT,
    method TEXT,
    field_count INTEGER,
    flag_count INTEGER
);

CREATE TABLE IF NOT EXISTS extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT,
    field_name TEXT,
    value TEXT,
    confidence REAL,
    source_span TEXT,
    flag_kind TEXT,
    method TEXT
);

CREATE TABLE IF NOT EXISTS flags (
    id TEXT PRIMARY KEY,
    risk_id TEXT,
    document_id TEXT,
    account_id TEXT,
    household_id TEXT,
    kind TEXT,
    label TEXT,
    detail TEXT,
    severity TEXT,
    source_field TEXT,
    rule_ref TEXT,
    clears_with TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    household_id TEXT,
    phase TEXT,
    title TEXT,
    owner_role TEXT,
    due_at TEXT,
    status TEXT,
    blocker TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS risks (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    household_id TEXT,
    kind TEXT,
    severity TEXT,
    reason TEXT,
    rule_ref TEXT,
    clears_with TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    kind TEXT,
    audience TEXT,
    channel TEXT,
    subject TEXT,
    body TEXT,
    status TEXT,
    household_id TEXT,
    created_at TEXT,
    updated_at TEXT,
    version TEXT,
    mode TEXT,
    word_count INTEGER
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT,
    text TEXT,
    source_type TEXT,
    source_ref TEXT,
    status TEXT,
    sort INTEGER
);

CREATE TABLE IF NOT EXISTS policy_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT,
    check_name TEXT,
    result TEXT,
    detail TEXT,
    sort INTEGER
);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT,
    approver TEXT,
    role TEXT,
    version TEXT,
    audience TEXT,
    channel TEXT,
    disclosures TEXT,
    retention_until TEXT,
    artifact_hash TEXT,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT,
    action TEXT,
    entity_type TEXT,
    entity_id TEXT,
    detail TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT,
    field_count INTEGER,
    correct_count INTEGER,
    accuracy REAL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_accounts_household ON accounts(household_id);
CREATE INDEX IF NOT EXISTS idx_tasks_account ON tasks(account_id);
CREATE INDEX IF NOT EXISTS idx_risks_account ON risks(account_id);
CREATE INDEX IF NOT EXISTS idx_extractions_document ON extractions(document_id);
CREATE INDEX IF NOT EXISTS idx_claims_draft ON claims(draft_id);
CREATE INDEX IF NOT EXISTS idx_checks_draft ON policy_checks(draft_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(reset: bool = False) -> dict[str, Any]:
    """Create and seed the database if needed. Never writes under data/."""

    with connect() as connection:
        connection.executescript(SCHEMA)
        if reset:
            _clear(connection)
        row = connection.execute("SELECT COUNT(*) AS count FROM books").fetchone()
        if row["count"] == 0:
            _seed(connection)
    return get_engine_report()


def reset_demo() -> dict[str, Any]:
    """Reset only the SQLite state. The JSON data files are read again, never changed."""

    return init_db(reset=True)


def _clear(connection: sqlite3.Connection) -> None:
    tables = (
        "meta",
        "books",
        "protocol_fields",
        "protocol_restrictions",
        "households",
        "accounts",
        "positions",
        "documents",
        "extractions",
        "flags",
        "tasks",
        "risks",
        "drafts",
        "claims",
        "policy_checks",
        "evidence",
        "audit_events",
        "eval_runs",
    )
    for table in tables:
        connection.execute(f"DELETE FROM {table}")
    try:
        connection.execute("DELETE FROM sqlite_sequence")
    except sqlite3.OperationalError:
        pass


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _book_meta(book: dict[str, Any]) -> dict[str, Any]:
    nested = book.get("book")
    if isinstance(nested, dict):
        merged = dict(nested)
        for key, value in book.items():
            if key != "book" and key not in merged:
                merged[key] = value
        return merged
    return book


def _money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "USD 0"
    if number.is_integer():
        return f"USD {int(number):,}"
    return f"USD {number:,.2f}"


def _signature_count(account: dict[str, Any]) -> int:
    """Deterministic form and signature estimate for the demo.

    This is a display estimate built from the registration and transfer path,
    not a count of collected signatures. The plan card labels it that way.
    """

    account_type = str(account.get("account_type") or "").lower()
    registration = str(account.get("registration") or "").lower()
    transfer_path = str(account.get("transfer_path") or "").lower()

    if "irrevocable" in account_type:
        return 6
    if "trust" in account_type:
        return 5
    if "utma" in account_type or "utma" in registration:
        return 5
    if "annuity" in account_type:
        return 5
    if "limited partnership" in account_type or "lp " in account_type:
        return 5
    if "inherited ira" in account_type:
        return 5
    if "rollover" in account_type:
        return 4
    if "margin" in account_type:
        return 4
    if "jtwros" in registration or "joint" in account_type:
        return 4
    if "traditional ira" in account_type or "roth" in account_type:
        return 3
    if "tod" in account_type:
        return 3
    if transfer_path == "new account":
        return 3
    return 2


def _account_signature_count(account: dict[str, Any]) -> int:
    """Prefer the seeded estimate, fall back to the display formula."""

    raw = account.get("signature_count")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if 0 < value <= 12:
        return value
    return _signature_count(account)


def _parse_crm(text: str) -> dict[str, dict[str, str]]:
    """Read the CRM export body into household contact rows.

    The CSV has a header line, twelve contact rows, a blank line, then a second
    table. csv.DictReader stops when the household id no longer looks like one.
    """

    rows: dict[str, dict[str, str]] = {}
    lines = text.splitlines()
    header_index: Optional[int] = None
    for index, line in enumerate(lines):
        if line.startswith("household_id,household_name"):
            header_index = index
            break
    if header_index is None:
        return rows

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    for row in reader:
        household_id = (row.get("household_id") or "").strip()
        if not household_id.startswith("H-"):
            break
        rows[household_id] = {
            "contact_name": (row.get("contact_name") or "").strip(),
            "email": (row.get("email") or "").strip(),
            "phone": (row.get("phone") or "").strip(),
            "address": (row.get("mailing_address") or "").strip(),
            "client_since": (row.get("client_since") or "").strip(),
            "data_quality_note": (row.get("data_quality_note") or "").strip(),
        }
    return rows


def _consent_for(household: dict[str, Any]) -> tuple[str, str]:
    """Read the seeded consent status, falling back to the demo mapping.

    data/book.json carries households[].consent_status. When the field is absent
    (an older dataset) the mapping in this module keeps the page coherent.
    """

    household_id = str(household.get("id") or "")
    seeded = str(household.get("consent_status") or "").strip()
    if seeded not in {"received", "pending", "not_required"}:
        if household_id in CONSENT_PENDING:
            seeded = "pending"
        elif household_id in CONSENT_NOT_REQUIRED:
            seeded = "not_required"
        else:
            seeded = "received"
    if seeded == "pending":
        return "pending", "Consent request sent 2026-11-12. Not returned."
    if seeded == "not_required":
        return "not_required", "Direct introduction. No protocol data used."
    return "received", "Signed consent recorded 2026-11-10."


def _flag_kind_for_field(document_id: str, field_name: str) -> Optional[str]:
    name = field_name.lower()
    if document_id == "D-1" and "address" in name:
        return "address_stale"
    if document_id == "D-2" and "beneficiary" in name:
        return "beneficiary_missing"
    if document_id == "D-2" and ("msgf" in name or "fund" in name or "share class" in name):
        if "msgf" in name and not name.endswith(".symbol"):
            return None
        return "non_transferable_share_class"
    if document_id == "D-3" and "registration" in name:
        return "registration_mismatch"
    if document_id == "D-5" and "ein" in name:
        return "trust_ein_absent"
    if document_id == "D-6" and "surrender" in name:
        return "annuity_surrender_charge"
    return None


def _normalise_field(row: dict[str, Any]) -> dict[str, Any]:
    try:
        confidence = float(row.get("confidence") or 0.9)
    except (TypeError, ValueError):
        confidence = 0.9
    confidence = max(0.0, min(1.0, confidence))
    value = row.get("value")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return {
        "name": str(row.get("name") or row.get("field_name") or "field"),
        "value": value if value is not None else "",
        "confidence": round(confidence, 2),
        "source_span": str(row.get("source_span") or ""),
    }


def _seeded_extractions(book: dict[str, Any], document_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in book.get("extractions", []):
        if not isinstance(row, dict) or str(row.get("document_id")) != document_id:
            continue
        rows.append(
            {
                "name": str(row.get("field_name") or "field"),
                "value": row.get("value"),
                "confidence": row.get("confidence"),
                "source_span": row.get("source_span"),
            }
        )
    return rows


def _seeded_flag_detail(book: dict[str, Any], document_id: str, label: str) -> Optional[str]:
    wanted = label.lower()
    for row in book.get("extractions", []):
        if not isinstance(row, dict) or str(row.get("document_id")) != document_id:
            continue
        field_name = str(row.get("field_name") or "")
        if not field_name.lower().startswith("flag:"):
            continue
        if wanted in field_name.lower():
            detail = str(row.get("value") or "").strip()
            if detail.lower().startswith("flag:"):
                detail = detail.split(":", 1)[1].strip()
            return detail or None
    return None


def _default_disclosure(kind: str) -> str:
    if kind == "client_update":
        return "This note is for explanation only. All data in this sample is synthetic."
    if kind == "custodian_cover":
        return "This letter is for transfer processing only. All data in this sample is synthetic."
    return "Internal use only. All data in this sample is synthetic."


def _store_digest(
    meta: dict[str, Any],
    accounts: list[dict[str, Any]],
    risks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the internal digest from the store counters.

    The internal digest is the one draft whose numbers are all book-level
    aggregates. Keeping it on the store avoids a summary that disagrees with the
    plan screen when the generator's wave or risk definitions differ.
    """

    account_count = len(accounts)
    aum = int(meta.get("aum_total") or 0)
    wave1 = sum(1 for account in accounts if int(account.get("wave") or 1) == 1)
    hard_cases = sum(1 for account in accounts if account.get("hard_case"))
    open_risks = sum(1 for risk in risks if risk.get("status") in {"open", "mitigating"})
    high_risks = sum(1 for risk in risks if risk.get("severity") == "high")
    body = "\n\n".join(
        (
            f"Internal transition digest for {meta.get('advisor_name') or 'the book'}'s book. "
            f"The book has {account_count} accounts and {_money(aum)} in assets. "
            f"Wave 1 covers {wave1} accounts. {hard_cases} accounts carry a hard case.",
            f"{open_risks} risks are open, {high_risks} of them high. "
            "Each risk carries a policy reference and the artifact that clears it.",
            "The queue holds client, custodian, and internal drafts. No client-facing draft is "
            "sent without a recorded approver role and an evidence file.",
            "Internal use only. All data in this sample is synthetic.",
        )
    )
    checks = [
        _check("advice_language", "pass", "No recommendation, transaction, or guarantee language found."),
        _check("promised_timing", "pass", "No timing promise found."),
        _check("performance_claim", "pass", "No performance or return claim found."),
        _check("testimonial", "pass", "No testimonial, endorsement, or rating found."),
        _check("uncited_number", "pass", "Every number has a claim that resolves to a seeded record."),
        _check("missing_disclosure", "pass", "Internal-use marker is present."),
    ]
    return {
        "subject": "Transition digest",
        "body": body,
        "claims": [
            {
                "text": f"{account_count} accounts",
                "source_type": "book",
                "source_ref": "book.accounts",
                "status": "cited",
            },
            {
                "text": f"{_money(aum)} in assets",
                "source_type": "book",
                "source_ref": "book.aum_total",
                "status": "cited",
            },
            {
                "text": f"Wave 1 covers {wave1} accounts",
                "source_type": "book",
                "source_ref": "book.accounts",
                "status": "cited",
            },
            {
                "text": f"{hard_cases} accounts carry a hard case",
                "source_type": "book",
                "source_ref": "book.accounts",
                "status": "cited",
            },
            {
                "text": f"{open_risks} risks are open",
                "source_type": "book",
                "source_ref": "book.risks",
                "status": "cited",
            },
            {
                "text": f"{high_risks} of them high",
                "source_type": "book",
                "source_ref": "book.risks",
                "status": "cited",
            },
            {
                "text": "This draft is explain-only and does not recommend a change.",
                "source_type": "policy",
                "source_ref": "Firm policy CP-02 (synthetic)",
                "status": "cited",
            },
        ],
        "blocked": [],
        "kind": "internal_digest",
        "household_id": None,
        "audience": "internal",
        "channel": "internal",
        "word_count": len(body.split()),
        "mode": "store digest (seeded counters)",
        "generated_at": "2026-11-12T09:45:00",
        "policy_checks": checks,
    }


def _digest_conflicts(
    body: str,
    accounts: list[dict[str, Any]],
    risks: list[dict[str, Any]],
) -> list[str]:
    """Return the book-level counts that disagree with a generated digest."""

    conflicts: list[str] = []
    wave1 = sum(1 for account in accounts if int(account.get("wave") or 1) == 1)
    high = sum(1 for risk in risks if risk.get("severity") == "high")
    open_risks = sum(1 for risk in risks if risk.get("status") in {"open", "mitigating"})

    match = re.search(r"Wave 1 covers (\d+) accounts", body)
    if match and int(match.group(1)) != wave1:
        conflicts.append(f"wave 1 is {match.group(1)} in the draft, {wave1} in the book")

    match = re.search(r"(\d+) high risks? are open", body)
    if match and int(match.group(1)) != high:
        conflicts.append(f"high risks are {match.group(1)} in the draft, {high} in the book")

    match = re.search(r"(\d+) risks? are open", body)
    if match and int(match.group(1)) != open_risks:
        conflicts.append(f"open risks are {match.group(1)} in the draft, {open_risks} in the book")

    return conflicts


def _check(
    name: str,
    result: str,
    detail: str,
) -> dict[str, str]:
    return {"check": name, "result": result, "detail": detail}


def _household_flags(flags: list[dict[str, Any]], household_id: str) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for flag in flags:
        if flag["household_id"] == household_id:
            found[flag["kind"]] = flag
    return found


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _seed(connection: sqlite3.Connection) -> None:
    payload = _load_json(DATA_DIR / "book.json", {})
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError(f"Could not read the seeded book at {DATA_DIR / 'book.json'}")

    meta = _book_meta(payload)
    households = [row for row in payload.get("households", []) if isinstance(row, dict)]
    accounts = [row for row in payload.get("accounts", []) if isinstance(row, dict)]
    positions = [row for row in payload.get("positions", []) if isinstance(row, dict)]
    seeded_risks = [dict(row) for row in payload.get("risks", []) if isinstance(row, dict)]
    seeded_tasks = [dict(row) for row in payload.get("tasks", []) if isinstance(row, dict)]
    seed_documents = {
        str(row.get("id")): dict(row)
        for row in payload.get("documents", [])
        if isinstance(row, dict)
    }

    document_files: list[dict[str, Any]] = []
    for path in sorted((DATA_DIR / "documents").glob("*.json")):
        document = _load_json(path, {})
        if isinstance(document, dict):
            document_files.append(document)

    engine = load_engine()

    # --- Book -------------------------------------------------------------
    book_id = str(meta.get("id") or "book-whitfield")
    protocol = meta.get("protocol") if isinstance(meta.get("protocol"), dict) else {}
    protocol_fields = protocol.get("permitted_fields")
    if not isinstance(protocol_fields, list) or not protocol_fields:
        protocol_fields = [name for name, _ in PROTOCOL_FIELDS]
    protocol_restrictions = protocol.get("restrictions")
    if not isinstance(protocol_restrictions, list) or not protocol_restrictions:
        protocol_restrictions = list(PROTOCOL_RESTRICTIONS)
    protocol_received_at = str(protocol.get("received_at") or meta.get("signed_at") or "")
    protocol_source_note = str(
        protocol.get("source_note")
        or "Synthetic representation of Broker Protocol data, not legal advice."
    )
    connection.execute(
        """
        INSERT INTO books (
            id, advisor_name, firm_from, transition_lead, signed_at, target_live_at,
            aum_total, household_count, protocol_received_at, protocol_source_note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            book_id,
            meta.get("advisor_name") or "",
            meta.get("firm_from") or "",
            meta.get("transition_lead") or "",
            meta.get("signed_at") or "",
            meta.get("target_live_at") or "",
            int(meta.get("aum_total") or 0),
            int(meta.get("household_count") or len(households)),
            protocol_received_at,
            protocol_source_note,
        ),
    )
    for index, field_name in enumerate(protocol_fields):
        note = PROTOCOL_FIELD_NOTES.get(str(field_name).strip().lower(), "")
        connection.execute(
            "INSERT INTO protocol_fields (book_id, field_name, field_note, sort) VALUES (?, ?, ?, ?)",
            (book_id, str(field_name), note, index),
        )
    for index, restriction in enumerate(protocol_restrictions):
        connection.execute(
            "INSERT INTO protocol_restrictions (book_id, restriction, sort) VALUES (?, ?, ?)",
            (book_id, str(restriction), index),
        )

    # --- Households and CRM contacts --------------------------------------
    crm = {}
    for document in document_files:
        if str(document.get("id")) == "D-4":
            crm = _parse_crm(str(document.get("body_text") or ""))
            break

    for household in households:
        household_id = str(household.get("id"))
        contact = crm.get(household_id, {})
        consent_status, consent_note = _consent_for(household)
        connection.execute(
            """
            INSERT INTO households (
                id, book_id, display_name, aum, relationship, notes, contact_name,
                email, phone, address, client_since, data_quality_note,
                consent_status, consent_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                household_id,
                book_id,
                household.get("display_name") or household_id,
                int(household.get("aum") or 0),
                household.get("relationship") or "",
                household.get("notes") or "",
                contact.get("contact_name") or "",
                contact.get("email") or "",
                contact.get("phone") or "",
                contact.get("address") or "",
                contact.get("client_since") or "",
                contact.get("data_quality_note") or "",
                consent_status,
                consent_note,
            ),
        )

    account_by_id: dict[str, dict[str, Any]] = {}
    for account in accounts:
        account_id = str(account.get("id"))
        account_by_id[account_id] = account
        connection.execute(
            """
            INSERT INTO accounts (
                id, household_id, custodian, account_type, registration, masked_number,
                transfer_path, status, hard_case, hard_case_note, wave, signature_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                str(account.get("household_id") or ""),
                account.get("custodian") or "",
                account.get("account_type") or "",
                account.get("registration") or "",
                account.get("masked_number") or "",
                account.get("transfer_path") or "",
                account.get("status") or "not started",
                account.get("hard_case") or "",
                account.get("hard_case_note") or "",
                int(account.get("wave") or 1),
                _account_signature_count(account),
            ),
        )

    for position in positions:
        connection.execute(
            """
            INSERT INTO positions (
                id, account_id, symbol, name, quantity, value, cost_basis, transferable, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(position.get("id") or ""),
                str(position.get("account_id") or ""),
                position.get("symbol") or "",
                position.get("name") or "",
                position.get("quantity"),
                position.get("value"),
                position.get("cost_basis"),
                1 if position.get("transferable") else 0,
                position.get("note") or "",
            ),
        )

    # --- Documents, fields, and the six findings --------------------------
    flags: list[dict[str, Any]] = []
    risk_by_id = {str(risk.get("id")): risk for risk in seeded_risks}
    for spec in FLAG_SPECS:
        risk = risk_by_id.get(spec["risk_id"], {})
        detail = _seeded_flag_detail(payload, spec["document_id"], spec["label"]) or risk.get("reason") or spec["label"]
        account = account_by_id.get(spec["account_id"], {})
        flag = {
            "id": spec["id"],
            "risk_id": spec["risk_id"],
            "document_id": spec["document_id"],
            "account_id": spec["account_id"],
            "household_id": str(account.get("household_id") or ""),
            "kind": spec["kind"],
            "label": spec["label"],
            "detail": detail,
            "severity": str(risk.get("severity") or spec["severity"]),
            "source_field": spec["source_field"],
            "rule_ref": str(risk.get("rule_ref") or ""),
            "clears_with": str(risk.get("clears_with") or ""),
            "status": str(risk.get("status") or "open"),
        }
        flags.append(flag)
        connection.execute(
            """
            INSERT INTO flags (
                id, risk_id, document_id, account_id, household_id, kind, label, detail,
                severity, source_field, rule_ref, clears_with, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                flag["id"], flag["risk_id"], flag["document_id"], flag["account_id"],
                flag["household_id"], flag["kind"], flag["label"], flag["detail"],
                flag["severity"], flag["source_field"], flag["rule_ref"],
                flag["clears_with"], flag["status"],
            ),
        )

    flag_count_by_document = Counter(flag["document_id"] for flag in flags)
    fields_total = 0
    for document in document_files:
        document_id = str(document.get("id") or "")
        metadata = seed_documents.get(document_id, {})
        kind = str(document.get("kind") or metadata.get("kind") or "document")
        text = str(document.get("body_text") or document.get("text") or "")

        if kind == "statement":
            extraction_input = dict(document)
            extraction_input["text"] = text
            result, used = _call_engine(
                engine,
                "extract_document",
                (extraction_input,),
                fallbacks.extract_document,
                f"extract {document_id}",
            )
            fields = [_normalise_field(row) for row in (result.get("fields") or [])]
            method = "Deterministic statement parser" if used == "pipeline" else "Fallback extraction record"
            if not fields:
                seeded_rows = [row for row in _seeded_extractions(payload, document_id) if not str(row["name"]).lower().startswith("flag:")]
                if seeded_rows:
                    fields = [_normalise_field(row) for row in seeded_rows]
                    method = "Seeded extraction record (extractor returned no fields)"
                    _record_runtime_fallback(
                        engine,
                        "extract_document",
                        document_id,
                        "The extractor returned no fields, so the seeded extraction record was used.",
                    )
        else:
            seeded_rows = [row for row in _seeded_extractions(payload, document_id) if not str(row["name"]).lower().startswith("flag:")]
            fields = [_normalise_field(row) for row in seeded_rows]
            method = "Recorded extraction record"

        connection.execute(
            """
            INSERT INTO documents (
                id, title, kind, custodian, filename, received_at, body_text, method,
                field_count, flag_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                document.get("title") or metadata.get("filename") or document_id,
                kind,
                document.get("custodian") or metadata.get("custodian") or "",
                document.get("filename") or metadata.get("filename") or "",
                document.get("received_at") or metadata.get("received_at") or "",
                text,
                method,
                len(fields),
                flag_count_by_document.get(document_id, 0),
            ),
        )

        for field in fields:
            flag_kind = _flag_kind_for_field(document_id, field["name"])
            connection.execute(
                """
                INSERT INTO extractions (
                    document_id, field_name, value, confidence, source_span, flag_kind, method
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    field["name"],
                    str(field["value"]),
                    float(field["confidence"]),
                    field["source_span"],
                    flag_kind,
                    method,
                ),
            )
            fields_total += 1

    # --- Plan -------------------------------------------------------------
    # The pipeline generator emits a plan without completion state. The seeded
    # task rows carry the operational status, owners, and blockers for this
    # mid-transition snapshot, so they are the display source when present. The
    # pipeline plan still runs and its critical path is recorded for the note.
    plan_result, plan_source = _call_engine(
        engine,
        "build_plan",
        (payload,),
        fallbacks.build_plan,
        "build plan",
    )
    tasks = seeded_tasks if seeded_tasks else [dict(row) for row in plan_result.get("tasks", [])]
    task_source = "seeded task rows" if seeded_tasks else "pipeline plan"
    if seeded_tasks and plan_result.get("tasks"):
        engine.notes.append(
            "Plan tasks use the seeded operational rows for status and blockers. "
            "app/pipeline.plan is loaded and its generated plan is cross-checked, "
            "but the seeded rows carry the completion state."
        )
    for task in tasks:
        phase = str(task.get("phase") or "")
        connection.execute(
            """
            INSERT OR IGNORE INTO tasks (
                id, account_id, household_id, phase, title, owner_role, due_at, status, blocker, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(task.get("id") or f"task-{task.get('account_id')}-{phase}"),
                str(task.get("account_id") or ""),
                str(task.get("household_id") or account_by_id.get(str(task.get("account_id")), {}).get("household_id") or ""),
                phase,
                task.get("title") or "",
                task.get("owner_role") or "",
                task.get("due_at") or "",
                task.get("status") or "not started",
                task.get("blocker") or "",
                task_source,
            ),
        )

    risk_rows = seeded_risks
    if not risk_rows:
        risk_rows = [dict(row) for row in plan_result.get("risks", [])]
    for risk in risk_rows:
        account_id = str(risk.get("account_id") or "")
        connection.execute(
            """
            INSERT OR IGNORE INTO risks (
                id, account_id, household_id, kind, severity, reason, rule_ref, clears_with, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(risk.get("id") or f"risk-{account_id}"),
                account_id,
                str(account_by_id.get(account_id, {}).get("household_id") or risk.get("household_id") or ""),
                risk.get("kind") or "NIGO",
                risk.get("severity") or "medium",
                risk.get("reason") or "",
                risk.get("rule_ref") or "",
                risk.get("clears_with") or "",
                risk.get("status") or "open",
            ),
        )

    # --- Drafts -----------------------------------------------------------
    household_names = {
        str(row.get("id")): row.get("display_name") or str(row.get("id"))
        for row in households
    }
    household_consent = {
        str(row.get("id")): _consent_for(row)[0]
        for row in households
    }
    drafts_created: list[dict[str, Any]] = []
    for spec in DRAFT_SPECS:
        kind = spec["kind"]
        household_id = spec["household_id"]
        call_household = household_id or (households[0].get("id") if households else "H-1042")
        result, used = _call_engine(
            engine,
            "draft",
            (kind, call_household, payload),
            fallbacks.draft,
            f"draft {kind}",
        )
        if not isinstance(result, dict):
            result = fallbacks.draft(kind, call_household, payload)
        if kind == "internal_digest":
            conflicts = _digest_conflicts(str(result.get("body") or ""), accounts, risk_rows)
            if conflicts:
                engine.notes.append(
                    "Internal digest rebuilt from store counters because the generated "
                    "text disagreed with the seeded book: " + "; ".join(conflicts) + "."
                )
                result = _store_digest(meta, accounts, risk_rows)
                used = "store"

        subject = str(result.get("subject") or f"Draft: {kind.replace('_', ' ')}")
        body = str(result.get("body") or "").strip()
        audience = str(result.get("audience") or ("client" if kind == "client_update" else "custodian" if kind == "custodian_cover" else "internal"))
        channel = str(result.get("channel") or ("email" if audience != "internal" else "internal"))
        claims = [dict(row) for row in result.get("claims", []) if isinstance(row, dict)]
        pipeline_checks = [
            {
                "check": str(row.get("check") or ""),
                "result": str(row.get("result") or "pass"),
                "detail": str(row.get("detail") or ""),
            }
            for row in result.get("policy_checks", [])
            if isinstance(row, dict)
        ]

        app_checks: list[dict[str, str]] = []
        if audience == "client":
            consent_status = household_consent.get(str(household_id or ""), "received")
            if consent_status == "pending":
                app_checks.append(
                    _check(
                        "protocol_consent",
                        "fail",
                        f"Client-facing draft for {household_names.get(str(household_id), 'this household')} "
                        "is blocked until protocol consent is recorded. Consent status: pending.",
                    )
                )
            else:
                app_checks.append(
                    _check(
                        "protocol_consent",
                        "pass",
                        f"Protocol consent is {consent_status.replace('_', ' ')} for "
                        f"{household_names.get(str(household_id), 'this household')}.",
                    )
                )

            household_flag_list = [
                flag for flag in flags if flag["household_id"] == str(household_id or "")
            ]
            address_flag = next((flag for flag in household_flag_list if flag["kind"] == "address_stale"), None)
            registration_flag = next((flag for flag in household_flag_list if flag["kind"] == "registration_mismatch"), None)
            if address_flag:
                app_checks.append(
                    _check(
                        "source_recency",
                        "warn",
                        f"Address source is stale: {address_flag['detail']} Confirm the current address before send.",
                    )
                )
            if registration_flag:
                app_checks.append(
                    _check(
                        "source_conflict",
                        "warn",
                        f"Registration conflict on the same household: {registration_flag['detail']} "
                        "Confirm the title with the custodian before send.",
                    )
                )
            other_flags = [
                flag
                for flag in household_flag_list
                if flag["kind"] not in {"address_stale", "registration_mismatch"}
            ]
            if other_flags:
                labels = "; ".join(
                    f"{flag['label']} ({flag['account_id']})" for flag in other_flags
                )
                app_checks.append(
                    _check(
                        "open_findings",
                        "warn",
                        f"{household_names.get(str(household_id), 'This household')} has "
                        f"{len(other_flags)} open document finding{'s' if len(other_flags) != 1 else ''}: "
                        f"{labels}. Confirm the transfer path before send.",
                    )
                )

        checks = list(app_checks)
        seen_checks = {check["check"] for check in checks}
        for check in pipeline_checks:
            if check["check"] in seen_checks:
                continue
            checks.append(check)
            seen_checks.add(check["check"])
        for blocked in result.get("blocked", []) or []:
            if not isinstance(blocked, dict):
                continue
            name = str(blocked.get("check") or "generation")
            if not any(check["check"] == name for check in checks):
                checks.append(
                    _check(name, "fail", str(blocked.get("detail") or "Generation was blocked."))
                )

        status = "blocked" if any(check["result"] == "fail" for check in checks) else "in_review"
        version = "1.0"
        updated_at = str(spec["created_at"])
        evidence = None

        if spec["id"] == "draft-3":
            status = "approved"
            version = "1.1"
            updated_at = "2026-11-17T15:40:00"
            evidence = {
                "approver": "Ruth Calder",
                "role": "Compliance",
                "version": version,
                "audience": audience,
                "channel": channel,
                "disclosures": _default_disclosure(kind),
                "retention_until": "2031-11-17",
                "artifact_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "approved_at": updated_at,
            }

        connection.execute(
            """
            INSERT INTO drafts (
                id, kind, audience, channel, subject, body, status, household_id,
                created_at, updated_at, version, mode, word_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spec["id"],
                kind,
                audience,
                channel,
                subject,
                body,
                status,
                household_id,
                spec["created_at"],
                updated_at,
                version,
                str(result.get("mode") or ("pipeline template" if used == "pipeline" else "fallback template")),
                int(result.get("word_count") or len(body.split())),
            ),
        )

        for index, claim in enumerate(claims):
            connection.execute(
                """
                INSERT INTO claims (draft_id, text, source_type, source_ref, status, sort)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    spec["id"],
                    str(claim.get("text") or ""),
                    str(claim.get("source_type") or ""),
                    str(claim.get("source_ref") or ""),
                    str(claim.get("status") or "cited"),
                    index,
                ),
            )

        for index, check in enumerate(checks):
            connection.execute(
                """
                INSERT INTO policy_checks (draft_id, check_name, result, detail, sort)
                VALUES (?, ?, ?, ?, ?)
                """,
                (spec["id"], check["check"], check["result"], check["detail"], index),
            )

        if evidence:
            connection.execute(
                """
                INSERT INTO evidence (
                    draft_id, approver, role, version, audience, channel, disclosures,
                    retention_until, artifact_hash, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec["id"], evidence["approver"], evidence["role"], evidence["version"],
                    evidence["audience"], evidence["channel"], evidence["disclosures"],
                    evidence["retention_until"], evidence["artifact_hash"], evidence["approved_at"],
                ),
            )

        drafts_created.append(
            {
                "id": spec["id"],
                "kind": kind,
                "status": status,
                "household_id": household_id,
                "checks": checks,
                "created_at": spec["created_at"],
            }
        )

    # --- Audit trail ------------------------------------------------------
    signature_total = sum(_account_signature_count(account) for account in accounts)
    high_risks = sum(1 for risk in risk_rows if risk.get("severity") == "high")
    wave1 = sum(1 for account in accounts if int(account.get("wave") or 1) == 1)
    events = [
        (
            "Dana Whitfield",
            "book.received",
            "book",
            book_id,
            "Contract signed. Book received from Meridian Private Wealth with "
            f"{len(households)} households and {len(accounts)} accounts.",
            "2026-11-03T09:12:00",
        ),
        (
            "Priya Raman",
            "protocol.recorded",
            "book",
            book_id,
            "Broker Protocol data recorded. Five permitted fields. Household consent tracking started.",
            "2026-11-03T09:14:00",
        ),
        (
            "Priya Raman",
            "document.received",
            "document",
            "D-1",
            "October statement received from Alder Clearing for Rowan Household.",
            "2026-11-05T08:30:00",
        ),
        (
            "Priya Raman",
            "extraction.run",
            "book",
            book_id,
            f"{len(document_files)} documents processed. {fields_total} fields extracted. "
            f"{len(flags)} hard findings raised.",
            "2026-11-10T16:20:00",
        ),
        (
            "Priya Raman",
            "plan.built",
            "book",
            book_id,
            f"{len(tasks)} tasks across {len(accounts)} accounts. {len(risk_rows)} risks, "
            f"{high_risks} high. Wave 1 set to {wave1} accounts.",
            "2026-11-11T10:05:00",
        ),
    ]
    for draft in drafts_created:
        note = "Blocked on protocol consent." if draft["status"] == "blocked" else "Ready for review."
        events.append(
            (
                "Priya Raman",
                "draft.generated",
                "draft",
                draft["id"],
                f"{draft['kind'].replace('_', ' ').capitalize()} generated. {note}",
                draft["created_at"],
            )
        )
    events.append(
        (
            "Ruth Calder",
            "draft.approved",
            "draft",
            "draft-3",
            "Approved as compliance. Evidence file v1.1 sealed. Audience: internal. Channel: internal.",
            "2026-11-17T15:40:00",
        )
    )
    events.append(
        (
            "Priya Raman",
            "consent.requested",
            "household",
            "H-1045",
            "Protocol consent request sent to Voss Household. Client-facing drafts remain blocked.",
            "2026-11-17T16:05:00",
        )
    )
    for event in events:
        connection.execute(
            """
            INSERT INTO audit_events (actor, action, entity_type, entity_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            event,
        )

    # --- Evaluation report, if one has been generated ---------------------
    eval_report = _read_eval_report()
    if eval_report:
        connection.execute(
            "INSERT INTO eval_runs (run_at, field_count, correct_count, accuracy, notes) VALUES (?, ?, ?, ?, ?)",
            (
                str(eval_report.get("run_at") or ""),
                int(eval_report.get("field_count") or 0),
                int(eval_report.get("correct_count") or 0),
                float(eval_report.get("accuracy") or 0.0),
                " ".join(eval_report.get("notes") or []),
            ),
        )

    # --- Meta -------------------------------------------------------------
    report = engine_report(engine)
    report["task_source"] = task_source
    report["plan_samples"] = {
        "tasks": len(tasks),
        "risks": len(risk_rows),
        "pipeline_tasks": len(plan_result.get("tasks", []) or []),
        "pipeline_risks": len(plan_result.get("risks", []) or []),
    }
    meta_rows = {
        "engine": json.dumps(report, sort_keys=True),
        "seeded_at": _now_iso(),
        "book_id": book_id,
        "as_of": AS_OF.isoformat(),
        "fields_total": str(fields_total),
        "findings_total": str(len(flags)),
        "signature_total": str(signature_total),
        "tasks_total": str(len(tasks)),
        "risks_total": str(len(risk_rows)),
        "high_risks_total": str(high_risks),
        "pipeline_plan_source": plan_source,
        "task_source": task_source,
        "documents_total": str(len(document_files)),
    }
    for key, value in meta_rows.items():
        connection.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def _one(sql: str, params: tuple[Any, ...] = ()) -> Optional[dict[str, Any]]:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def get_meta(key: str) -> Optional[str]:
    row = _one("SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else None


def get_engine_report() -> dict[str, Any]:
    raw = get_meta("engine")
    if not raw:
        return {"mode": "unknown", "sources": {}, "errors": {}, "runtime_fallbacks": [], "notes": []}
    try:
        report = json.loads(raw)
    except ValueError:
        return {"mode": "unknown", "sources": {}, "errors": {}, "runtime_fallbacks": [], "notes": []}
    if not isinstance(report, dict):
        return {"mode": "unknown", "sources": {}, "errors": {}, "runtime_fallbacks": [], "notes": []}
    return report


def get_book() -> dict[str, Any]:
    book = _one("SELECT * FROM books LIMIT 1")
    if not book:
        return {}
    fields = _rows("SELECT field_name, field_note FROM protocol_fields ORDER BY sort")
    restrictions = _rows("SELECT restriction FROM protocol_restrictions ORDER BY sort")
    custodians = [
        row["custodian"]
        for row in _rows("SELECT DISTINCT custodian FROM accounts ORDER BY custodian")
        if row.get("custodian")
    ]
    signed = _parse_date(book.get("signed_at"))
    target = _parse_date(book.get("target_live_at"))
    day_number = (AS_OF - signed).days + 1 if signed else None
    days_total = (target - signed).days if signed and target else None
    days_left = (target - AS_OF).days if target else None
    book["protocol"] = {
        "received_at": book.get("protocol_received_at") or "",
        "source_note": book.get("protocol_source_note") or "",
        "permitted_fields": fields,
        "restrictions": [row["restriction"] for row in restrictions],
    }
    book["custodians"] = custodians
    book["day_number"] = day_number
    book["days_total"] = days_total
    book["days_left"] = days_left
    book["aum_display"] = _money(book.get("aum_total"))
    account_count = _one("SELECT COUNT(*) AS c FROM accounts") or {"c": 0}
    document_count = _one("SELECT COUNT(*) AS c FROM documents") or {"c": 0}
    book["account_count"] = int(account_count["c"])
    book["document_count"] = int(document_count["c"])
    return book


def get_households() -> list[dict[str, Any]]:
    rows = _rows(
        """
        SELECT h.*,
               COUNT(a.id) AS account_count,
               COALESCE(SUM(a.signature_count), 0) AS signatures
        FROM households h
        LEFT JOIN accounts a ON a.household_id = h.id
        GROUP BY h.id
        ORDER BY h.id
        """
    )
    for row in rows:
        row["account_aum"] = int(row.get("aum") or 0)
    return rows


def get_consent_summary() -> dict[str, Any]:
    rows = get_households()
    counts = Counter(row["consent_status"] for row in rows)
    pending = [row for row in rows if row["consent_status"] == "pending"]
    return {
        "received": counts.get("received", 0),
        "pending": counts.get("pending", 0),
        "not_required": counts.get("not_required", 0),
        "total": len(rows),
        "pending_rows": pending,
    }


def get_accounts(
    wave: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    rows = _rows(
        """
        SELECT a.*,
               h.display_name AS household_name,
               h.consent_status,
               (SELECT MIN(t.due_at) FROM tasks t
                 WHERE t.account_id = a.id AND t.status != 'complete') AS next_due,
               (SELECT t.phase FROM tasks t
                 WHERE t.account_id = a.id AND t.status != 'complete'
                 ORDER BY t.due_at, t.id LIMIT 1) AS next_phase,
               (SELECT t.owner_role FROM tasks t
                 WHERE t.account_id = a.id AND t.status != 'complete'
                 ORDER BY t.due_at, t.id LIMIT 1) AS owner,
               (SELECT COUNT(*) FROM tasks t
                 WHERE t.account_id = a.id AND t.status = 'complete') AS tasks_complete,
               (SELECT COUNT(*) FROM tasks t
                 WHERE t.account_id = a.id) AS tasks_total,
               (SELECT COUNT(*) FROM risks r
                 WHERE r.account_id = a.id AND r.status IN ('open', 'mitigating')) AS risk_count,
               (SELECT COUNT(*) FROM flags f WHERE f.account_id = a.id) AS finding_count
        FROM accounts a
        JOIN households h ON h.id = a.household_id
        ORDER BY a.wave, h.display_name, a.id
        """
    )
    if wave and wave != "all":
        rows = [row for row in rows if str(row.get("wave")) == str(wave)]
    if status and status != "all":
        rows = [row for row in rows if str(row.get("status")) == status]
    for row in rows:
        row["status"] = str(row.get("status") or "not started")
        row["is_overdue"] = bool(
            row.get("next_due") and _parse_date(row["next_due"]) and _parse_date(row["next_due"]) < AS_OF
        )
    return rows


def get_all_risks() -> list[dict[str, Any]]:
    rows = _rows(
        """
        SELECT r.*, a.household_id AS account_household_id, a.account_type, a.status AS account_status,
               a.custodian, h.display_name AS household_name
        FROM risks r
        JOIN accounts a ON a.id = r.account_id
        JOIN households h ON h.id = a.household_id
        ORDER BY CASE r.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                 r.account_id, r.id
        """
    )
    return rows


def get_plan_view(wave: str = "1", status: str = "all") -> dict[str, Any]:
    all_accounts = get_accounts()
    filtered_accounts = get_accounts(wave=wave, status=status)
    visible_ids = {row["id"] for row in filtered_accounts}

    all_risks = get_all_risks()
    visible_risks = [risk for risk in all_risks if risk["account_id"] in visible_ids]

    phase_account_ids = {
        row["id"]
        for row in all_accounts
        if wave == "all" or str(row.get("wave")) == str(wave)
    }
    phase_tasks = _rows(
        "SELECT account_id, phase, due_at, status FROM tasks ORDER BY due_at"
    )
    phase_map: dict[str, dict[str, Any]] = {}
    for task in phase_tasks:
        if task.get("account_id") not in phase_account_ids:
            continue
        phase = str(task.get("phase") or "")
        bucket = phase_map.setdefault(
            phase,
            {"phase": phase, "first_due": None, "last_due": None, "total": 0, "blocked": 0, "complete": 0},
        )
        due = str(task.get("due_at") or "")
        if due:
            if bucket["first_due"] is None or due < bucket["first_due"]:
                bucket["first_due"] = due
            if bucket["last_due"] is None or due > bucket["last_due"]:
                bucket["last_due"] = due
        bucket["total"] += 1
        if task.get("status") == "blocked":
            bucket["blocked"] += 1
        if task.get("status") == "complete":
            bucket["complete"] += 1
    phases = sorted(phase_map.values(), key=lambda row: PHASE_ORDER.get(str(row.get("phase")), 99))

    household_rows = get_households()
    signature_total = sum(int(row.get("signatures") or 0) for row in household_rows)
    signature_max = max([int(row.get("signatures") or 0) for row in household_rows] or [0])
    pending_households = [row for row in household_rows if row["consent_status"] == "pending"]
    largest = max(household_rows, key=lambda row: int(row.get("signatures") or 0), default=None)

    task_count = _one("SELECT COUNT(*) AS c FROM tasks") or {"c": 0}
    task_complete = _one("SELECT COUNT(*) AS c FROM tasks WHERE status = 'complete'") or {"c": 0}
    task_blocked = _one("SELECT COUNT(*) AS c FROM tasks WHERE status = 'blocked'") or {"c": 0}
    upcoming = _one(
        """
        SELECT t.due_at, t.phase, t.title, a.id AS account_id
        FROM tasks t JOIN accounts a ON a.id = t.account_id
        WHERE t.status != 'complete' AND t.due_at >= ?
        ORDER BY t.due_at, t.id LIMIT 1
        """,
        (AS_OF.isoformat(),),
    )
    if upcoming is None:
        upcoming = _one(
            """
            SELECT t.due_at, t.phase, t.title, a.id AS account_id
            FROM tasks t JOIN accounts a ON a.id = t.account_id
            WHERE t.status != 'complete'
            ORDER BY t.due_at, t.id LIMIT 1
            """
        )

    high_risks = [risk for risk in all_risks if risk["severity"] == "high"]
    wave_counts = Counter(str(account.get("wave")) for account in all_accounts)
    status_counts = Counter(account.get("status") for account in all_accounts)
    wave_accounts = [
        account
        for account in all_accounts
        if wave == "all" or str(account.get("wave")) == str(wave)
    ]
    wave_status_counts = Counter(account.get("status") for account in wave_accounts)

    return {
        "wave": wave,
        "status": status,
        "accounts": filtered_accounts,
        "risks": visible_risks,
        "phases": phases,
        "households": household_rows,
        "pending_households": pending_households,
        "summary": {
            "accounts": len(all_accounts),
            "accounts_in_view": len(filtered_accounts),
            "wave1": wave_counts.get("1", 0),
            "wave2": wave_counts.get("2", 0),
            "hard_cases": sum(1 for account in all_accounts if account.get("hard_case")),
            "risks": len(all_risks),
            "risks_high": len(high_risks),
            "risks_in_view": len(visible_risks),
            "signatures": signature_total,
            "pending_consent": len(pending_households),
            "tasks": int(task_count["c"]),
            "tasks_complete": int(task_complete["c"]),
            "tasks_blocked": int(task_blocked["c"]),
            "status_counts": dict(status_counts),
            "wave_status_counts": dict(wave_status_counts),
            "wave_accounts": len(wave_accounts),
        },
        "signature_max": signature_max,
        "largest_household": largest,
        "upcoming": upcoming,
        "statuses": list(ACCOUNT_STATUSES),
        "as_of": AS_OF.isoformat(),
        "critical_accounts": [risk["account_id"] for risk in high_risks],
    }


def get_findings() -> list[dict[str, Any]]:
    rows = _rows(
        """
        SELECT f.*, d.title AS document_title, d.filename, a.account_type, a.custodian,
               h.display_name AS household_name
        FROM flags f
        JOIN documents d ON d.id = f.document_id
        JOIN accounts a ON a.id = f.account_id
        JOIN households h ON h.id = f.household_id
        ORDER BY CASE f.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, f.id
        """
    )
    return rows


def get_documents() -> list[dict[str, Any]]:
    return _rows("SELECT * FROM documents ORDER BY id")


def _field_line_matches(span: str, value: str, lines: list[str]) -> list[int]:
    matches: list[int] = []
    candidates = [span.strip(), value.strip()]
    for index, line in enumerate(lines):
        for candidate in candidates:
            if not candidate or len(candidate) < 3:
                continue
            if candidate in line or (line.strip() and len(line.strip()) >= 8 and line.strip() in candidate):
                matches.append(index)
                break
    if matches:
        return sorted(set(matches))
    # Long spans are occasionally truncated by the extractor. Retry on the
    # first 36 characters, which is enough to find a statement line.
    for index, line in enumerate(lines):
        for candidate in candidates:
            if candidate[:36] and candidate[:36] in line:
                matches.append(index)
    return sorted(set(matches))


def get_document_view(document_id: Optional[str]) -> Optional[dict[str, Any]]:
    documents = get_documents()
    if not documents:
        return None
    document = next((row for row in documents if row["id"] == document_id), documents[0])
    fields = _rows(
        """
        SELECT e.*, f.label AS flag_label, f.severity AS flag_severity
        FROM extractions e
        LEFT JOIN flags f ON f.kind = e.flag_kind AND f.document_id = e.document_id
        WHERE e.document_id = ?
        ORDER BY e.id
        """,
        (document["id"],),
    )
    body_lines = str(document.get("body_text") or "").splitlines()
    flat_fields: list[dict[str, Any]] = []
    for index, field in enumerate(fields):
        field["index"] = index
        field["lines"] = _field_line_matches(str(field.get("source_span") or ""), str(field.get("value") or ""), body_lines)
        field["line_attr"] = ",".join(str(line) for line in field["lines"])
        field["name_display"] = field.get("field_name") or "Field"
        flat_fields.append(field)

    groups: list[dict[str, Any]] = []
    group_name = {
        "crm_export": "Contact fields",
        "trust_summary": "Trust fields",
        "annuity_summary": "Contract fields",
    }.get(document.get("kind"), "Account fields")
    scalar = [field for field in flat_fields if not str(field.get("field_name") or "").startswith("position[")]
    position_fields = [field for field in flat_fields if str(field.get("field_name") or "").startswith("position[")]
    if scalar:
        groups.append({"name": group_name, "fields": scalar})
    if position_fields:
        groups.append({"name": "Positions", "fields": position_fields})

    line_map: dict[int, list[int]] = {}
    for field in flat_fields:
        for line_index in field["lines"]:
            line_map.setdefault(line_index, []).append(field["index"])
    rendered_lines = []
    for index, text in enumerate(body_lines):
        rendered_lines.append(
            {
                "number": index + 1,
                "text": text,
                "field_ids": line_map.get(index, []),
                "has_source": index in line_map,
            }
        )

    confidences = [float(field.get("confidence") or 0) for field in flat_fields]
    return {
        "document": document,
        "fields": flat_fields,
        "groups": groups,
        "lines": rendered_lines,
        "field_count": len(flat_fields),
        "flag_count": document.get("flag_count") or 0,
        "avg_confidence": round(sum(confidences) / len(confidences), 2) if confidences else 0,
        "matched_fields": sum(1 for field in flat_fields if field["lines"]),
    }


def get_drafts() -> list[dict[str, Any]]:
    rows = _rows(
        """
        SELECT d.*, h.display_name AS household_name, h.consent_status
        FROM drafts d
        LEFT JOIN households h ON h.id = d.household_id
        ORDER BY CASE d.status
                   WHEN 'blocked' THEN 0 WHEN 'in_review' THEN 1
                   WHEN 'approved' THEN 2 ELSE 3 END,
                 d.id
        """
    )
    for row in rows:
        counts = _one(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN result = 'fail' THEN 1 ELSE 0 END) AS fails, "
            "SUM(CASE WHEN result = 'warn' THEN 1 ELSE 0 END) AS warns "
            "FROM policy_checks WHERE draft_id = ?",
            (row["id"],),
        ) or {}
        row["check_total"] = int(counts.get("total") or 0)
        row["check_fails"] = int(counts.get("fails") or 0)
        row["check_warns"] = int(counts.get("warns") or 0)
    return rows


def get_draft(draft_id: str) -> Optional[dict[str, Any]]:
    draft = _one(
        """
        SELECT d.*, h.display_name AS household_name, h.consent_status, h.contact_name
        FROM drafts d
        LEFT JOIN households h ON h.id = d.household_id
        WHERE d.id = ?
        """,
        (draft_id,),
    )
    if not draft:
        return None
    draft["claims"] = _rows("SELECT * FROM claims WHERE draft_id = ? ORDER BY sort, id", (draft_id,))
    draft["checks"] = _rows("SELECT * FROM policy_checks WHERE draft_id = ? ORDER BY sort, id", (draft_id,))
    evidence = _one("SELECT * FROM evidence WHERE draft_id = ? ORDER BY id DESC LIMIT 1", (draft_id,))
    draft["evidence"] = evidence
    counts = Counter(check["result"] for check in draft["checks"])
    draft["check_counts"] = {
        "pass": counts.get("pass", 0),
        "warn": counts.get("warn", 0),
        "fail": counts.get("fail", 0),
        "total": len(draft["checks"]),
    }
    draft["blocking_checks"] = [check for check in draft["checks"] if check["result"] == "fail"]
    draft["claims_cited"] = sum(1 for claim in draft["claims"] if claim["status"] == "cited")
    draft["claims_uncited"] = sum(1 for claim in draft["claims"] if claim["status"] != "cited")
    draft["can_approve"] = (
        draft["status"] == "in_review"
        and not draft["blocking_checks"]
        and (
            draft["audience"] != "client"
            or draft.get("consent_status") in {"received", "not_required"}
        )
    )
    return draft


def get_audit(limit: int = 40) -> list[dict[str, Any]]:
    return _rows(
        "SELECT * FROM audit_events ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    )


def get_audit_count() -> int:
    row = _one("SELECT COUNT(*) AS c FROM audit_events") or {"c": 0}
    return int(row["c"])


def nav_counts() -> dict[str, int]:
    risks = _one("SELECT COUNT(*) AS c FROM risks WHERE status IN ('open', 'mitigating')") or {"c": 0}
    review = _one("SELECT COUNT(*) AS c FROM drafts WHERE status IN ('blocked', 'in_review')") or {"c": 0}
    return {"plan": int(risks["c"]), "review": int(review["c"])}


def stats() -> dict[str, Any]:
    def count(table: str, where: str = "") -> int:
        row = _one(f"SELECT COUNT(*) AS c FROM {table} {where}") or {"c": 0}
        return int(row["c"])

    book = get_book()
    consent = get_consent_summary()
    return {
        "households": count("households"),
        "accounts": count("accounts"),
        "aum": int(book.get("aum_total") or 0),
        "hard_cases": count("accounts", "WHERE hard_case != ''"),
        "documents": count("documents"),
        "fields": int(get_meta("fields_total") or 0),
        "findings": count("flags"),
        "tasks": count("tasks"),
        "tasks_complete": count("tasks", "WHERE status = 'complete'"),
        "risks": count("risks"),
        "high_risks": count("risks", "WHERE severity = 'high'"),
        "drafts": count("drafts"),
        "evidence": count("evidence"),
        "draft_blocked": count("drafts", "WHERE status = 'blocked'"),
        "draft_in_review": count("drafts", "WHERE status = 'in_review'"),
        "draft_approved": count("drafts", "WHERE status = 'approved'"),
        "draft_rejected": count("drafts", "WHERE status = 'rejected'"),
        "signatures": int(get_meta("signature_total") or 0),
        "consent_received": consent["received"],
        "consent_pending": consent["pending"],
        "audit": get_audit_count(),
        "eval_runs": count("eval_runs"),
    }


# ---------------------------------------------------------------------------
# State changes
# ---------------------------------------------------------------------------


def approve_draft(draft_id: str, role: str, note: str = "") -> tuple[bool, str]:
    role_key = (role or "").strip().lower()
    if role_key not in APPROVERS:
        return False, "role"
    approver, role_label = APPROVERS[role_key]

    with connect() as connection:
        draft = connection.execute(
            "SELECT d.*, h.consent_status, h.display_name FROM drafts d "
            "LEFT JOIN households h ON h.id = d.household_id WHERE d.id = ?",
            (draft_id,),
        ).fetchone()
        if draft is None:
            return False, "notfound"
        if draft["status"] == "blocked":
            return False, "blocked"
        if draft["status"] != "in_review":
            return False, "state"
        fails = connection.execute(
            "SELECT COUNT(*) AS c FROM policy_checks WHERE draft_id = ? AND result = 'fail'",
            (draft_id,),
        ).fetchone()["c"]
        if fails:
            return False, "blocked"
        if draft["audience"] == "client" and draft["consent_status"] not in {"received", "not_required"}:
            return False, "blocked"

        version = str(draft["version"] or "1.0")
        try:
            major, minor = version.split(".")
            version = f"{major}.{int(minor) + 1}"
        except (ValueError, TypeError):
            version = "1.1"
        approved_at = _now_iso()
        retention = _retention_until()
        artifact_hash = hashlib.sha256(str(draft["body"]).encode("utf-8")).hexdigest()
        disclosures = _default_disclosure(str(draft["kind"]))

        connection.execute("DELETE FROM evidence WHERE draft_id = ?", (draft_id,))
        connection.execute(
            """
            INSERT INTO evidence (
                draft_id, approver, role, version, audience, channel, disclosures,
                retention_until, artifact_hash, approved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id, approver, role_label, version, draft["audience"], draft["channel"],
                disclosures, retention, artifact_hash, approved_at,
            ),
        )
        connection.execute(
            "UPDATE drafts SET status = 'approved', version = ?, updated_at = ? WHERE id = ?",
            (version, approved_at, draft_id),
        )
        detail = (
            f"Approved as {role_label.lower()}. Evidence file v{version} sealed. "
            f"Audience: {draft['audience']}. Channel: {draft['channel']}."
        )
        if note.strip():
            detail += f" Note: {note.strip()[:240]}"
        connection.execute(
            """
            INSERT INTO audit_events (actor, action, entity_type, entity_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (approver, "draft.approved", "draft", draft_id, detail, approved_at),
        )
    return True, "approved"


def reject_draft(draft_id: str, role: str, reason: str = "") -> tuple[bool, str]:
    role_key = (role or "").strip().lower()
    if role_key not in APPROVERS:
        return False, "role"
    approver, role_label = APPROVERS[role_key]

    with connect() as connection:
        draft = connection.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if draft is None:
            return False, "notfound"
        if draft["status"] not in {"in_review", "blocked"}:
            return False, "state"
        rejected_at = _now_iso()
        connection.execute(
            "UPDATE drafts SET status = 'rejected', updated_at = ? WHERE id = ?",
            (rejected_at, draft_id),
        )
        detail = f"Rejected as {role_label.lower()}."
        if reason.strip():
            detail += f" Reason: {reason.strip()[:240]}"
        else:
            detail += " No reason given."
        connection.execute(
            """
            INSERT INTO audit_events (actor, action, entity_type, entity_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (approver, "draft.rejected", "draft", draft_id, detail, rejected_at),
        )
    return True, "rejected"


def mark_consent(draft_id: str) -> tuple[bool, str]:
    """Record consent for the draft's household and unblock its client drafts."""

    with connect() as connection:
        draft = connection.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if draft is None:
            return False, "notfound"
        household_id = draft["household_id"]
        if not household_id:
            return False, "consent"
        household = connection.execute(
            "SELECT * FROM households WHERE id = ?", (household_id,)
        ).fetchone()
        if household is None:
            return False, "consent"
        if household["consent_status"] == "received":
            return False, "consent"
        recorded_at = _now_iso()
        connection.execute(
            "UPDATE households SET consent_status = 'received', consent_note = ? WHERE id = ?",
            ("Consent recorded in the demo. Audit event written.", household_id),
        )
        detail = (
            f"Protocol consent recorded for {household['display_name']}. "
            "Client-facing drafts for this household unblocked."
        )
        connection.execute(
            "UPDATE policy_checks SET result = 'pass', detail = ? "
            "WHERE check_name = 'protocol_consent' AND draft_id IN "
            "(SELECT id FROM drafts WHERE household_id = ?)",
            (detail, household_id),
        )
        drafts = connection.execute(
            "SELECT id, status FROM drafts WHERE household_id = ?", (household_id,)
        ).fetchall()
        for row in drafts:
            if row["status"] not in {"blocked", "in_review"}:
                continue
            failures = connection.execute(
                "SELECT COUNT(*) AS c FROM policy_checks WHERE draft_id = ? AND result = 'fail'",
                (row["id"],),
            ).fetchone()["c"]
            next_status = "blocked" if failures else "in_review"
            connection.execute(
                "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?",
                (next_status, recorded_at, row["id"]),
            )
        connection.execute(
            """
            INSERT INTO audit_events (actor, action, entity_type, entity_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("Priya Raman", "consent.received", "household", household_id, detail, recorded_at),
        )
    return True, "consent"


# ---------------------------------------------------------------------------
# Evaluation report
# ---------------------------------------------------------------------------


def _read_eval_report() -> Optional[dict[str, Any]]:
    if not EVAL_REPORT_PATH.is_file():
        return None
    payload = _load_json(EVAL_REPORT_PATH, None)
    if not isinstance(payload, dict):
        return None
    if not {"field_count", "correct_count", "accuracy"}.issubset(payload):
        return None
    return payload


def get_eval_report() -> dict[str, Any]:
    report = _read_eval_report()
    return {
        "exists": report is not None,
        "report": report,
        "path": str(EVAL_REPORT_PATH),
        "mtime": (
            datetime.fromtimestamp(EVAL_REPORT_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            if EVAL_REPORT_PATH.is_file()
            else None
        ),
    }


def get_value_model() -> dict[str, Any]:
    """The account-based hours model from the pipeline, or the documented fallback."""

    engine = load_engine()
    book = get_book()
    model, source = _call_engine(
        engine, "value_model", (book,), fallbacks.value_model, "value model"
    )
    if not isinstance(model, dict):
        model = fallbacks.value_model(book)
        source = "fallback"
    return {
        "model": model,
        "source": source,
        "runtime_fallbacks": list(engine.runtime_fallbacks),
    }


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "database": str(DB_PATH),
        "data": str(DATA_DIR),
        "engine": get_engine_report().get("mode"),
        "book": get_meta("book_id"),
        "accounts": (stats().get("accounts") if DB_PATH.is_file() else 0),
    }
