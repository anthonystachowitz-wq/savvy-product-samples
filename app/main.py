"""FastAPI application for the Transition Copilot work sample.

All pages are server rendered with Jinja2. There is no Node build, no CDN, and
no client-side router. The JavaScript in static/app.js only adds source-span
highlighting, a field filter, risk-to-row highlighting, and a reset confirm.
Everything important works without it.

Read app/store.py for the schema and seeding, and app/fallbacks.py for the
build-time adapter used when the pipeline is unavailable.
"""

from __future__ import annotations

import importlib
import json
import logging
import platform
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import store
from .platform import config as suite_config
from .platform import llm as suite_llm
from .platform import nav as suite_nav

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["suite_modules"] = suite_nav.SUITE_MODULES
templates.env.globals["suite_levers"] = suite_nav.LEVERS


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_db()
    yield


app = FastAPI(
    title="Transition Copilot",
    description="Unofficial concept. Synthetic data. Not affiliated with Savvy.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Formatting and labels
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text.startswith("$"):
        text = text[1:]
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    if abs(number - round(number)) < 0.005:
        return "$" + f"{int(round(number)):,}"
    return "$" + f"{number:,.2f}"


def usd(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    if abs(number - round(number)) < 0.005:
        return f"USD {int(round(number)):,}"
    return f"USD {number:,.2f}"


def num(value: Any) -> str:
    number = _number(value)
    if number is None:
        return str(value or "")
    if abs(number - round(number)) < 0.005:
        return f"{int(round(number)):,}"
    return f"{number:,.2f}"


def pct(value: Any, digits: int = 0) -> str:
    number = _number(value)
    if number is None:
        return ""
    if abs(number) <= 1:
        number *= 100
    return f"{number:.{digits}f}%"


def date_long(value: Any) -> str:
    parsed = _as_date(value)
    if parsed is None:
        return "Not set"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def date_short(value: Any) -> str:
    parsed = _as_date(value)
    if parsed is None:
        return "Not set"
    return f"{parsed.strftime('%b')} {parsed.day}"


def datetime_short(value: Any) -> str:
    parsed = _as_datetime(value)
    if parsed is None:
        return "Not set"
    hour = parsed.strftime("%I").lstrip("0") or "12"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}, {hour}:{parsed.strftime('%M %p')}"


def date_range(start: Any, end: Any) -> str:
    first = _as_date(start)
    last = _as_date(end)
    if first is None:
        return ""
    if last is None or first == last:
        return date_short(first)
    if first.year == last.year and first.month == last.month:
        return f"{first.strftime('%b')} {first.day}-{last.day}"
    if first.year == last.year:
        return f"{first.strftime('%b')} {first.day} - {last.strftime('%b')} {last.day}"
    return f"{date_short(first)}, {first.year} - {date_short(last)}, {last.year}"


STATUS_LABELS = {
    "in_review": "In review",
    "not_required": "Not required",
    "in flight": "In flight",
    "in progress": "In progress",
    "not started": "Not started",
    "waiting on client": "Waiting on client",
    "waiting on custodian": "Waiting on custodian",
    "open": "Open",
    "mitigating": "Mitigating",
    "closed": "Closed",
    "approved": "Approved",
    "rejected": "Rejected",
    "blocked": "Blocked",
    "received": "Received",
    "pending": "Pending",
    "complete": "Complete",
    "pass": "Pass",
    "warn": "Warn",
    "fail": "Fail",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "cited": "Cited",
    "uncited": "Uncited",
}


def status_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in STATUS_LABELS:
        return STATUS_LABELS[text]
    return text.replace("_", " ").capitalize()


DOCUMENT_KINDS = {
    "statement": "Custodian statement",
    "crm_export": "CRM contact export",
    "trust_summary": "Trust document summary",
    "annuity_summary": "Annuity contract summary",
}

DRAFT_KINDS = {
    "client_update": "Client status note",
    "custodian_cover": "Custodian cover letter",
    "internal_digest": "Internal digest",
}

ACTION_LABELS = {
    "book.received": "Book received",
    "protocol.recorded": "Protocol recorded",
    "document.received": "Document received",
    "extraction.run": "Extraction run",
    "plan.built": "Plan built",
    "draft.generated": "Draft generated",
    "draft.approved": "Draft approved",
    "draft.rejected": "Draft rejected",
    "consent.requested": "Consent requested",
    "consent.received": "Consent received",
}

CHECK_LABELS = {
    "advice_language": "Advice language",
    "promised_timing": "Promised timing",
    "performance_claim": "Performance claim",
    "testimonial": "Testimonial",
    "uncited_number": "Uncited number",
    "missing_disclosure": "Missing disclosure",
    "protocol_consent": "Protocol consent",
    "source_recency": "Source recency",
    "source_conflict": "Source conflict",
    "open_findings": "Open findings",
    "word_count": "Word count",
    "generation": "Generation",
    "household_lookup": "Household lookup",
    "account_data": "Account data",
}

FIELD_LABELS = {
    "registration": "Registration",
    "account_number": "Account number",
    "statement_period": "Statement period",
    "account_title": "Account title",
    "address": "Address on file",
    "beneficiary": "Beneficiary",
    "trust_ein": "Trust EIN",
    "positions_count": "Positions held",
    "account_value": "Account value",
    "cost_basis": "Cost basis",
    "fund_holding": "Fund holding",
    "share_class": "Share class",
    "contract_owner": "Contract owner",
    "annuitant": "Annuitant",
    "issuer": "Issuer",
    "contract_number": "Contract number",
    "contract_type": "Contract type",
    "issue_date": "Issue date",
    "contract_value": "Contract value",
    "surrender_charge": "Surrender charge",
    "1035_exchange": "1035 exchange",
    "trust_name": "Trust name",
    "trust_type": "Trust type",
    "trust_date": "Trust date",
    "trustee": "Trustee",
    "successor_trustee": "Successor trustee",
    "governing_state": "Governing state",
    "beneficiaries": "Beneficiaries",
    "distribution_standard": "Distribution standard",
    "account_to_open": "Account to open",
    "household_id": "Household ID",
    "household_name": "Household name",
    "primary_contact": "Primary contact",
    "current_address": "Current address",
    "accounts_linked": "Accounts linked",
    "client_since": "Client since",
    "data_quality_note": "Data quality note",
    "custodian": "Custodian",
}


def kind_label(value: Any) -> str:
    text = str(value or "")
    return DOCUMENT_KINDS.get(text) or DRAFT_KINDS.get(text) or text.replace("_", " ").capitalize()


def human_field(value: Any) -> str:
    text = str(value or "")
    if text.startswith("position[") and "]." in text:
        ticker, rest = text[9:].split("].", 1)
        return f"{ticker} · {rest.replace('_', ' ').capitalize()}"
    if text in FIELD_LABELS:
        return FIELD_LABELS[text]
    if any(character.isupper() for character in text) and " " in text:
        return text
    return text.replace("_", " ").capitalize()


def phase_label(value: Any) -> str:
    return str(value or "").replace("_", " ").capitalize()


def action_label(value: Any) -> str:
    text = str(value or "")
    if text in ACTION_LABELS:
        return ACTION_LABELS[text]
    return text.replace(".", " ").capitalize()


def check_label(value: Any) -> str:
    text = str(value or "")
    return CHECK_LABELS.get(text, text.replace("_", " ").capitalize())


def source_label(value: Any) -> str:
    return {
        "book": "Book record",
        "plan": "Plan",
        "policy": "Policy",
        "document": "Document",
    }.get(str(value or ""), str(value or "").replace("_", " ").capitalize())


def audience_label(value: Any) -> str:
    return {"client": "Client", "custodian": "Custodian", "internal": "Internal"}.get(
        str(value or ""), str(value or "").capitalize()
    )


def owner_label(value: Any) -> str:
    text = str(value or "").strip()
    return text[:1].upper() + text[1:] if text else ""


def initials(value: Any) -> str:
    parts = [part for part in str(value or "").split() if part]
    return "".join(part[0].upper() for part in parts[:2]) or "TC"


def field_value(value: Any, name: Any) -> str:
    """Format a field value for the intake pane without changing the stored value."""

    text = "" if value is None else str(value)
    if not text:
        return ""
    lowered = str(name or "").lower()
    number = _number(text)
    if number is not None:
        if lowered.endswith(".quantity"):
            return f"{number:,.4f}".rstrip("0").rstrip(".")
        if lowered.endswith(".price"):
            return "$" + f"{number:,.2f}"
        if lowered.endswith((".value", ".cost_basis")) or lowered in {
            "account_value",
            "cost_basis",
            "contract_value",
        }:
            return "$" + f"{number:,.2f}"
    return text


templates.env.filters.update(
    {
        "money": money,
        "usd": usd,
        "num": num,
        "pct": pct,
        "date_long": date_long,
        "date_short": date_short,
        "datetime_short": datetime_short,
        "date_range": date_range,
        "status_label": status_label,
        "kind_label": kind_label,
        "human_field": human_field,
        "phase_label": phase_label,
        "action_label": action_label,
        "check_label": check_label,
        "source_label": source_label,
        "audience_label": audience_label,
        "owner_label": owner_label,
        "initials": initials,
        "field_value": field_value,
    }
)


# ---------------------------------------------------------------------------
# Tour, flash, and base context
# ---------------------------------------------------------------------------


TOUR_STEPS = (
    {
        "number": 1,
        "screen": "Intake",
        "caption": "A book arrives as documents and spreadsheets.",
        "href": "/intake?tour=1#documents",
    },
    {
        "number": 2,
        "screen": "Intake",
        "caption": "It becomes a structured record with confidence and flags.",
        "href": "/intake?tour=2&doc=D-2#viewer",
    },
    {
        "number": 3,
        "screen": "Plan",
        "caption": "The plan is a signature chase and a risk list.",
        "href": "/plan?tour=3&wave=1#critical-path",
    },
    {
        "number": 4,
        "screen": "Review",
        "caption": "Nothing sends without a human and an evidence file.",
        "href": "/review?tour=4&draft=draft-1",
    },
    {
        "number": 5,
        "screen": "Value",
        "caption": "The result is measured in hours returned.",
        "href": "/value?tour=5#hours",
    },
)

MESSAGES = {
    "approved": "Draft approved. The evidence file is sealed and an audit event was written.",
    "rejected": "Draft rejected. An audit event was written with the role and reason.",
    "consent": "Protocol consent recorded for the household. Client-facing drafts are unblocked and the action is in the audit trail.",
    "reset": "Demo state reset. The data files were not changed.",
}

ERRORS = {
    "role": "Select an approver role before recording a decision.",
    "blocked": "This draft cannot be approved while a policy check is failing. Resolve the failing check first.",
    "state": "This draft is no longer in review. The decision was not recorded.",
    "notfound": "That draft was not found. The demo may have been reset.",
    "consent": "Consent is already recorded for this household, or the household could not be found.",
}


def _flash(request: Request) -> dict[str, str]:
    message = MESSAGES.get(request.query_params.get("msg", ""), "")
    error = ERRORS.get(request.query_params.get("error", ""), "")
    if request.query_params.get("error") and not error:
        error = "The action could not be completed."
    return {"message": message, "error": error}


def _active_path(path: str) -> str:
    if path == "/" or path == "":
        return "overview"
    if path.startswith("/intake"):
        return "intake"
    if path.startswith("/plan"):
        return "plan"
    if path.startswith("/review"):
        return "review"
    if path.startswith("/value"):
        return "value"
    if path.startswith("/about"):
        return "about"
    return ""


def _tour_context(request: Request) -> dict[str, Any]:
    path = request.url.path
    raw = request.query_params.get("tour")
    current = 1
    if raw and raw.isdigit() and 1 <= int(raw) <= len(TOUR_STEPS):
        current = int(raw)
    elif path.startswith("/plan"):
        current = 3
    elif path.startswith("/review"):
        current = 4
    elif path.startswith("/value") or path.startswith("/about"):
        current = 5
    elif path.startswith("/intake"):
        doc = request.query_params.get("doc")
        current = 2 if doc and doc != "D-1" else 1
    prev_step = TOUR_STEPS[current - 2] if current > 1 else None
    next_step = TOUR_STEPS[current] if current < len(TOUR_STEPS) else None
    return {
        "current": current,
        "total": len(TOUR_STEPS),
        "step": TOUR_STEPS[current - 1],
        "prev_url": prev_step["href"] if prev_step else "/transition",
        "next_url": next_step["href"] if next_step else "/transition?tour=1",
        "prev_label": f"Back to step {current - 1}" if prev_step else "Overview",
        "next_label": f"Next: step {current + 1}" if next_step else "Restart tour",
        "is_last": next_step is None,
    }


def _base(request: Request, active: str) -> dict[str, Any]:
    return {
        "request": request,
        "active": active,
        "book": store.get_book(),
        "nav_counts": store.nav_counts(),
        "consent": store.get_consent_summary(),
        "engine": store.get_engine_report(),
        "tour": _tour_context(request),
        "flash": _flash(request),
        "as_of": store.AS_OF,
        "today": datetime.now(),
    }


def _render(request: Request, template: str, active: str, **extra: Any):
    context = _base(request, active)
    context.update(extra)
    return templates.TemplateResponse(request=request, name=template, context=context)


def _paragraphs(body: Any) -> list[str]:
    text = str(body or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(parts) > 1:
        return parts
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) > 4:
        return [" ".join(sentences[index : index + 3]) for index in range(0, len(sentences), 3)]
    return [text]


async def _form(request: Request) -> dict[str, str]:
    """Read an application/x-www-form-urlencoded body without extra dependencies."""

    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1].strip() for key, values in parsed.items()}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


# ---------------------------------------------------------------------------
# Suite hub and method
# ---------------------------------------------------------------------------


def _module_evals() -> tuple[list[dict[str, Any]], list[str]]:
    """Read every module's eval file so the method page reflects real scores."""

    evals: list[dict[str, Any]] = []
    failures: list[str] = []
    transition_report = suite_config.REPO_ROOT / "data" / "eval" / "report.json"
    if transition_report.is_file():
        try:
            payload = json.loads(transition_report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        score = payload.get("accuracy")
        evals.append(
            {
                "slug": "transition",
                "label": "Transition Copilot",
                "name": "Extraction field accuracy (in-sample)",
                "cases": payload.get("field_count", "n/a"),
                "score": f"{score * 100:.1f}%" if isinstance(score, float) else "n/a",
                "shows_failures": bool(payload.get("failures")),
            }
        )
    modules_dir = suite_config.REPO_ROOT / "data" / "modules"
    if not modules_dir.is_dir():
        return evals, failures
    for path in sorted(modules_dir.glob("*/eval.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        slug = path.parent.name
        try:
            label = suite_nav.module(slug)["label"]
        except KeyError:
            label = slug.replace("-", " ").title()
        score = payload.get("accuracy", payload.get("score"))
        if isinstance(score, float):
            score = f"{score * 100:.1f}%"
        evals.append(
            {
                "slug": slug,
                "label": label,
                "name": str(payload.get("name") or "Evaluation"),
                "cases": payload.get("cases", payload.get("case_count", "n/a")),
                "score": score if score is not None else "n/a",
                "shows_failures": bool(payload.get("failures")),
            }
        )
        failures.extend(str(item) for item in (payload.get("failure_notes") or []))
    return evals, failures


def _llm_stats() -> dict[str, Any]:
    runs = 0
    prompt_tokens = 0
    completion_tokens = 0
    latencies: list[int] = []
    models: set[str] = set()
    root = suite_config.CACHE_DIR
    if root.is_dir():
        for path in root.glob("**/*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            runs += 1
            usage = payload.get("usage") or {}
            prompt_tokens += int(usage.get("prompt_tokens") or 0)
            completion_tokens += int(usage.get("completion_tokens") or 0)
            if payload.get("latency_ms"):
                latencies.append(int(payload["latency_ms"]))
            if payload.get("model"):
                models.add(str(payload["model"]))
    availability = suite_llm.availability()
    return {
        "runs": runs,
        "prompt_tokens": f"{prompt_tokens:,}",
        "completion_tokens": f"{completion_tokens:,}",
        "latency": int(sum(latencies) / len(latencies)) if latencies else 0,
        "models": ", ".join(sorted(models)) or "none yet",
        "used_today": availability["used_today"],
        "daily_limit": availability["daily_limit"],
    }


@app.get("/")
def hub(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="hub.html",
        context={"request": request, "active": "hub"},
    )


@app.get("/method")
def method(request: Request):
    evals, failures = _module_evals()
    return templates.TemplateResponse(
        request=request,
        name="method.html",
        context={
            "request": request,
            "active": "method",
            "evals": evals,
            "failures": failures[:8],
            "llm_stats": _llm_stats(),
        },
    )


# ---------------------------------------------------------------------------
# Transition Copilot
# ---------------------------------------------------------------------------


@app.get("/transition")
def overview(request: Request):
    stats = store.stats()
    eval_data = store.get_eval_report()
    report = eval_data.get("report") or {}
    labels = report.get("field_count") or 51
    evidence_word = "evidence file" if stats["evidence"] == 1 else "evidence files"
    claims = [
        {
            "number": "01",
            "label": "Intake",
            "title": "The book arrives messy, and this makes it structured.",
            "body": (
                f"{stats['documents']} synthetic documents become one household and account record. "
                "Every field carries a confidence value and a source line. Six hard findings "
                "sit beside the record instead of in a spreadsheet tab."
            ),
            "proof": f"{stats['documents']} documents · {stats['fields']} fields · {stats['findings']} findings",
            "href": "/intake",
            "link": "Open intake",
        },
        {
            "number": "02",
            "label": "Plan",
            "title": "The work becomes one plan with named risks.",
            "body": (
                "Every account gets a transfer path, a status, an owner, and a next due date. "
                f"{stats['risks']} NIGO risks carry a reason, a synthetic rule reference, and the "
                "artifact that clears them."
            ),
            "proof": f"{stats['accounts']} accounts · {stats['tasks']} tasks · {stats['risks']} risks",
            "href": "/plan",
            "link": "Open plan",
        },
        {
            "number": "03",
            "label": "Review",
            "title": "Nothing leaves the queue without a human and an evidence file.",
            "body": (
                "Each draft is checked against its source claims and policy checks. "
                "A named role approves. The approval writes an evidence file and an audit "
                "event. A client draft with pending consent stays blocked."
            ),
            "proof": f"{stats['drafts']} drafts · {stats['audit']} audit events · {stats['evidence']} {evidence_word}",
            "href": "/review",
            "link": "Open review",
        },
    ]
    flow = [
        {
            "number": "01",
            "title": "Intake",
            "caption": "Documents become a book record with confidence and flags.",
            "meta": f"{stats['fields']} fields · {stats['findings']} findings",
            "href": "/intake",
        },
        {
            "number": "02",
            "title": "Plan",
            "caption": "Accounts become tasks, due dates, signatures, and NIGO risks.",
            "meta": f"{stats['tasks']} tasks · {stats['risks']} risks",
            "href": "/plan",
        },
        {
            "number": "03",
            "title": "Review",
            "caption": "Drafts wait for a role, a check result, and an evidence file.",
            "meta": f"{stats['draft_blocked']} blocked · {stats['draft_in_review']} in review",
            "href": "/review",
        },
        {
            "number": "04",
            "title": "Value",
            "caption": "The hours model and the extraction eval, with the arithmetic.",
            "meta": f"435 h baseline · {labels} labels",
            "href": "/value",
        },
    ]
    reading = [
        {
            "href": "/review/draft-1#evidence",
            "title": "Start with the consent gate",
            "body": "A client draft is blocked. Record consent and watch the audit trail change.",
        },
        {
            "href": "/intake?doc=D-2#viewer",
            "title": "Open the split document viewer",
            "body": "Two findings on one statement, each mapped to the line that proves it.",
        },
        {
            "href": "/plan#critical-path",
            "title": "Read the critical path",
            "body": "Where the 45 days go, from signature to live, with the risk list beside it.",
        },
        {
            "href": "/value#hours",
            "title": "Check the hours model",
            "body": "435 hours baseline, 109 to 152 returned. Every step of the arithmetic is visible.",
        },
    ]
    return _render(
        request,
        "overview.html",
        "overview",
        stats=stats,
        claims=claims,
        flow=flow,
        reading=reading,
    )


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


@app.get("/intake")
def intake(request: Request):
    documents = store.get_documents()
    requested = request.query_params.get("doc") or "D-1"
    valid_ids = {document["id"] for document in documents}
    selected_id = requested if requested in valid_ids else (documents[0]["id"] if documents else None)
    view = store.get_document_view(selected_id)
    findings = store.get_findings()
    households = store.get_households()
    consent = store.get_consent_summary()
    stats = store.stats()
    high_findings = [finding for finding in findings if finding["severity"] == "high"]
    medium_findings = [finding for finding in findings if finding["severity"] != "high"]
    return _render(
        request,
        "intake.html",
        "intake",
        documents=documents,
        selected_id=selected_id,
        view=view,
        findings=findings,
        high_findings=high_findings,
        medium_findings=medium_findings,
        households=households,
        consent=consent,
        stats=stats,
    )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@app.get("/plan")
def plan(request: Request):
    wave = request.query_params.get("wave", "1")
    if wave not in {"1", "2", "all"}:
        wave = "1"
    status = request.query_params.get("status", "all")
    if status != "all" and status not in store.ACCOUNT_STATUSES:
        status = "all"
    view = store.get_plan_view(wave, status)
    household_rows = sorted(
        view["households"],
        key=lambda row: (-int(row.get("signatures") or 0), row.get("display_name") or ""),
    )
    return _render(
        request,
        "plan.html",
        "plan",
        view=view,
        households_by_load=household_rows,
        wave=wave,
        status=status,
    )


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


APPROVER_OPTIONS = [
    {
        "value": key,
        "name": name,
        "role": role,
        "label": f"{role} · {name}",
    }
    for key, (name, role) in store.APPROVERS.items()
]


def _review_context(request: Request, draft_id: Optional[str]) -> dict[str, Any]:
    drafts = store.get_drafts()
    selected_id = draft_id or "draft-2"
    selected = store.get_draft(selected_id)
    if selected is None and drafts:
        selected = store.get_draft(drafts[0]["id"])
    if selected is None:
        return {}
    selected["paragraphs"] = _paragraphs(selected.get("body"))
    selected["disclosure_preview"] = store._default_disclosure(str(selected.get("kind") or ""))
    evidence = selected.get("evidence")
    if evidence:
        evidence["hash_short"] = str(evidence.get("artifact_hash") or "")[:16]
        evidence["hash_full"] = str(evidence.get("artifact_hash") or "")
    retention = date.today()
    try:
        retention = retention.replace(year=retention.year + 5)
    except ValueError:
        retention = retention.replace(year=retention.year + 5, day=28)
    audit = store.get_audit(40)
    stats = store.stats()
    return {
        "drafts": drafts,
        "selected": selected,
        "claims": selected.get("claims", []),
        "checks": selected.get("checks", []),
        "evidence": evidence,
        "retention_preview": retention.isoformat(),
        "audit": audit,
        "audit_count": store.get_audit_count(),
        "approver_options": APPROVER_OPTIONS,
        "stats": stats,
        "blocking": selected.get("blocking_checks", []),
    }


@app.get("/review")
def review(request: Request):
    context = _review_context(request, request.query_params.get("draft"))
    if not context:
        return _redirect("/?error=notfound")
    return _render(request, "review.html", "review", **context)


@app.get("/review/{draft_id}")
def review_draft(request: Request, draft_id: str):
    context = _review_context(request, draft_id)
    if not context:
        return _redirect("/review?error=notfound")
    return _render(request, "review.html", "review", **context)


@app.post("/review/{draft_id}/approve")
async def approve(request: Request, draft_id: str):
    data = await _form(request)
    ok, code = store.approve_draft(draft_id, data.get("role", ""), data.get("note", ""))
    if ok:
        return _redirect(f"/review/{draft_id}?msg=approved")
    if code == "notfound":
        return _redirect("/review?error=notfound")
    return _redirect(f"/review/{draft_id}?error={code}")


@app.post("/review/{draft_id}/reject")
async def reject(request: Request, draft_id: str):
    data = await _form(request)
    ok, code = store.reject_draft(draft_id, data.get("role", ""), data.get("reason", ""))
    if ok:
        return _redirect(f"/review/{draft_id}?msg=rejected")
    if code == "notfound":
        return _redirect("/review?error=notfound")
    return _redirect(f"/review/{draft_id}?error={code}")


@app.post("/review/{draft_id}/consent")
async def consent(request: Request, draft_id: str):
    ok, code = store.mark_consent(draft_id)
    if ok:
        return _redirect(f"/review/{draft_id}?msg=consent")
    return _redirect(f"/review/{draft_id}?error={code}")


# ---------------------------------------------------------------------------
# Value
# ---------------------------------------------------------------------------


def _hours_model() -> dict[str, Any]:
    """The account-based hours model, from app/pipeline/value.py when it is available.

    The page renders the same figure structure either way, so a broken pipeline
    degrades to the same arithmetic rather than to a blank card.
    """

    try:
        payload = store.get_value_model()
    except Exception:  # noqa: BLE001 - the page must render even if the engine is broken
        payload = {}
    data = payload.get("model") if isinstance(payload, dict) else None
    if isinstance(data, dict) and data.get("model") == "account-based":
        reference = data.get("reference_book") or {}
        assumptions = data.get("assumptions") or {}
        low_hours = float(data.get("hours_returned_low") or 0)
        high_hours = float(data.get("hours_returned_high") or 0)
        return {
            "clients": int(reference.get("clients") or 100),
            "accounts_per_client": int(reference.get("accounts_per_household") or 5),
            "accounts": int(reference.get("accounts") or 500),
            "minutes_per_account": int(reference.get("minutes_per_account") or 45),
            "planning_hours": float(reference.get("planning_hours") or 60),
            "per_account_hours": float(reference.get("account_hours") or 375),
            "baseline_hours": float(data.get("baseline_hours") or 435),
            "low_rate": float(assumptions.get("reduction_low") or 0.25),
            "high_rate": float(assumptions.get("reduction_high") or 0.35),
            "low_hours": int(round(low_hours)),
            "high_hours": int(round(high_hours)),
            "low_days": int(round(low_hours / 8)),
            "high_days": int(round(high_hours / 8)),
            "annual_transitions": int(data.get("annual_transitions") or 40),
            "annual_low": int(round(float(data.get("annual_hours_returned_low") or 4360))),
            "annual_high": int(round(float(data.get("annual_hours_returned_high") or 6080))),
            "source": payload.get("source", "pipeline"),
        }

    clients = 100
    accounts_per_client = 5
    accounts = clients * accounts_per_client
    minutes_per_account = 45
    planning_hours = 60
    annual_transitions = 40
    low_rate = 0.25
    high_rate = 0.35

    per_account_hours = accounts * minutes_per_account / 60
    baseline_hours = per_account_hours + planning_hours
    low_hours = round(baseline_hours * low_rate)
    high_hours = round(baseline_hours * high_rate)
    low_days = round(low_hours / 8)
    high_days = round(high_hours / 8)
    annual_low = low_hours * annual_transitions
    annual_high = high_hours * annual_transitions

    return {
        "clients": clients,
        "accounts_per_client": accounts_per_client,
        "accounts": accounts,
        "minutes_per_account": minutes_per_account,
        "planning_hours": planning_hours,
        "per_account_hours": per_account_hours,
        "baseline_hours": baseline_hours,
        "low_rate": low_rate,
        "high_rate": high_rate,
        "low_hours": low_hours,
        "high_hours": high_hours,
        "low_days": low_days,
        "high_days": high_days,
        "annual_transitions": annual_transitions,
        "annual_low": annual_low,
        "annual_high": annual_high,
        "source": "local arithmetic",
    }


def _value_formula_rows(model: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "step": "1",
            "name": "Accounts in scope",
            "formula": f"{model['clients']} clients x {model['accounts_per_client']} accounts",
            "result": f"{model['accounts']:,} accounts",
            "basis": "Reference book",
        },
        {
            "step": "2",
            "name": "Per-account work",
            "formula": f"{model['accounts']:,} accounts x {model['minutes_per_account']} minutes / 60",
            "result": f"{model['per_account_hours']:,.0f} hours",
            "basis": "45 minutes per account",
        },
        {
            "step": "3",
            "name": "Planning and reconciliation",
            "formula": "Planning, client contact, and reconciliation",
            "result": f"{model['planning_hours']} hours",
            "basis": "Reference estimate",
        },
        {
            "step": "4",
            "name": "Baseline per transition",
            "formula": f"{model['per_account_hours']:,.0f} + {model['planning_hours']}",
            "result": f"{model['baseline_hours']:,.0f} hours",
            "basis": "Modelled estimate",
        },
        {
            "step": "5",
            "name": "Reduction at 25%",
            "formula": f"{model['baseline_hours']:,.0f} x 0.25 = 108.75",
            "result": f"{model['low_hours']:,.0f} hours",
            "basis": "Rounded to the nearest hour",
        },
        {
            "step": "6",
            "name": "Reduction at 35%",
            "formula": f"{model['baseline_hours']:,.0f} x 0.35 = 152.25",
            "result": f"{model['high_hours']:,.0f} hours",
            "basis": "Rounded to the nearest hour",
        },
        {
            "step": "7",
            "name": "Working days returned",
            "formula": f"{model['low_hours']} / 8 and {model['high_hours']} / 8",
            "result": f"{model['low_days']} to {model['high_days']} days",
            "basis": "8-hour working day",
        },
        {
            "step": "8",
            "name": "Annual at 40 transitions",
            "formula": f"{model['low_hours']} x {model['annual_transitions']} and "
                       f"{model['high_hours']} x {model['annual_transitions']}",
            "result": f"{model['annual_low']:,} to {model['annual_high']:,} hours",
            "basis": "Modelled estimate",
        },
    ]


@app.get("/value")
def value(request: Request):
    model = _hours_model()
    stats = store.stats()
    eval_data = store.get_eval_report()
    report = eval_data.get("report")
    accuracy_pct = None
    failures: list[dict[str, Any]] = []
    per_field: list[dict[str, Any]] = []
    report_notes: list[str] = []
    if report:
        accuracy = report.get("accuracy")
        accuracy_pct = round(float(accuracy) * 100, 1) if accuracy is not None else None
        per_field = report.get("per_field") or []
        failures = report.get("failures") or []
        report_notes = report.get("notes") or []
    eval_path_display = "data/eval/report.json"
    try:
        eval_path_display = str(
            Path(eval_data.get("path", "")).resolve().relative_to(store.REPO_ROOT.resolve())
        )
    except (ValueError, OSError):
        pass
    indicators = [
        {"label": "Seeded accounts", "value": stats["accounts"], "sub": "18 in the book, 12 in wave 1"},
        {"label": "Documents read", "value": stats["documents"], "sub": "6 synthetic files"},
        {"label": "Fields extracted", "value": stats["fields"], "sub": "with confidence and source spans"},
        {"label": "Hard findings", "value": stats["findings"], "sub": "all linked to a NIGO risk"},
        {"label": "NIGO risks", "value": stats["risks"], "sub": f"{stats['high_risks']} high"},
        {"label": "Tasks planned", "value": stats["tasks"], "sub": f"{stats['tasks_complete']} complete"},
    ]
    return _render(
        request,
        "value.html",
        "value",
        model=model,
        formula_rows=_value_formula_rows(model),
        indicators=indicators,
        eval_data=eval_data,
        report=report,
        accuracy_pct=accuracy_pct,
        failures=failures,
        per_field=per_field,
        report_notes=report_notes,
        eval_path_display=eval_path_display,
        stats=stats,
    )


# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------


@app.get("/about")
def about(request: Request):
    engine = store.get_engine_report()
    stats = store.stats()
    eval_data = store.get_eval_report()
    report = eval_data.get("report")
    real = [
        "A server-rendered FastAPI app with SQLite state. Draft status, evidence, consent, and the audit trail persist across requests.",
        "A real extraction pass over the six synthetic documents. Every field has a confidence value and a source span.",
        "A plan with account-level due dates, owners, phases, and a seeded NIGO risk register.",
        "Role-based approval with an evidence file, an artifact hash, and an append-only audit event.",
        "The hours arithmetic on the value page. Every step can be checked by hand.",
    ]
    simulated = [
        "Every name, household, custodian, account number, address, phone number, and email address.",
        "The policy references and custodian rule names. They are labelled synthetic in the plan.",
        "Task dates, completion states, and the six hard findings. They are seeded to show a mid-transition snapshot.",
        "The 45 minutes per account, the 100-client reference book, and the 25 to 35 percent reduction. They are modelled assumptions, not a time study.",
        "The compliance outcome. This is a product sample, not legal or compliance advice.",
    ]
    ai_points = [
        (
            "Extraction is a deterministic parser.",
            "It reads the statement layout with fixed rules. There is no model call in this run, and no field is invented when the text is missing.",
        ),
        (
            "Drafting is deterministic by default.",
            "Each draft is assembled from seeded facts. The provider interface also has a cached path that replays recorded model output by key, and an optional Bedrock path behind an environment switch. The cache exists so a live run cannot make the demo drift. This run used no model calls.",
        ),
        (
            "Every numeric claim must resolve.",
            "A claim table points each number or status back to a book, plan, or policy reference. An uncited number fails a policy check and blocks approval.",
        ),
        (
            "No model approves anything.",
            "A human role approves. The model can draft and check. It cannot seal an evidence file or write an approval as a person.",
        ),
    ]
    compliance = [
        {
            "title": "Explain versus advise",
            "does": "Client drafts explain what the record shows and what happens next. Recommendation, transaction, and guarantee language fails the advice check. An advice question routes to the adviser.",
            "does_not": "The sample does not recommend a security, a rollover, an allocation, or a timing decision.",
        },
        {
            "title": "Protocol consent",
            "does": "The book record carries the five Broker Protocol fields and the date they arrived. Client contact waits for resignation. Each household has a consent status. A pending household blocks its client-facing draft.",
            "does_not": "The sample does not use account numbers, balances, holdings, or statements as protocol data. It does not contact a client before consent.",
        },
        {
            "title": "Records and retention",
            "does": "Approval writes an evidence file with approver, role, version, audience, channel, disclosures, retention date, and artifact hash. Records are firm-held in the demo SQLite file. Five-year retention is the target.",
            "does_not": "The sample does not claim a vendor retention agreement. A production deployment would need a zero-retention carve-out in the vendor contract.",
        },
        {
            "title": "Privacy",
            "does": "All data is synthetic and local. Account numbers are masked. There are no tax identifiers, no analytics, no third-party requests, and no external fonts or images.",
            "does_not": "The sample does not process real client data, connect to a custodian, or send telemetry anywhere.",
        },
        {
            "title": "Human approval",
            "does": "A named role is required. A failing policy check disables approval on the server, not only in the button. Approve, reject, and consent each write an audit event.",
            "does_not": "The sample does not auto-send, schedule, or deliver a draft. There is no mail or custodian integration.",
        },
    ]
    limitations = [
        "No custodian API, no ACATS submission, and no account opening integration. Transfer paths are a plan, not an execution.",
        "The documents are synthetic text files. There is no PDF ingestion, OCR, or document storage.",
        "The extraction eval is small and in-sample. A high score on 51 labels describes this dataset, not the next statement.",
        "No public NIGO rate and no public days-to-first-funded series exist. The product instruments both from day one instead of inventing a benchmark.",
        "The policy checks are drafting controls. They are not a legal opinion and the rule references are synthetic.",
        "No authentication, no multi-user roles, and no production database. State is one local SQLite file.",
        "The value model is an assumption set. It is not a time study and not a measured result.",
    ]
    sources = {
        "extract_document": "Extraction",
        "build_plan": "Plan",
        "draft": "Draft",
        "value_model": "Value model",
        "eval": "Eval",
    }
    return _render(
        request,
        "about.html",
        "about",
        stats=stats,
        engine=engine,
        eval_data=eval_data,
        report=report,
        real=real,
        simulated=simulated,
        ai_points=ai_points,
        compliance=compliance,
        limitations=limitations,
        sources=sources,
        python_version=platform.python_version(),
        sqlite_version=sqlite3.sqlite_version,
        db_path=str(store.DB_PATH),
        data_path=str(store.DATA_DIR),
    )


# ---------------------------------------------------------------------------
# Utility routes
# ---------------------------------------------------------------------------


@app.post("/reset")
def reset():
    store.reset_demo()
    return _redirect("/?msg=reset")


@app.get("/healthz")
def healthz():
    return JSONResponse(store.health())


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/static/favicon.svg", status_code=307)

# ---------------------------------------------------------------------------
# Product sample routers
# ---------------------------------------------------------------------------

_suite_log = logging.getLogger("savvy.suite")

for _router_file in sorted((BASE_DIR / "modules").glob("*/routes.py")):
    _slug = _router_file.parent.name
    try:
        _module = importlib.import_module(f"app.modules.{_slug}.routes")
        app.include_router(_module.router)
        _suite_log.info("Mounted product sample: %s", _slug)
    except Exception as _exc:  # noqa: BLE001 - a broken sample must not take down the suite
        _suite_log.warning("Product sample %s did not mount: %s", _slug, _exc)