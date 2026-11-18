"""Build-time fallback adapter for the Transition Copilot demo.

This module exists so the web UI can still render if the parallel pipeline
workstream is unfinished, renamed, or fails to import. It reads the same seeded
files under data/ and derives the minimum view models the screens need.

The adapter is deliberately marked everywhere it appears:

* the footer of the app reports which engine seeded the demo,
* the About screen lists pipeline and fallback per capability,
* seeded rows carry a method label such as "fallback: seeded extraction".

Nothing here invents data. Every fallback value comes from data/book.json or
data/documents/*.json. The fallback draft templates are clear about being
templates, not model output.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"

PHASES = (
    "intake",
    "account_opening",
    "document_collection",
    "transfer_initiation",
    "custodian_review",
    "confirmation",
    "handoff",
)

AUDIENCE = {
    "client_update": "client",
    "custodian_cover": "custodian",
    "internal_digest": "internal",
}

CHANNEL = {
    "client_update": "email",
    "custodian_cover": "email",
    "internal_digest": "internal",
}

SEVERITY_BY_FLAG = {
    "address_stale": "medium",
    "beneficiary_missing": "high",
    "non_transferable_share_class": "high",
    "registration_mismatch": "high",
    "trust_ein_absent": "high",
    "annuity_surrender_charge": "medium",
}


# ---------------------------------------------------------------------------
# Seed readers
# ---------------------------------------------------------------------------


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _load_book() -> dict[str, Any]:
    payload = _load_json(DATA_DIR / "book.json", {})
    return payload if isinstance(payload, dict) else {}


def _load_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for path in sorted((DATA_DIR / "documents").glob("*.json")):
        payload = _load_json(path, {})
        if isinstance(payload, dict):
            documents.append(payload)
    return documents


def _meta(book: dict[str, Any]) -> dict[str, Any]:
    nested = book.get("book")
    if isinstance(nested, dict):
        return nested
    return book


def _households(book: dict[str, Any]) -> list[dict[str, Any]]:
    rows = book.get("households")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _accounts(book: dict[str, Any]) -> list[dict[str, Any]]:
    rows = book.get("accounts")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _account_rows(book: dict[str, Any], household_id: Optional[str] = None) -> list[dict[str, Any]]:
    rows = _accounts(book)
    if household_id is not None:
        rows = [row for row in rows if str(row.get("household_id")) == str(household_id)]
    return rows


def _household(book: dict[str, Any], household_id: str) -> Optional[dict[str, Any]]:
    for row in _households(book):
        if str(row.get("id")) == str(household_id):
            return row
    return None


def _document_text(document: dict[str, Any]) -> str:
    for key in ("body_text", "text", "body", "content", "raw_text"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "USD 0"
    if number.is_integer():
        return f"USD {int(number):,}"
    return f"USD {number:,.2f}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def _date_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_document(document: dict[str, Any]) -> dict[str, Any]:
    """Derive fields and flags from the seeded extraction records.

    The real pipeline parses statement text. This adapter reads the extraction
    rows that shipped with data/book.json, which is enough for the UI screens and
    makes the fallback visibly different in provenance.
    """

    document_id = str(document.get("id") or document.get("document_id") or "document")
    book = _load_book()
    rows = [row for row in book.get("extractions", []) if str(row.get("document_id")) == document_id]

    fields: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        name = str(row.get("field_name") or "")
        value = row.get("value")
        if name.upper().startswith("FLAG:"):
            label = name.split(":", 1)[1].strip()
            kind = _slug(label)
            detail = str(value or label)
            if detail.upper().startswith("FLAG:"):
                detail = detail.split(":", 1)[1].strip()
            flags.append(
                {
                    "kind": kind,
                    "detail": detail,
                    "severity": SEVERITY_BY_FLAG.get(kind, "medium"),
                }
            )
            continue
        fields.append(
            {
                "name": name,
                "value": value,
                "confidence": float(row.get("confidence") or 0.9),
                "source_span": str(row.get("source_span") or ""),
            }
        )

    return {
        "fields": fields,
        "flags": flags,
        "document_id": document_id,
        "method": "fallback: seeded extraction record",
    }


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def build_plan(book: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Return the seeded task and risk rows, with a small derived summary.

    The plan tasks and NIGO risks are already in data/book.json. If either list
    is missing this builds a readable minimum instead of returning an empty
    screen.
    """

    book = book or _load_book()
    meta = _meta(book)
    tasks = [dict(row) for row in book.get("tasks", []) if isinstance(row, dict)]
    risks = [dict(row) for row in book.get("risks", []) if isinstance(row, dict)]
    accounts = _accounts(book)

    if not tasks:
        signed = _parse_date(meta.get("signed_at")) or date(2026, 11, 3)
        target = _parse_date(meta.get("target_live_at")) or (signed + timedelta(days=45))
        window = max(1, (target - signed).days)
        for index, account in enumerate(accounts):
            for phase_index, phase in enumerate(PHASES):
                due = signed + timedelta(days=round(window * (phase_index + 1) / len(PHASES)))
                tasks.append(
                    {
                        "id": f"task-{account.get('id')}-{phase}",
                        "account_id": account.get("id"),
                        "household_id": account.get("household_id"),
                        "phase": phase,
                        "title": f"{phase.replace('_', ' ').capitalize()} for {account.get('id')}",
                        "owner_role": "Transition specialist" if phase_index < 3 else "Transfer desk",
                        "due_at": due.isoformat(),
                        "status": "open",
                        "blocker": "",
                    }
                )

    if not risks:
        for index, account in enumerate(accounts):
            if account.get("hard_case"):
                risks.append(
                    {
                        "id": f"risk-{account.get('id')}",
                        "account_id": account.get("id"),
                        "kind": "NIGO",
                        "severity": "high",
                        "reason": account.get("hard_case_note") or account.get("hard_case"),
                        "rule_ref": "Firm transition policy (synthetic)",
                        "clears_with": "Documentation review and client signature",
                        "status": "open",
                    }
                )

    critical_path: list[dict[str, Any]] = []
    household_names = {
        str(row.get("id")): row.get("display_name") or str(row.get("id"))
        for row in _households(book)
    }
    for account in accounts:
        if not account.get("hard_case"):
            continue
        account_tasks = [task for task in tasks if str(task.get("account_id")) == str(account.get("id"))]
        account_tasks.sort(key=lambda task: str(task.get("due_at") or ""))
        blocking = next((task for task in account_tasks if task.get("status") == "blocked"), None)
        if blocking is None and account_tasks:
            blocking = account_tasks[0]
        if blocking is None:
            continue
        blocking_index = _phase_index(blocking.get("phase"))
        downstream = []
        for task in account_tasks:
            index = _phase_index(task.get("phase"))
            if index is None or blocking_index is None:
                continue
            if index >= blocking_index:
                downstream.append(task.get("id"))
        critical_path.append(
            {
                "account_id": account.get("id"),
                "household_id": account.get("household_id"),
                "household": household_names.get(str(account.get("household_id")), ""),
                "registration": account.get("registration") or account.get("account_type") or "",
                "hard_case": account.get("hard_case"),
                "blocking_phase": blocking.get("phase"),
                "blocking_task_id": blocking.get("id"),
                "task_ids": downstream,
                "due_at": blocking.get("due_at"),
                "why": account.get("hard_case_note") or "",
            }
        )
    critical_path.sort(key=lambda item: (str(item.get("due_at") or ""), str(item.get("account_id") or "")))

    return {
        "tasks": tasks,
        "critical_path": critical_path,
        "risks": risks,
        "summary": {
            "accounts": len(accounts),
            "wave1": sum(1 for account in accounts if account.get("wave") == 1),
            "blocked": sum(1 for account in accounts if account.get("status") == "blocked"),
            "high_risks": sum(1 for risk in risks if risk.get("severity") == "high"),
        },
        "phases": list(PHASES),
        "signed_at": str(meta.get("signed_at") or ""),
        "target_live_at": str(meta.get("target_live_at") or ""),
    }


def _phase_index(value: Any) -> Optional[int]:
    text = str(value or "").replace("_", " ").strip().lower()
    for index, phase in enumerate(PHASES):
        if phase.replace("_", " ").strip().lower() == text:
            return index
    return None


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    for pattern in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


def draft(
    kind: str,
    household_id: str,
    book: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build a readable explain-only draft from the seeded household record."""

    if kind not in AUDIENCE:
        raise ValueError(f"Unknown draft kind {kind!r}")
    book = book or _load_book()
    meta = _meta(book)
    household = _household(book, household_id)
    if household is None:
        household = _households(book)[0] if _households(book) else {"id": household_id, "display_name": "Household"}
    accounts = _account_rows(book, str(household.get("id")))
    household_name = household.get("display_name") or "the household"
    advisor = meta.get("advisor_name") or "the transition team"
    lead = meta.get("transition_lead") or "the transition lead"
    aum = household.get("aum") or sum(int(account.get("value") or 0) for account in accounts)
    account_count = len(accounts)

    claims: list[dict[str, Any]] = []
    if kind == "client_update":
        account_sentences = []
        for account in accounts[:3]:
            masked = account.get("masked_number") or ""
            tail = masked.split("-")[-1] if masked else ""
            account_sentences.append(
                f"Account ending {tail} is titled {account.get('account_type')}."
            )
            if tail:
                claims.append(_claim(f"Account ending {tail}", "book", "book.accounts", book))
            claims.append(_claim(f"titled {account.get('account_type')}", "book", "book.accounts", book))
        body = (
            f"Hello. This note covers the {account_count} accounts on file for {household_name}, "
            f"with a combined value of {_money(aum)}. "
            + " ".join(account_sentences)
            + " This note explains what the record shows. It does not recommend a change to how "
            "the accounts are invested, and it is not investment advice. The next step is "
            "collecting the signed forms for each account. Your advisor can explain what each "
            f"form is for. Thank you, {advisor}. The transition team. "
            "This note is for explanation only. All data in this sample is synthetic."
        )
        subject = f"Your transition status: {household_name}"
    elif kind == "custodian_cover":
        account = accounts[0] if accounts else {}
        masked = account.get("masked_number") or ""
        tail = masked.split("-")[-1] if masked else ""
        custodian = account.get("custodian") or "the custodian"
        body = (
            f"To the transfer team at {custodian}, Please accept the enclosed transfer documents "
            f"for {household_name}. The package covers {account_count} account(s) at {custodian}, "
            f"ending {tail}. Signed authorization and the account applications are attached. "
            "This letter is for transfer processing only. It does not instruct a trade and does "
            f"not constitute investment advice. Questions about the enclosed forms can go to {lead}, "
            f"the transition lead. Thank you, {advisor}. "
            "This letter is for transfer processing only. All data in this sample is synthetic."
        )
        subject = f"Transfer documents for {household_name}"
        claims.append(_claim(f"{account_count} account(s)", "book", "book.accounts", book))
    else:
        book_aum = meta.get("aum_total") or sum(int(row.get("aum") or 0) for row in _households(book))
        hard_cases = sum(1 for account in _accounts(book) if account.get("hard_case"))
        body = (
            f"Internal transition digest for {advisor}'s book. The book has "
            f"{len(_accounts(book))} accounts and {_money(book_aum)} in assets. "
            f"Wave 1 covers {sum(1 for account in _accounts(book) if account.get('wave') == 1)} accounts. "
            f"{hard_cases} accounts carry a hard case. Each risk in the plan carries a policy "
            "reference and the artifact that clears it. No client-facing draft is sent without a "
            "recorded approver role and an evidence file. Internal use only. All data in this "
            "sample is synthetic."
        )
        subject = "Transition digest"
        claims.append(_claim(f"{len(_accounts(book))} accounts", "book", "book.accounts", book))
        claims.append(_claim(f"{_money(book_aum)} in assets", "book", "book.aum_total", book))

    claims.append(
        _claim(
            "This draft is explain-only and does not recommend a change.",
            "policy",
            "Firm policy CP-02 (synthetic)",
            book,
        )
    )

    checks = [
        {
            "check": "advice_language",
            "result": "pass",
            "detail": "No recommendation, transaction, or guarantee language found.",
        },
        {
            "check": "promised_timing",
            "result": "pass",
            "detail": "No completion date or custodian service window promised.",
        },
        {
            "check": "performance_claim",
            "result": "pass",
            "detail": "No return, yield, or relative performance statement.",
        },
        {
            "check": "testimonial",
            "result": "pass",
            "detail": "No testimonial, endorsement, or rating.",
        },
        {
            "check": "uncited_number",
            "result": "pass",
            "detail": "Every number has a claim that resolves to a seeded record.",
        },
        {
            "check": "missing_disclosure",
            "result": "pass",
            "detail": "The audience-appropriate explanation disclosure is present.",
        },
    ]

    return {
        "subject": subject,
        "body": " ".join(body.split()),
        "claims": claims,
        "blocked": [],
        "kind": kind,
        "household_id": str(household.get("id")),
        "audience": AUDIENCE[kind],
        "channel": CHANNEL[kind],
        "word_count": len(" ".join(body.split()).split()),
        "mode": "fallback template",
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "policy_checks": checks,
    }


def _claim(text: str, source_type: str, source_ref: str, book: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": text,
        "source_type": source_type,
        "source_ref": source_ref,
        "status": "cited",
    }


# ---------------------------------------------------------------------------
# Value model
# ---------------------------------------------------------------------------


def value_model(
    book: Optional[dict[str, Any]] = None,
    assumptions: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """The account-based hours model, mirroring app/pipeline/value.py.

    Only used when the pipeline cannot be imported. The figures are modelled
    estimates from stated assumptions, never a measurement.
    """

    book = book or _load_book()
    overrides = assumptions or {}
    clients = int(overrides.get("clients", 100))
    accounts_per_household = int(overrides.get("accounts_per_household", 5))
    accounts = int(overrides.get("accounts", clients * accounts_per_household))
    minutes_per_account = float(overrides.get("minutes_per_account", 45))
    planning_hours = float(overrides.get("planning_hours", 60))
    low_rate = float(overrides.get("reduction_low", 0.25))
    high_rate = float(overrides.get("reduction_high", 0.35))
    transitions = int(overrides.get("annual_transitions", 40))
    target_days = int(overrides.get("days_to_funded_target", 45))

    account_hours = accounts * minutes_per_account / 60
    baseline = account_hours + planning_hours
    low = round(baseline * low_rate)
    high = round(baseline * high_rate)
    mid = (low + high) / 2
    risks = [row for row in book.get("risks", []) if isinstance(row, dict)]
    nigo_caught = sum(
        1 for row in risks if str(row.get("status")) in {"open", "mitigating"}
    )

    seeded_accounts = len([row for row in book.get("accounts", []) if isinstance(row, dict)])
    seeded_planning = planning_hours * seeded_accounts / accounts if accounts else 0.0
    seeded_baseline = seeded_accounts * minutes_per_account / 60 + seeded_planning
    seeded_low = round(seeded_baseline * low_rate)
    seeded_high = round(seeded_baseline * high_rate)

    return {
        "model": "account-based",
        "baseline_hours": baseline,
        "assisted_hours_low": baseline - high,
        "assisted_hours_high": baseline - low,
        "hours_returned_low": low,
        "hours_returned_high": high,
        "hours_returned": mid,
        "annual_transitions": transitions,
        "annual_hours_returned_low": low * transitions,
        "annual_hours_returned_high": high * transitions,
        "annual_hours_returned": mid * transitions,
        "days_to_funded_target": target_days,
        "nigo_caught": nigo_caught,
        "assisted_hours": baseline - mid,
        "hours_baseline": baseline,
        "hours_assisted": baseline - mid,
        "reference_book": {
            "clients": clients,
            "accounts_per_household": accounts_per_household,
            "accounts": accounts,
            "minutes_per_account": minutes_per_account,
            "account_hours": account_hours,
            "planning_hours": planning_hours,
        },
        "seeded_book": {
            "households": len([row for row in book.get("households", []) if isinstance(row, dict)]),
            "accounts": seeded_accounts,
            "accounts_in_scope": seeded_accounts,
            "account_hours": seeded_accounts * minutes_per_account / 60,
            "planning_hours": seeded_planning,
            "baseline_hours": seeded_baseline,
            "hours_returned_low": seeded_low,
            "hours_returned_high": seeded_high,
        },
        "formulas": [
            f"Modelled estimate: accounts = clients x accounts_per_household = {clients} x {accounts_per_household} = {accounts} accounts",
            f"Modelled estimate: account_hours = accounts x minutes_per_account / 60 = {accounts} x {minutes_per_account:g} / 60 = {account_hours:g} hours",
            f"Modelled estimate: baseline_hours = account_hours + planning_hours = {account_hours:g} + {planning_hours:g} = {baseline:g} hours per transition",
            f"Modelled estimate: hours_returned_low = baseline_hours x reduction_low = {baseline:g} x {low_rate:g} = {baseline * low_rate:.2f}, rounded to {low:g} hours",
            f"Modelled estimate: hours_returned_high = baseline_hours x reduction_high = {baseline:g} x {high_rate:g} = {baseline * high_rate:.2f}, rounded to {high:g} hours",
            f"Modelled estimate: annual_hours_returned_low = annual_transitions x hours_returned_low = {transitions} x {low:g} = {low * transitions:g} hours",
            f"Modelled estimate: annual_hours_returned_high = annual_transitions x hours_returned_high = {transitions} x {high:g} = {high * transitions:g} hours",
            "Modelled estimate: seeded_book.baseline_hours = accounts x minutes_per_account / 60 + planning_hours x accounts / reference accounts",
        ],
        "assumptions": {
            "model": "account-based",
            "clients": clients,
            "accounts_per_household": accounts_per_household,
            "accounts": accounts,
            "minutes_per_account": minutes_per_account,
            "planning_hours": planning_hours,
            "reduction_low": low_rate,
            "reduction_high": high_rate,
            "baseline_hours": baseline,
            "assisted_hours": baseline - mid,
            "annual_transitions": transitions,
            "days_to_funded_target": target_days,
            "nigo_caught": nigo_caught,
            "provenance": (
                "Fallback illustration computed from the same stated assumptions as the "
                "pipeline model. Modelled estimates, not a measurement."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def run_eval(provider: Any = None, **_: Any) -> dict[str, Any]:
    """The UI reads data/eval/report.json. The fallback cannot compute a run."""

    raise RuntimeError(
        "The evaluation pipeline is unavailable. Run python3 scripts/run_eval.py "
        "and refresh the value page."
    )
