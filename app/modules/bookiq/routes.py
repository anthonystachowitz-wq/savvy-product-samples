"""Book IQ routes: one queue page, one household detail, and the decisions.

Everything works without JavaScript. Forms post and redirect with a flash. The
page reads recorded model output by key and degrades to a clear "not recorded"
state when a key is missing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.platform import audit, llm, nav
from app.platform import rate_limit
from app.platform.llm import LLMUnavailable

from . import data, logic

router = APIRouter(prefix="/bookiq", tags=["bookiq"])

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals["suite_modules"] = nav.SUITE_MODULES
templates.env.globals["suite_levers"] = nav.LEVERS

ACTION_LABELS = {
    "outreach.approved": "Outreach approved",
    "outreach.rejected": "Outreach rejected",
    "outreach.edited": "Draft edited",
    "rank.live_run": "Live ranking run",
    "draft.live_run": "Live draft run",
    "demo.reset": "Demo reset",
}

STATUS_LABELS = {
    "in_review": "In review",
    "approved": "Approved",
    "rejected": "Rejected",
    "received": "Received",
    "pending": "Pending",
    "not required": "Not required",
    "pass": "Pass",
    "warn": "Warn",
    "fail": "Fail",
    "verified": "Verified",
    "no_number": "No number",
    "unverified": "Unverified",
    "unknown_field": "Unknown field",
    "mismatch": "Mismatch",
    "stale": "Stale",
    "traced": "Traced",
    "untraced": "Untraced",
}

MESSAGES = {
    "approved": "Draft approved. The evidence record is sealed and nothing was sent.",
    "rejected": "Draft rejected. The reason and the decision are in the audit trail.",
    "saved": "Draft saved as a new version. Approval re-checks every number in the edited text.",
    "ranked": "Live ranking recorded. The queue now reflects the new call.",
    "drafted": "Live outreach recorded for this household. The page shows the new draft.",
    "reset": "Demo decisions reset. Data files and recordings were not changed.",
}

ERRORS = {
    "consent": "Approval blocked. This household has no recorded consent, so client outreach cannot be approved.",
    "guardrail": "Approval blocked. A guardrail check is failing, so the draft cannot be approved as written.",
    "name": "Enter the approver's name before recording a decision.",
    "role": "Select an approver role before recording a decision.",
    "nodraft": "No draft is available for this household. Run the recorder first.",
    "notfound": "That household is not in the book.",
    "reason": "Add a rejection reason before recording the decision.",
    "draft": "Add a subject and a body before saving.",
    "liveoff": "This deployment replays recordings. Start it with SAVVY_LLM_MODE=live to allow a fresh call.",
    "budget": "The daily live-call budget is used up. The recorded version is still in use.",
    "llm": "The live call failed. The recorded version is still in use.",
    "rankparse": "The live ranking call returned no usable output. The recorded ranking is still in use. Try again.",
    "locked": "This draft is approved. Reopen it with an edit before recording a decision.",
    "state": "The action could not be recorded. Try again.",
}


def action_label(value: Any) -> str:
    text = str(value or "")
    return ACTION_LABELS.get(text, text.replace(".", " ").capitalize())


def status_label(value: Any) -> str:
    text = str(value or "")
    return STATUS_LABELS.get(text, text.replace("_", " ").capitalize())


templates.env.filters.update(
    {
        "usd": logic.usd,
        "usd_short": logic.usd_short,
        "share_pct": logic.share_pct,
        "display_pct": logic.display_pct,
        "date_long": logic.date_long,
        "date_short": logic.date_short,
        "timestamp_label": logic.timestamp_label,
        "plays_label": logic.plays_label,
        "plays_short": logic.plays_short,
        "confidence_label": logic.confidence_label,
        "confidence_pct": logic.confidence_pct,
        "strength_label": logic.strength_label,
        "action_label": action_label,
        "status_label": status_label,
    }
)


def _flash(message: str, error: str) -> dict[str, str]:
    return {
        "message": MESSAGES.get(message, ""),
        "error": ERRORS.get(error, "The action could not be completed." if error else ""),
    }


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1].strip() for key, values in parsed.items()}


def _resolve_draft(
    household_id: str, decision: dict[str, Any]
) -> tuple[Optional[dict[str, Any]], Any, str, bool]:
    """Return the draft in force, the recording, a parse error, and edit state."""

    recorded = llm.recorded(logic.MODULE, logic.draft_key(household_id))
    if recorded is None:
        return None, None, "The outreach for this household is not recorded yet.", False
    parsed, error = logic.parse_draft(recorded.text)
    if parsed is None:
        return None, recorded, error, False
    edit = (decision or {}).get("edit")
    if isinstance(edit, dict) and str(edit.get("body") or "").strip():
        return (
            {
                "subject": str(edit.get("subject") or parsed["subject"]),
                "body": str(edit.get("body") or parsed["body"]),
                "claims": parsed["claims"],
            },
            recorded,
            "",
            True,
        )
    return parsed, recorded, "", False


def _next_sequence(household_id: str) -> int:
    events = audit.recent(logic.MODULE, 500)
    return (
        len(
            [
                event
                for event in events
                if event.get("action") == "outreach.approved"
                and event.get("entity_id") == household_id
            ]
        )
        + 1
    )


def _selected_context(
    household_id: str,
    rows: list[dict[str, Any]],
    show_raw: bool = True,
) -> Optional[dict[str, Any]]:
    row = next((item for item in rows if item["id"] == household_id), None)
    if row is None:
        return None
    household = row["household"]
    decision = data.decision(household_id) or {}
    draft, recorded, draft_error, edited = _resolve_draft(household_id, decision)
    guardrail = logic.guardrail(draft, household) if draft else None
    block = logic.approval_block(household, draft, guardrail)
    evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else None
    evidence_stale = bool(evidence and draft and evidence.get("draft_hash") != logic.draft_hash(draft))
    status = str(decision.get("status") or "in_review")
    meta = {
        "key": logic.draft_key(household_id),
        "model": recorded.model if recorded else "",
        "mode": recorded.mode if recorded else "",
        "created_at": recorded.created_at if recorded else "",
        "latency_ms": recorded.latency_ms if recorded else 0,
        "usage": recorded.usage if recorded else {},
        "edited": edited,
        "version": int((decision.get("edit") or {}).get("version") or 1) if edited else 1,
    }
    return {
        "row": row,
        "household": household,
        "decision": decision,
        "draft": draft,
        "draft_error": draft_error,
        "draft_raw": recorded.text if (recorded and draft_error and show_raw) else "",
        "guardrail": guardrail,
        "approval_block": block,
        "can_approve": block is None,
        "consent_blocked": block == "consent",
        "status": status,
        "evidence": evidence,
        "evidence_stale": evidence_stale,
        "edited": edited,
        "version": meta["version"],
        "meta": meta,
    }


def _eval_context() -> dict[str, Any]:
    report = data.eval_report()
    present = bool(report)
    accuracy = report.get("accuracy")
    return {
        "report": report,
        "present": present,
        "accuracy_pct": f"{float(accuracy) * 100:.1f}%" if isinstance(accuracy, (int, float)) else "n/a",
        "results": report.get("results") or [],
        "failures": report.get("failures") or [],
        "notes": report.get("failure_notes") or [],
        "missing": not present,
    }


def _page_context(
    request: Request,
    household_id: str = "",
    play_filter: str = "all",
    message: str = "",
    error: str = "",
) -> dict[str, Any]:
    payload = data.households_payload()
    households = payload.get("households") or []
    book = payload.get("book") or {}
    demo = data.demo_state()
    recorded_rank = llm.recorded(logic.MODULE, logic.RANK_KEY)
    rank_rows: list[dict[str, Any]] = []
    rank_error = ""
    if recorded_rank is not None:
        rank_rows, rank_meta = logic.parse_ranking(recorded_rank.text)
        rank_error = str(rank_meta.get("error") or "")
    rows = logic.build_rows(households, rank_rows, demo)
    summary = logic.summary(rows, book, demo)
    if play_filter not in logic.PLAYS:
        play_filter = "all"
    visible = rows if play_filter == "all" else [row for row in rows if row["play"] == play_filter]
    selected = _selected_context(household_id, rows) if household_id else None
    if household_id and selected is None and not error:
        error = "notfound"
    assembled = {
        "request": request,
        "active": "bookiq",
        "book": book,
        "as_of": payload.get("as_of") or logic.AS_OF,
        "summary": summary,
        "rows": visible,
        "play_filter": play_filter,
        "selected": selected,
        "assumptions": data.assumptions(),
        "audit_events": audit.recent(logic.MODULE, 20),
        "availability": llm.availability(),
        "eval": _eval_context(),
        "flash": _flash(message, error),
        "rank": {
            "present": recorded_rank is not None,
            "error": rank_error,
            "key": logic.RANK_KEY,
            "model": recorded_rank.model if recorded_rank else "",
            "mode": recorded_rank.mode if recorded_rank else "",
            "created_at": recorded_rank.created_at if recorded_rank else "",
            "latency_ms": recorded_rank.latency_ms if recorded_rank else 0,
            "usage": recorded_rank.usage if recorded_rank else {},
            "rows": len(rank_rows),
        },
        "data_files": [
            "households.json",
            "assumptions.json",
            "eval_cases.json",
            "eval.json",
        ],
        "data_present": data.files_present(),
        "module_key": logic.MODULE,
    }
    return assembled


@router.get("")
def index(
    request: Request,
    h: str = "",
    play: str = "all",
    msg: str = "",
    error: str = "",
):
    context = _page_context(request, household_id=h, play_filter=play, message=msg, error=error)
    return templates.TemplateResponse(
        request=request,
        name="modules/bookiq/index.html",
        context=context,
    )


@router.post("/approve")
async def approve(request: Request):
    form = await _form(request)
    household_id = form.get("h", "")
    household = data.household(household_id)
    if household is None:
        return _redirect("/bookiq?error=notfound")
    decision = data.decision(household_id) or {}
    draft, recorded, draft_error, edited = _resolve_draft(household_id, decision)
    guardrail = logic.guardrail(draft, household) if draft else None
    block = logic.approval_block(household, draft, guardrail)
    approver = form.get("approver", "")
    role = form.get("role", "")
    note = form.get("note", "")
    if block:
        return _redirect(f"/bookiq?h={household_id}&error={block}#detail")
    if not approver:
        return _redirect(f"/bookiq?h={household_id}&error=name#detail")
    if role not in {"Adviser", "Supervising principal", "Operations"}:
        return _redirect(f"/bookiq?h={household_id}&error=role#detail")
    if str(decision.get("status") or "in_review") == "approved":
        return _redirect(f"/bookiq?h={household_id}&error=locked#detail")

    decided_at = datetime.now(timezone.utc)
    evidence = logic.make_evidence(
        household=household,
        draft=draft,
        report=guardrail,
        approver=approver,
        role=role,
        note=note,
        model=recorded.model if recorded else "",
        mode=recorded.mode if recorded else "",
        cache_key=logic.draft_key(household_id),
        sequence=_next_sequence(household_id),
        decided_at=decided_at,
        version=2 if edited else 1,
    )
    entry = {
        "status": "approved",
        "decided_at": decided_at.isoformat(timespec="seconds"),
        "approver": approver,
        "role": role,
        "note": note,
        "evidence": evidence,
        "edit": decision.get("edit"),
    }
    data.save_decision(household_id, entry)
    audit.record(
        logic.MODULE,
        approver,
        "outreach.approved",
        "household",
        household_id,
        (
            f"Evidence {evidence['id']}; role {role}; version {evidence['version']}; "
            f"model {evidence['model']}; claims {evidence['claims_verified']} of {evidence['claims_total']} verified; "
            "nothing sent."
        ),
    )
    return _redirect(f"/bookiq?h={household_id}&msg=approved#detail")


@router.post("/reject")
async def reject(request: Request):
    form = await _form(request)
    household_id = form.get("h", "")
    household = data.household(household_id)
    if household is None:
        return _redirect("/bookiq?error=notfound")
    decision = data.decision(household_id) or {}
    draft, recorded, draft_error, edited = _resolve_draft(household_id, decision)
    if draft is None:
        return _redirect(f"/bookiq?h={household_id}&error=nodraft#detail")
    approver = form.get("approver", "")
    role = form.get("role", "")
    reason = form.get("reason", "")
    if not approver:
        return _redirect(f"/bookiq?h={household_id}&error=name#detail")
    if role not in {"Adviser", "Supervising principal", "Operations"}:
        return _redirect(f"/bookiq?h={household_id}&error=role#detail")
    if not reason:
        return _redirect(f"/bookiq?h={household_id}&error=reason#detail")
    entry = {
        "status": "rejected",
        "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approver": approver,
        "role": role,
        "reason": reason,
        "evidence": None,
        "edit": decision.get("edit"),
    }
    data.save_decision(household_id, entry)
    audit.record(
        logic.MODULE,
        approver,
        "outreach.rejected",
        "household",
        household_id,
        f"Role {role}; reason {reason}; nothing sent.",
    )
    return _redirect(f"/bookiq?h={household_id}&msg=rejected#detail")


@router.post("/save")
async def save(request: Request):
    form = await _form(request)
    household_id = form.get("h", "")
    household = data.household(household_id)
    if household is None:
        return _redirect("/bookiq?error=notfound")
    subject = form.get("subject", "")
    body = form.get("body", "")
    editor = form.get("editor", "")
    if not subject or not body:
        return _redirect(f"/bookiq?h={household_id}&error=draft#detail")
    if not editor:
        return _redirect(f"/bookiq?h={household_id}&error=name#detail")
    decision = data.decision(household_id) or {}
    previous = decision.get("edit") if isinstance(decision.get("edit"), dict) else {}
    version = int(previous.get("version") or 1) + 1
    entry = {
        "status": "in_review",
        "decided_at": "",
        "approver": "",
        "role": "",
        "note": "",
        "evidence": None,
        "edit": {
            "subject": subject,
            "body": body,
            "editor": editor,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "version": version,
        },
    }
    data.save_decision(household_id, entry)
    audit.record(
        logic.MODULE,
        editor,
        "outreach.edited",
        "household",
        household_id,
        f"Version {version}; {logic.word_count(body)} words; approval re-checks the edited text.",
    )
    return _redirect(f"/bookiq?h={household_id}&msg=saved#detail")


@router.post("/run-live")
def run_live(request: Request):
    if llm.availability()["mode"] != "live":
        return _redirect("/bookiq?error=liveoff")
    payload = data.households_payload()
    system, prompt = logic.build_rank_prompt(payload)
    previous = llm.snapshot(logic.MODULE, logic.RANK_KEY)
    try:
        result = llm.run(
            logic.MODULE,
            logic.RANK_KEY,
            system,
            prompt,
            max_tokens=8000,
            temperature=0.2,
            refresh=True,
        )
    except rate_limit.BudgetExceeded:
        return _redirect("/bookiq?error=budget")
    except LLMUnavailable:
        return _redirect("/bookiq?error=llm")
    rows, meta = logic.parse_ranking(result.text)
    usage = result.usage or {}
    if meta.get("error"):
        llm.restore(previous)
    audit.record(
        logic.MODULE,
        "Dana Whitfield (adviser)",
        "rank.live_run",
        "book",
        str(payload.get("book", {}).get("id") or "book-whitfield"),
        (
            f"model {result.model}; {result.latency_ms} ms; "
            f"{int(usage.get('total_tokens') or 0)} tokens; {len(rows)} rows"
            + (f"; parse error {meta.get('error')}" if meta.get("error") else "")
            + "."
        ),
    )
    if meta.get("error"):
        return _redirect("/bookiq?error=rankparse")
    return _redirect("/bookiq?msg=ranked")


@router.post("/run-live/draft")
def run_live_draft(request: Request):
    if llm.availability()["mode"] != "live":
        return _redirect("/bookiq?error=liveoff")
    household_id = request.query_params.get("h", "")
    household = data.household(household_id)
    if household is None:
        return _redirect("/bookiq?error=notfound")
    decision = data.decision(household_id) or {}
    if str(decision.get("status") or "in_review") == "approved":
        return _redirect(f"/bookiq?h={household_id}&error=locked#detail")
    recorded_rank = llm.recorded(logic.MODULE, logic.RANK_KEY)
    play = ""
    signal = ""
    if recorded_rank is not None:
        rows, _ = logic.parse_ranking(recorded_rank.text)
        row = next((item for item in rows if item["household_id"] == household_id), None)
        if row:
            play = row["play"] or ""
            signal = row["signal"] or ""
    if not play:
        rule = logic.rule_play(household)
        play = rule["play"]
        signal = rule.get("signal") or ""
    system, prompt = logic.build_draft_prompt(household, play, signal)
    try:
        result = llm.run(
            logic.MODULE,
            logic.draft_key(household_id),
            system,
            prompt,
            max_tokens=3000,
            temperature=0.35,
            refresh=True,
        )
    except rate_limit.BudgetExceeded:
        return _redirect(f"/bookiq?h={household_id}&error=budget#detail")
    except LLMUnavailable:
        return _redirect(f"/bookiq?h={household_id}&error=llm#detail")
    entry = {
        "status": "in_review",
        "decided_at": "",
        "approver": "",
        "role": "",
        "note": "",
        "evidence": None,
        "edit": None,
    }
    data.save_decision(household_id, entry)
    usage = result.usage or {}
    audit.record(
        logic.MODULE,
        "Dana Whitfield (adviser)",
        "draft.live_run",
        "household",
        household_id,
        (
            f"Model {result.model}; {result.latency_ms} ms; "
            f"{int(usage.get('total_tokens') or 0)} tokens; play {play}; nothing sent."
        ),
    )
    return _redirect(f"/bookiq?h={household_id}&msg=drafted#detail")


@router.post("/reset")
def reset(request: Request):
    data.reset_state()
    audit.record(
        logic.MODULE,
        "Demo operator",
        "demo.reset",
        "module",
        logic.MODULE,
        "Decisions cleared. Data files and recordings were not changed.",
    )
    return _redirect("/bookiq?msg=reset")