"""Routes for Supervision in the loop.

One page, six posts. The page renders recorded model output and the current
demo state. Every approval and rejection re-runs the policy gate on the server
before it writes anything.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.platform import audit, nav, state

from . import data, logic

MODULE = "supervision"
APP_DIR = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.globals["suite_modules"] = nav.SUITE_MODULES
templates.env.globals["suite_levers"] = nav.LEVERS

router = APIRouter(prefix="/supervision", tags=["supervision"])

MESSAGES = {
    "approved": "Approved. The evidence file is sealed and the audit event is written.",
    "rejected": "Rejected. The reason, the approver, and the role are in the audit trail.",
    "revised": "The recorded revision is now version 2. Its claims were classified again and the gate re-ran.",
    "returned": "Returned to the adviser. The draft stays blocked until a new version is recorded.",
    "reset": "Demo state cleared. Recordings, data files, and audit events were not changed.",
}

ERRORS = {
    "blocked": "Approval refused on the server. A policy check is failing. Resolve it before approving.",
    "consent": "Approval refused. The selected audience includes households without recorded consent.",
    "self_review": "Approval refused. The adviser who drafted the piece cannot approve it.",
    "approver": "Select an approver before recording a decision.",
    "audience": "Select a valid audience for this brief.",
    "decided": "This brief already has a decision. Reset the demo state to review it again.",
    "not_recorded": "That version is not recorded yet. Run the recorder before approving.",
    "not_blocked": "A revision or a return needs a failing check. This draft is not blocked.",
    "reason": "A rejection needs a short reason.",
    "not_found": "That brief was not found.",
    "unknown": "The action could not be completed.",
}

ACTION_LABELS = {
    "llm.recorded": "Live run recorded",
    "brief.approved": "Brief approved",
    "brief.rejected": "Brief rejected",
    "brief.blocked": "Approval refused",
    "brief.revision_applied": "Revision applied",
    "brief.returned": "Returned to adviser",
    "demo.reset": "Demo state reset",
}

WORD_LIMIT = re.compile(r"(\d+)\s*to\s*(\d+)")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _flash(request: Request) -> dict[str, str]:
    message = MESSAGES.get(request.query_params.get("msg", ""), "")
    error = ERRORS.get(request.query_params.get("error", ""), "")
    if request.query_params.get("error") and not error:
        error = ERRORS["unknown"]
    return {"message": message, "error": error}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


async def _form(request: Request) -> dict[str, str]:
    """Read an urlencoded body without adding python-multipart."""

    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1].strip() for key, values in parsed.items()}


def _stored() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = state.load(MODULE, {})
    if not isinstance(payload, dict):
        payload = {}
    decisions = payload.get("decisions")
    revisions = payload.get("revisions")
    return (
        payload,
        decisions if isinstance(decisions, dict) else {},
        revisions if isinstance(revisions, dict) else {},
    )


def _find_brief(briefs: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    for brief in briefs:
        if brief["id"] == key or brief.get("key") == key:
            return brief
    return None


def _short_name(household: dict[str, Any]) -> str:
    return str(household.get("display_name", household["id"])).replace(" Household", "")


def _household_rows(households: dict[str, dict[str, Any]], ids: list[str]) -> list[dict[str, Any]]:
    rows = []
    for household_id in ids:
        household = households.get(household_id)
        if not household:
            rows.append({"id": household_id, "name": household_id, "status": "Unknown"})
            continue
        consent = str(household.get("consent_status", "")).lower()
        if consent == "received":
            status, tone = "Consent received", "success"
        elif consent == "pending":
            status, tone = "Consent pending", "warn"
        else:
            status, tone = "No consent recorded", "neutral"
        rows.append(
            {
                "id": household_id,
                "name": _short_name(household),
                "status": status,
                "tone": tone,
            }
        )
    return rows


def _audience_options(brief: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    if brief.get("audience_type") == "public":
        return [
            {
                "id": "public",
                "label": "Public social post",
                "selectable": True,
                "count": 0,
                "note": "No household audience.",
            }
        ]
    options = []
    for audience in bundle.get("audiences", []):
        options.append(
            {
                "id": audience["id"],
                "label": "{label} ({count} households)".format(
                    label=audience["label"], count=audience["count"]
                ),
                "selectable": not audience.get("blocked", False),
                "count": audience["count"],
                "note": audience.get("note", ""),
            }
        )
    return options


def _default_audience(brief: dict[str, Any], options: list[dict[str, Any]]) -> dict[str, Any]:
    for option in options:
        if option["id"] == brief.get("default_audience"):
            return option
    return options[0]


def _word_limit(brief: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any] | None:
    match = WORD_LIMIT.search(brief.get("word_limit", ""))
    if not match or not draft.get("ok"):
        return None
    low, high = int(match.group(1)), int(match.group(2))
    count = int(draft.get("word_count") or 0)
    within = low <= count <= high
    return {
        "text": "{count} words. The brief asks for {low} to {high}.".format(
            count=count, low=low, high=high
        ),
        "within": within,
        "tone": "success" if within else "warn",
    }


def _status(decision: dict[str, Any] | None, evaluation: dict[str, Any], draft: dict[str, Any]) -> dict[str, str]:
    if decision and decision.get("status") == "approved":
        return {"key": "approved", "label": "Approved", "tone": "success"}
    if decision and decision.get("status") == "rejected":
        return {"key": "rejected", "label": "Rejected", "tone": "neutral"}
    if not draft.get("ok"):
        return {"key": "not_recorded", "label": "Not recorded", "tone": "warn"}
    if evaluation.get("blocked"):
        return {"key": "blocked", "label": "Blocked", "tone": "danger"}
    return {"key": "ready", "label": "In review", "tone": "accent"}


def _brief_view(
    brief: dict[str, Any],
    bundle: dict[str, Any],
    fact_sheet: dict[str, Any],
    decisions: dict[str, Any],
    revisions: dict[str, Any],
    households: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    revision = revisions.get(brief["id"])
    version = 2 if revision else 1
    draft = logic.load_draft(brief, version)
    claims_result = logic.load_claims(brief, fact_sheet, version)
    evaluation = logic.evaluate(brief, draft, claims_result, fact_sheet)
    decision = decisions.get(brief["id"])
    status = _status(decision, evaluation, draft)
    claims = claims_result.get("claims") or []

    paragraphs = []
    if draft.get("ok"):
        paragraphs = [logic.annotate_paragraph(part, claims) for part in draft.get("paragraphs", [])]

    options = _audience_options(brief, bundle)
    default_audience = _default_audience(brief, options)

    included_ids: list[str] = []
    excluded_ids: list[str] = []
    if brief.get("audience_type") == "households":
        segment = next(
            (item for item in bundle.get("audiences", []) if item["id"] == default_audience["id"]),
            None,
        )
        if segment:
            included_ids = list(segment.get("household_ids", []))
            excluded_ids = [
                household_id
                for household_id in households
                if household_id not in set(included_ids)
            ]
        audience_label = default_audience["label"]
        audience_note = (
            "{included} of {total} households have recorded consent to marketing. "
            "The all-book audience is refused by the server."
        ).format(included=len(included_ids), total=len(households))
    else:
        audience_label = "Public social post"
        audience_note = (
            "Public post. No household audience, so household consent status does not apply."
        )

    evidence = None
    if decision and decision.get("status") == "approved":
        stored = dict(decision)
        stored["hash_short"] = str(stored.get("artifact_hash", ""))[:12]
        stored["retention_display"] = logic.date_display(stored.get("retention_until"))
        evidence = stored
    elif decision and decision.get("status") == "rejected":
        evidence = dict(decision)
        evidence["rejected_display"] = logic.iso_display(decision.get("rejected_at"))

    preview = None
    if draft.get("ok") and not decision:
        preview = logic.evidence_preview(
            brief,
            draft,
            claims,
            version,
            default_audience["id"],
            audience_label,
        )
        preview["retention_display"] = logic.date_display(preview["retention_until"])
        preview["hash_short"] = preview["hash"][:12]

    required = logic.disclosure_index(fact_sheet)
    attached_ids = set(draft.get("disclosure_ids") or [])
    required_rows = []
    for item_id in brief.get("required_disclosures", []):
        item = required.get(item_id)
        if item:
            required_rows.append(
                {
                    "id": item_id,
                    "text": item["text"],
                    "attached": item_id in attached_ids,
                }
            )

    return {
        "id": brief["id"],
        "key": brief["key"],
        "title": brief["title"],
        "channel": brief["channel"],
        "cadence": brief.get("cadence", ""),
        "objective": brief.get("objective", ""),
        "key_points": brief.get("key_points", []),
        "must_use": brief.get("must_use", []),
        "tone": brief.get("tone", ""),
        "word_limit": brief.get("word_limit", ""),
        "seeded_risk": brief.get("seeded_risk", ""),
        "audience_type": brief.get("audience_type", ""),
        "status": status,
        "version": version,
        "revision": revision,
        "has_revision": bool(brief.get("revision_key")),
        "draft_key": brief.get("draft_key"),
        "claims_key": brief.get("claims_key"),
        "draft": {
            "ok": bool(draft.get("ok")),
            "missing": bool(draft.get("missing")),
            "error": draft.get("error", ""),
            "key": draft.get("key"),
            "headline": draft.get("headline", ""),
            "body": draft.get("body", ""),
            "word_count": draft.get("word_count", 0),
            "limit": _word_limit(brief, draft),
            "paragraphs": paragraphs,
            "disclosures": draft.get("disclosures", []),
            "provenance": draft.get("provenance"),
            "revision_note": draft.get("revision_note", ""),
        },
        "claims": claims,
        "claims_ok": bool(claims_result.get("ok")),
        "claims_error": claims_result.get("error", ""),
        "claims_provenance": claims_result.get("provenance"),
        "claims_risk": claims_result.get("risk", "unknown"),
        "policy": evaluation,
        "decision": decision,
        "evidence": evidence,
        "preview": preview,
        "audience_options": options,
        "default_audience": default_audience,
        "audience_label": audience_label,
        "audience_note": audience_note,
        "included": _household_rows(households, included_ids),
        "excluded": _household_rows(households, excluded_ids),
        "required_disclosures": required_rows,
        "facts": logic.brief_facts(brief, fact_sheet),
    }


def _eval_view() -> dict[str, Any] | None:
    report = data.eval_report()
    if not report:
        return None
    failures = report.get("failures") or []
    notes = report.get("failure_notes") or []
    return {
        "name": report.get("name", "Claim flag accuracy"),
        "cases": report.get("cases", 0),
        "accuracy": "{:.1f}%".format(float(report.get("accuracy") or 0) * 100),
        "precision": "{:.1f}%".format(float(report.get("precision") or 0) * 100),
        "recall": "{:.1f}%".format(float(report.get("recall") or 0) * 100),
        "f1": "{:.1f}%".format(float(report.get("f1") or 0) * 100),
        "exact": report.get("exact", 0),
        "flags": report.get("flags") or {},
        "source": report.get("source", ""),
        "model": report.get("model", ""),
        "recorded": logic.iso_display(report.get("recorded_at")),
        "failures": failures[:4],
        "notes": notes[:4],
        "by_label": report.get("by_label") or {},
    }


def _audit_rows() -> list[dict[str, Any]]:
    rows = []
    for row in audit.recent(MODULE, limit=14):
        rows.append(
            {
                "id": row["id"],
                "time": logic.iso_display(row["created_at"]),
                "actor": row["actor"],
                "action": ACTION_LABELS.get(
                    row["action"], str(row["action"]).replace(".", " ").capitalize()
                ),
                "entity": row["entity_id"],
                "detail": row["detail"],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


@router.get("")
def supervision_page(request: Request, brief: str = ""):
    bundle = data.briefs_bundle()
    fact_sheet = data.fact_sheet()
    households = data.households_by_id()
    briefs = bundle["briefs"]
    selected = _find_brief(briefs, brief) or briefs[0]

    _, decisions, revisions = _stored()
    views = [
        _brief_view(item, bundle, fact_sheet, decisions, revisions, households)
        for item in briefs
    ]
    view = next(item for item in views if item["id"] == selected["id"])

    approved = len([item for item in views if item["status"]["key"] == "approved"])
    blocked = len([item for item in views if item["status"]["key"] == "blocked"])
    eval_view = _eval_view()
    metric = logic.review_cycle_model()
    provenance = view["draft"].get("provenance") or view.get("claims_provenance")

    stats = [
        {"label": "Briefs", "value": str(len(briefs)), "sub": "one campaign each"},
        {
            "label": "Recorded calls",
            "value": str(data.recordings_present()),
            "sub": "drafts, claims, revision, eval",
        },
        {"label": "Blocked", "value": str(blocked), "sub": "policy gate, in code"},
        {"label": "Approved", "value": str(approved), "sub": "sealed evidence files"},
        {
            "label": "Eval accuracy",
            "value": eval_view["accuracy"] if eval_view else "Not run",
            "sub": "12 labelled claims",
        },
        {
            "label": "Audit events",
            "value": str(audit.count(MODULE)),
            "sub": "append only",
        },
    ]

    context = {
        "request": request,
        "active": MODULE,
        "flash": _flash(request),
        "bundle": bundle,
        "household_total": len(households),
        "fact_sheet": fact_sheet,
        "briefs": views,
        "selected": view,
        "stats": stats,
        "approvers": logic.approvals(),
        "eval": eval_view,
        "metric": metric,
        "audit_rows": _audit_rows(),
        "audit_count": audit.count(MODULE),
        "provenance": provenance,
        "disclosures": fact_sheet.get("disclosures", []),
        "system_generation": logic.SYSTEM_GENERATION,
        "system_classify": logic.SYSTEM_CLASSIFY,
        "today": datetime.now(timezone.utc),
    }
    return templates.TemplateResponse(
        request=request, name="modules/supervision/page.html", context=context
    )


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def _state_decision(payload: Any, brief_id: str, record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    decisions = payload.get("decisions")
    if not isinstance(decisions, dict):
        decisions = {}
    decisions[brief_id] = record
    payload["decisions"] = decisions
    if not isinstance(payload.get("revisions"), dict):
        payload["revisions"] = {}
    return payload


def _state_revision(payload: Any, brief_id: str, record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    revisions = payload.get("revisions")
    if not isinstance(revisions, dict):
        revisions = {}
    revisions[brief_id] = record
    payload["revisions"] = revisions
    if not isinstance(payload.get("decisions"), dict):
        payload["decisions"] = {}
    return payload


def _canonical_url(brief_id: str, key: str = "msg", value: str = "") -> str:
    if not value:
        return "/supervision?brief={brief}".format(brief=brief_id)
    return "/supervision?brief={brief}&{key}={value}".format(brief=brief_id, key=key, value=value)


@router.post("/approve")
async def approve(request: Request):
    form = await _form(request)
    briefs = data.briefs_bundle()["briefs"]
    brief = _find_brief(briefs, form.get("brief", ""))
    if not brief:
        return _redirect("/supervision?error=not_found")

    _, decisions, revisions = _stored()
    if brief["id"] in decisions:
        return _redirect(_canonical_url(brief["id"], "error", "decided"))

    approver = logic.find_approver(form.get("approver", ""))
    if approver is None:
        return _redirect(_canonical_url(brief["id"], "error", "approver"))
    if not approver["selectable"]:
        audit.record(
            MODULE,
            approver["name"],
            "brief.blocked",
            "marketing_brief",
            brief["id"],
            "Approval refused: {name} drafted this piece and self-review is not permitted.".format(
                name=approver["name"]
            ),
        )
        return _redirect(_canonical_url(brief["id"], "error", "self_review"))

    options = _audience_options(brief, data.briefs_bundle())
    option = next((item for item in options if item["id"] == form.get("audience", "")), None)
    if option is None:
        return _redirect(_canonical_url(brief["id"], "error", "audience"))
    if not option["selectable"]:
        audit.record(
            MODULE,
            approver["name"],
            "brief.blocked",
            "marketing_brief",
            brief["id"],
            "Approval refused: audience {audience} includes households without consent. {note}".format(
                audience=option["label"], note=option["note"]
            ),
        )
        return _redirect(_canonical_url(brief["id"], "error", "consent"))

    version = 2 if brief["id"] in revisions else 1
    draft = logic.load_draft(brief, version)
    claims_result = logic.load_claims(brief, data.fact_sheet(), version)
    evaluation = logic.evaluate(brief, draft, claims_result, data.fact_sheet())
    if not draft.get("ok") or not claims_result.get("ok"):
        return _redirect(_canonical_url(brief["id"], "error", "not_recorded"))
    if evaluation["blocked"]:
        detail = "Approval refused by the policy gate: " + "; ".join(
            check["name"] for check in evaluation["blocking"]
        )
        audit.record(MODULE, approver["name"], "brief.blocked", "marketing_brief", brief["id"], detail)
        return _redirect(_canonical_url(brief["id"], "error", "blocked"))

    evidence = logic.seal_evidence(
        brief,
        draft,
        claims_result["claims"],
        evaluation["checks"],
        version,
        approver,
        option["id"],
        option["label"],
        form.get("note", ""),
    )
    state.mutate(MODULE, lambda payload: _state_decision(payload, brief["id"], evidence), {})
    audit.record(
        MODULE,
        approver["name"],
        "brief.approved",
        "marketing_brief",
        brief["id"],
        "{role} approved version {version} for {audience}. Evidence {hash}, retention to {retention}.".format(
            role=approver["role"],
            version=version,
            audience=option["label"],
            hash=evidence["artifact_hash"][:12],
            retention=logic.date_display(evidence["retention_until"]),
        ),
    )
    return _redirect(_canonical_url(brief["id"], "msg", "approved"))


@router.post("/reject")
async def reject(request: Request):
    form = await _form(request)
    briefs = data.briefs_bundle()["briefs"]
    brief = _find_brief(briefs, form.get("brief", ""))
    if not brief:
        return _redirect("/supervision?error=not_found")

    _, decisions, revisions = _stored()
    if brief["id"] in decisions:
        return _redirect(_canonical_url(brief["id"], "error", "decided"))

    approver = logic.find_approver(form.get("approver", ""))
    if approver is None:
        return _redirect(_canonical_url(brief["id"], "error", "approver"))
    if not approver["selectable"]:
        return _redirect(_canonical_url(brief["id"], "error", "self_review"))

    reason = form.get("reason", "")
    if not reason:
        return _redirect(_canonical_url(brief["id"], "error", "reason"))

    version = 2 if brief["id"] in revisions else 1
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {
        "status": "rejected",
        "brief_id": brief["id"],
        "title": brief["title"],
        "approver": approver["name"],
        "role": approver["role"],
        "rejected_at": stamp,
        "version": version,
        "reason": reason,
    }
    state.mutate(MODULE, lambda payload: _state_decision(payload, brief["id"], record), {})
    audit.record(
        MODULE,
        approver["name"],
        "brief.rejected",
        "marketing_brief",
        brief["id"],
        "{role} rejected version {version}. Reason: {reason}".format(
            role=approver["role"], version=version, reason=reason
        ),
    )
    return _redirect(_canonical_url(brief["id"], "msg", "rejected"))


@router.post("/revise")
async def revise(request: Request):
    form = await _form(request)
    briefs = data.briefs_bundle()["briefs"]
    brief = _find_brief(briefs, form.get("brief", ""))
    if not brief:
        return _redirect("/supervision?error=not_found")

    _, decisions, revisions = _stored()
    if brief["id"] in decisions:
        return _redirect(_canonical_url(brief["id"], "error", "decided"))
    if brief["id"] in revisions:
        return _redirect(_canonical_url(brief["id"], "error", "decided"))

    approver = logic.find_approver(form.get("approver", ""))
    if approver is None:
        return _redirect(_canonical_url(brief["id"], "error", "approver"))
    if not approver["selectable"]:
        return _redirect(_canonical_url(brief["id"], "error", "self_review"))

    draft = logic.load_draft(brief, 1)
    claims_result = logic.load_claims(brief, data.fact_sheet(), 1)
    evaluation = logic.evaluate(brief, draft, claims_result, data.fact_sheet())
    if not evaluation["blocked"]:
        return _redirect(_canonical_url(brief["id"], "error", "not_blocked"))

    revision_key = brief.get("revision_key")
    revision_claims_key = brief.get("revision_claims_key")
    if not revision_key or not revision_claims_key:
        audit.record(
            MODULE,
            approver["name"],
            "brief.returned",
            "marketing_brief",
            brief["id"],
            "Returned to the adviser. No revision is recorded for this brief.",
        )
        return _redirect(_canonical_url(brief["id"], "msg", "returned"))

    revised = logic.load_draft(brief, 2)
    revised_claims = logic.load_claims(brief, data.fact_sheet(), 2)
    if not revised.get("ok") or not revised_claims.get("ok"):
        return _redirect(_canonical_url(brief["id"], "error", "not_recorded"))

    record = {
        "requested_by": approver["name"],
        "role": approver["role"],
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from_version": 1,
        "to_version": 2,
        "note": revised.get("revision_note", ""),
    }
    state.mutate(MODULE, lambda payload: _state_revision(payload, brief["id"], record), {})
    audit.record(
        MODULE,
        approver["name"],
        "brief.revision_applied",
        "marketing_brief",
        brief["id"],
        "{role} applied the recorded revision, version 1 to version 2. The claim classifier re-ran on the revised text.".format(
            role=approver["role"]
        ),
    )
    return _redirect(_canonical_url(brief["id"], "msg", "revised"))


@router.post("/return")
async def send_back(request: Request):
    form = await _form(request)
    briefs = data.briefs_bundle()["briefs"]
    brief = _find_brief(briefs, form.get("brief", ""))
    if not brief:
        return _redirect("/supervision?error=not_found")

    _, decisions, revisions = _stored()
    if brief["id"] in decisions:
        return _redirect(_canonical_url(brief["id"], "error", "decided"))

    approver = logic.find_approver(form.get("approver", ""))
    if approver is None:
        return _redirect(_canonical_url(brief["id"], "error", "approver"))
    if not approver["selectable"]:
        return _redirect(_canonical_url(brief["id"], "error", "self_review"))

    version = 2 if brief["id"] in revisions else 1
    draft = logic.load_draft(brief, version)
    claims_result = logic.load_claims(brief, data.fact_sheet(), version)
    evaluation = logic.evaluate(brief, draft, claims_result, data.fact_sheet())
    if not evaluation["blocked"]:
        return _redirect(_canonical_url(brief["id"], "error", "not_blocked"))

    audit.record(
        MODULE,
        approver["name"],
        "brief.returned",
        "marketing_brief",
        brief["id"],
        "{role} returned version {version} to the adviser. Failing checks: {checks}.".format(
            role=approver["role"],
            version=version,
            checks=", ".join(check["name"] for check in evaluation["blocking"]),
        ),
    )
    return _redirect(_canonical_url(brief["id"], "msg", "returned"))


@router.post("/reset")
def reset():
    state.reset(MODULE)
    audit.record(
        MODULE,
        "Demo operator",
        "demo.reset",
        "module",
        MODULE,
        "Demo state cleared. Recordings, data files, and prior audit events were not changed.",
    )
    return _redirect("/supervision?msg=reset")


@router.get("/evidence/{brief_id}.json")
def evidence_file(brief_id: str):
    _, decisions, _ = _stored()
    record = decisions.get(brief_id)
    if not record or record.get("status") != "approved":
        return JSONResponse(
            {"error": "No sealed evidence file for this brief.", "brief_id": brief_id},
            status_code=404,
        )
    return JSONResponse(record)
