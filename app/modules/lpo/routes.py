"""Routes for LP Ops, one page at /lpo.

Everything is server rendered. Forms post, redirect, and flash. The only model
call on a POST is the explicit Run extraction action; all other model output is
replayed from the recordings. Approvals and stage changes write demo state and
the shared audit table.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ...platform import audit, llm, nav, state
from . import data, logic

MODULE = "lpo"
router = APIRouter(prefix="/lpo", tags=["lpo"])

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["suite_modules"] = nav.SUITE_MODULES
templates.env.globals["suite_levers"] = nav.LEVERS
templates.env.filters["usd"] = logic.format_money
templates.env.filters["date_long"] = logic.format_date
templates.env.filters["datetime_short"] = logic.format_datetime

MESSAGES = {
    "extracted": "Extraction loaded from the recording. No new model call was made.",
    "extracted_live": "Extraction recorded live. The output is in the cache and the audit trail.",
    "approved": "Exception approved. The evidence record is written and the pipeline count is updated.",
    "advanced": "LP advanced. The stage change and its evidence record are written.",
    "note_approved": "LP status note approved and sealed. Nothing is sent by this sample.",
    "note_rejected": "LP status note rejected. The reason is in the evidence record and the audit trail.",
    "reset": "Demo state reset. Generated data files and model recordings were not changed.",
}

ERRORS = {
    "missing": "No recording for this packet yet. Run SAVVY_LLM_MODE=live python3 scripts/record_lpo.py.",
    "actor": "Select a named approver from the operations team.",
    "scope": "Select an open exception before approving.",
    "resolved": "There are no open exceptions for this packet.",
    "notfound": "That packet or LP was not found.",
    "consent": "This household's consent is pending. The server rejects the LP-facing approval.",
    "stage": "This LP cannot be advanced from its current stage.",
    "open": "Resolve every open exception before advancing this LP.",
    "funded": "Funding is recorded by the fund administrator, not by this desk.",
    "reason": "A rejection needs a reason of at least 8 characters.",
    "already": "A decision is already recorded for this note.",
}


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


def _back(packet_id: str = "", msg: str = "", error: str = "", ev: str = "", anchor: str = "intake") -> str:
    params: dict[str, str] = {}
    if packet_id:
        params["packet"] = packet_id
    if msg:
        params["msg"] = msg
    if error:
        params["error"] = error
    if ev:
        params["ev"] = ev
    base = "/lpo"
    if params:
        base = base + "?" + urlencode(params)
    if anchor:
        base = base + "#" + anchor
    return base


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1].strip() for key, values in parsed.items()}


def _flash(request: Request) -> dict[str, str]:
    query = request.query_params
    message = MESSAGES.get(query.get("msg", ""), "")
    evidence_id = query.get("ev", "")
    if evidence_id.startswith("EV-LPO-"):
        message = (message + " Evidence " + evidence_id + ".").strip()
    error = ERRORS.get(query.get("error", ""), "")
    if query.get("error") and not error:
        error = "The action could not be completed."
    return {"message": message, "error": error}


def _meta(result: llm.LLMResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    usage = result.usage or {}
    return {
        "key": result.key,
        "model": result.model,
        "mode": result.mode,
        "created_at": result.created_at,
        "created_at_label": logic.format_datetime(result.created_at),
        "latency_ms": result.latency_ms,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
    }


def _evidence_record(
    records: dict[str, Any],
    packet: dict[str, Any] | None,
    state_payload: dict[str, Any],
    *,
    kind: str,
    decision: str,
    scope: str,
    scope_label: str,
    resolved_kinds: list[str],
    actor: str,
    role: str,
    note: str,
    basis: str,
    lp_id: str = "",
    model_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state_payload["seq"] = int(state_payload.get("seq") or 0) + 1
    evidence_id = f"EV-LPO-{state_payload['seq']:04d}"
    fund = records.get("fund") or {}
    packet_id = str(packet.get("id")) if packet else ""
    record = {
        "id": evidence_id,
        "kind": kind,
        "decision": decision,
        "decision_label": "Approved" if decision == "approved" else "Rejected",
        "packet_id": packet_id,
        "lp_id": lp_id or (str(packet.get("lp_id")) if packet else ""),
        "scope": scope,
        "scope_label": scope_label,
        "resolved_kinds": resolved_kinds,
        "actor": actor,
        "role": role,
        "note": note,
        "basis": basis,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "packet_sha": str(packet.get("sha256")) if packet else "",
        "packet_sha_short": str(packet.get("sha_short")) if packet else "",
        "retention": f"{fund.get('retention_years', 7)} years from final close",
        "retention_until": str(fund.get("retention_until") or ""),
        "model": (model_meta or {}).get("model", ""),
        "recording_key": (model_meta or {}).get("key", ""),
        "recording_created_at": (model_meta or {}).get("created_at", ""),
    }
    return record


def _note_date_flag(
    packet: dict[str, Any],
    extraction: dict[str, Any] | None,
    note: dict[str, Any] | None,
    records: dict[str, Any],
) -> str:
    if not note or not note.get("ok"):
        return ""
    closing_value = extraction.get("fields", {}).get("closing_date") if extraction else None
    if closing_value in (None, logic.MISSING, "Not stated"):
        closing_value = (packet.get("expected_fields") or {}).get("closing_date")
    as_of = str((records.get("program") or {}).get("as_of") or "")
    if (
        closing_value
        and isinstance(closing_value, str)
        and len(closing_value) == 10
        and as_of
        and closing_value < as_of
        and "closing" in note.get("body", "").lower()
    ):
        return (
            "The packet closing date is " + logic.format_date(closing_value) + ", before the "
            + logic.format_date(as_of) + " as-of date. The draft still treats it as upcoming."
        )
    return ""


def _packets_with_labels(packet_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    consent_labels = {"received": "Received", "pending": "Pending", "not required": "Not required", "unknown": "Unknown"}
    for packet in packet_list:
        item = dict(packet)
        item["status_label"] = "Closed" if item.get("status") == "closed" else "In queue"
        item["consent_label"] = consent_labels.get(str(item.get("consent_status")), str(item.get("consent_status")))
        result.append(item)
    return result


def _lps_with_labels(records: dict[str, Any], packet_list: list[dict[str, Any]], state_payload: dict[str, Any]) -> list[dict[str, Any]]:
    lps = data.lps_with_state(records, packet_list, state_payload)
    labels = {"received": "Received", "pending": "Pending", "not required": "Not required", "unknown": "Unknown"}
    for lp in lps:
        lp["consent_label"] = labels.get(str(lp.get("consent_status")), str(lp.get("consent_status")))
    return lps


def _context(request: Request, stage: str, packet_id: str, lp_id: str) -> dict[str, Any]:
    records = data.load_records()
    packet_payload = data.load_packets()
    packet_list = _packets_with_labels(packet_payload.get("packets") or [])
    if not packet_list:
        raise RuntimeError("LP Ops has no packet data. Run scripts/generate_lpo.py.")
    state_payload = data.load_state()
    lps = _lps_with_labels(records, packet_list, state_payload)

    selected = next((item for item in packet_list if item.get("id") == packet_id), None)
    if selected is None and lp_id:
        selected = next((item for item in packet_list if item.get("lp_id") == lp_id), None)
    if selected is None:
        selected = packet_list[0]
    selected_lp = next((item for item in lps if item.get("id") == selected.get("lp_id")), lps[0])

    valid_stages = ["all"] + data.STAGE_ORDER
    stage_key = stage if stage in valid_stages else "all"
    visible_lps = lps if stage_key == "all" else [item for item in lps if item.get("stage") == stage_key]

    extraction_result = llm.recorded(MODULE, logic.extraction_key(selected["id"]))
    extraction = logic.parse_extraction(extraction_result.text) if extraction_result else None
    extraction_meta = _meta(extraction_result)
    if extraction is not None:
        extraction["confidence_pct"] = logic.confidence_pct(extraction.get("confidence"))

    checklist_result = llm.recorded(MODULE, logic.checklist_key(selected["id"]))
    checklist = logic.parse_checklist(checklist_result.text) if checklist_result else None
    checklist_meta = _meta(checklist_result)

    note_result = llm.recorded(MODULE, logic.note_key(selected["id"]))
    note = logic.parse_note(note_result.text) if note_result else None
    note_meta = _meta(note_result)
    note_flags = logic.note_flags(note.get("body", "")) if note else []
    note_decision = data.note_decision(state_payload, selected["id"])
    consent_status = data.household_consent(selected["household_id"])
    note_blocked = consent_status == "pending"
    note_date_flag = _note_date_flag(selected, extraction, note, records)

    field_rows = []
    for definition in records.get("field_definitions", []):
        key = str(definition.get("key"))
        value = extraction.get("fields", {}).get(key, logic.MISSING) if extraction else logic.MISSING
        evidence = logic.locate_evidence(selected.get("text", ""), key, value) if extraction and extraction.get("ok") else None
        field_rows.append(
            {
                "key": key,
                "label": definition.get("label", key),
                "value": value,
                "display": logic.display_value(key, value),
                "mono": key in logic.MONEY_FIELDS or key in logic.DATE_FIELDS,
                "evidence": evidence,
            }
        )

    resolved = data.resolved_for(state_payload, selected["id"])
    exception_view = logic.merge_exceptions(selected, extraction, resolved)
    exception_view["model_available"] = extraction is not None and extraction.get("ok", False)
    for row in exception_view["rows"]:
        if row.get("status") != "approved":
            continue
        match = None
        for item in state_payload.get("approvals", []):
            if item.get("packet_id") != selected["id"]:
                continue
            if row.get("kind") in (item.get("resolved_kinds") or []):
                match = item
                break
        if match:
            row["approved_by"] = match.get("actor", "")
            row["approved_at"] = match.get("created_at", "")
            row["evidence_id"] = match.get("id", "")

    open_exceptions = [
        row for row in exception_view["rows"] if row.get("origin") == "log" and row.get("status") == "open"
    ]
    latest_evidence = data.latest_approval(state_payload, selected["id"])
    evidence_recent = data.recent_approvals(state_payload, 6)

    summary = data.summary_with_state(records, lps, packet_list, state_payload)
    behind = [item for item in lps if int(item.get("outstanding") or 0) > 0]
    summary["lps_behind"] = len(behind)
    summary["behind_ids"] = ", ".join(str(item.get("id")) for item in behind)
    stage_map = data.stage_counts(lps)

    calendar = data.calendar(records)
    next_event = next((item for item in calendar if item.get("is_next")), calendar[0] if calendar else {})

    stats = data.model_stats()
    provenance = {
        "recorded": stats.get("count", 0) > 0,
        "count": stats.get("count", 0),
        "models": ", ".join(stats.get("models") or []) or "None",
        "model": (extraction_meta or {}).get("model") or stats.get("model") or "Not recorded",
        "mode": (extraction_meta or {}).get("mode") or "Not recorded",
        "created_at": (extraction_meta or {}).get("created_at", ""),
        "created_at_label": (extraction_meta or {}).get("created_at_label", ""),
        "latency_ms": (extraction_meta or {}).get("latency_ms", 0),
        "prompt_tokens": (extraction_meta or {}).get("prompt_tokens", 0),
        "completion_tokens": (extraction_meta or {}).get("completion_tokens", 0),
        "mean_latency_ms": stats.get("mean_latency_ms", 0),
        "prompt_tokens_total": stats.get("prompt_tokens", 0),
        "completion_tokens_total": stats.get("completion_tokens", 0),
    }

    next_stage = logic.stage_next(str(selected_lp.get("stage")))
    advance_allowed = str(selected_lp.get("stage")) in {"document_intake", "exception_review"}
    advance_next_label = records.get("stage_labels", {}).get(next_stage, "") if next_stage else ""

    return {
        "request": request,
        "active": MODULE,
        "module": data.module_meta(),
        "records": records,
        "program": records.get("program") or {},
        "fund": records.get("fund") or {},
        "summary": summary,
        "lps": lps,
        "visible_lps": visible_lps,
        "selected": selected,
        "selected_lp": selected_lp,
        "packets": packet_list,
        "team": records.get("team") or [],
        "stage": stage_key,
        "stage_counts": stage_map,
        "stage_order": data.STAGE_ORDER,
        "stage_labels": records.get("stage_labels") or {},
        "exception_labels": records.get("exception_labels") or {},
        "extraction": extraction,
        "extraction_meta": extraction_meta,
        "field_rows": field_rows,
        "exceptions": exception_view,
        "open_exceptions": open_exceptions,
        "checklist": checklist,
        "checklist_meta": checklist_meta,
        "note": note,
        "note_meta": note_meta,
        "note_flags": note_flags,
        "note_decision": note_decision,
        "note_blocked": note_blocked,
        "note_date_flag": note_date_flag,
        "consent_status": consent_status,
        "evidence": latest_evidence,
        "evidence_recent": evidence_recent,
        "audit": audit.recent(MODULE, 30),
        "audit_count": audit.count(MODULE),
        "eval": data.load_eval(),
        "provenance": provenance,
        "metric": records.get("metric") or {},
        "calendar": calendar,
        "next_event": next_event,
        "build_note": records.get("build_note") or {},
        "sources": records.get("sources") or {},
        "assumptions": records.get("assumptions") or [],
        "as_of": (records.get("program") or {}).get("as_of", ""),
        "flash": _flash(request),
        "advance_allowed": advance_allowed,
        "advance_next_label": advance_next_label,
    }


@router.get("")
def page(request: Request, stage: str = "all", packet: str = "", lp: str = ""):
    context = _context(request, stage, packet, lp)
    return templates.TemplateResponse(request=request, name="modules/lpo/index.html", context=context)


@router.post("/extract")
async def extract(request: Request):
    form = await _form(request)
    packet = data.packet_by_id(form.get("packet", ""))
    if packet is None:
        return _redirect(_back(error="notfound", anchor="fields"))
    try:
        result = logic.run_extraction(packet)
    except RuntimeError as exc:
        audit.record("lpo", "System", "extraction.failed", "packet", packet["id"], str(exc)[:200])
        return _redirect(_back(packet["id"], error="missing", anchor="fields"))
    if result.is_live:
        usage = result.usage or {}
        detail = (
            f"{result.model}, {result.latency_ms} ms, {usage.get('prompt_tokens', 0)} prompt + "
            f"{usage.get('completion_tokens', 0)} completion tokens"
        )
        audit.record("lpo", "System", "extraction.live", "packet", packet["id"], detail)
    message = "extracted_live" if result.is_live else "extracted"
    return _redirect(_back(packet["id"], msg=message, anchor="fields"))


@router.post("/approve")
async def approve(request: Request):
    form = await _form(request)
    packet = data.packet_by_id(form.get("packet", ""))
    if packet is None:
        return _redirect(_back(error="notfound", anchor="exceptions"))
    team = data.team()
    actor, role = logic.parse_actor(form.get("actor", ""), team)
    if not actor:
        return _redirect(_back(packet["id"], error="actor", anchor="exceptions"))
    records = data.load_records()
    state_payload = data.load_state()
    known = data.known_exceptions(packet)
    resolved = data.resolved_for(state_payload, packet["id"])
    open_kinds = [str(item.get("kind")) for item in known if item.get("kind") not in resolved]
    if not open_kinds:
        return _redirect(_back(packet["id"], error="resolved", anchor="exceptions"))
    scope = form.get("scope", "all").strip() or "all"
    if scope == "all":
        chosen = open_kinds
    elif scope in open_kinds:
        chosen = [scope]
    else:
        return _redirect(_back(packet["id"], error="scope", anchor="exceptions"))
    note = form.get("note", "")[:240]

    recording = llm.recorded(MODULE, logic.extraction_key(packet["id"]))
    extraction = logic.parse_extraction(recording.text) if recording else None
    merged = logic.merge_exceptions(packet, extraction, resolved)
    model_meta = _meta(recording)
    basis = (
        f"Administrator issue log for {packet['id']}. "
        f"{merged['matched']} of {merged['known_total']} seeded flags matched the recorded extraction. "
        f"Human approval is the decision of record."
    )
    scope_label = "All open exceptions" if len(chosen) > 1 else logic.KIND_LABELS.get(chosen[0], chosen[0])
    evidence = _evidence_record(
        records,
        packet,
        state_payload,
        kind="exception_approval",
        decision="approved",
        scope="all" if len(chosen) > 1 else chosen[0],
        scope_label=scope_label,
        resolved_kinds=chosen,
        actor=actor,
        role=role,
        note=note,
        basis=basis,
        model_meta=model_meta,
    )
    existing = state_payload["resolved"].setdefault(packet["id"], [])
    if not isinstance(existing, list):
        existing = []
    for kind in chosen:
        if kind not in existing:
            existing.append(kind)
    state_payload["resolved"][packet["id"]] = existing
    state_payload["approvals"].insert(0, evidence)
    data.save_state(state_payload)
    audit.record(
        "lpo",
        actor,
        "exception.approved",
        "packet",
        packet["id"],
        f"{role}. Approved {', '.join(chosen)} for {packet.get('lp_id')}. Evidence {evidence['id']}. {note}".strip(),
    )
    return _redirect(_back(packet["id"], msg="approved", ev=evidence["id"], anchor="evidence"))


@router.post("/advance")
async def advance(request: Request):
    form = await _form(request)
    records = data.load_records()
    lp_id = form.get("lp", "")
    lp = next((item for item in records.get("lps", []) if item.get("id") == lp_id), None)
    if lp is None:
        return _redirect(_back(error="notfound", anchor="review"))
    packet_list = data.packets()
    lp_packets = [item for item in packet_list if item.get("lp_id") == lp_id]
    current_packet = data.packet_by_id(form.get("packet", "")) or (lp_packets[0] if lp_packets else None)
    team = data.team()
    actor, role = logic.parse_actor(form.get("actor", ""), team)
    if not actor:
        return _redirect(_back(current_packet["id"] if current_packet else "", error="actor", anchor="review"))
    state_payload = data.load_state()
    stage = str(state_payload.get("stages", {}).get(lp_id) or lp.get("stage"))
    open_items: list[dict[str, Any]] = []
    for packet in lp_packets:
        open_items.extend(data.open_known_exceptions(packet, state_payload))
    note = form.get("note", "")[:240]
    if open_items:
        kinds = ", ".join(str(item.get("kind")) for item in open_items)
        suffix = "" if len(open_items) == 1 else "s"
        audit.record(
            "lpo",
            actor,
            "lp.advance_blocked",
            "lp",
            lp_id,
            f"{role}. {len(open_items)} open exception{suffix}: {kinds}. Note: {note}".strip(),
        )
        return _redirect(_back(current_packet["id"] if current_packet else "", error="open", anchor="review"))
    next_stage = logic.stage_next(stage)
    if not next_stage:
        return _redirect(_back(current_packet["id"] if current_packet else "", error=logic.stage_advance_error(stage), anchor="review"))
    evidence = _evidence_record(
        records,
        current_packet,
        state_payload,
        kind="stage_advance",
        decision="approved",
        scope=f"{stage}_to_{next_stage}",
        scope_label=f"{records.get('stage_labels', {}).get(stage, stage)} to {records.get('stage_labels', {}).get(next_stage, next_stage)}",
        resolved_kinds=[],
        actor=actor,
        role=role,
        note=note,
        basis=f"Stage gate for {lp_id}: 0 open exceptions across {len(lp_packets)} packet(s). Human approval is the decision of record.",
        lp_id=lp_id,
    )
    state_payload["stages"][lp_id] = next_stage
    state_payload["approvals"].insert(0, evidence)
    data.save_state(state_payload)
    audit.record(
        "lpo",
        actor,
        "lp.advanced",
        "lp",
        lp_id,
        f"{role}. {stage} to {next_stage}. Evidence {evidence['id']}. {note}".strip(),
    )
    return _redirect(_back(current_packet["id"] if current_packet else "", msg="advanced", ev=evidence["id"], anchor="evidence"))


@router.post("/note")
async def note_decision(request: Request):
    form = await _form(request)
    packet = data.packet_by_id(form.get("packet", ""))
    if packet is None:
        return _redirect(_back(error="notfound", anchor="note"))
    team = data.team()
    actor, role = logic.parse_actor(form.get("actor", ""), team)
    if not actor:
        return _redirect(_back(packet["id"], error="actor", anchor="note"))
    decision = form.get("decision", "")
    if decision not in {"approve", "reject"}:
        return _redirect(_back(packet["id"], error="notfound", anchor="note"))
    reason = form.get("reason", "")[:200]
    records = data.load_records()
    state_payload = data.load_state()
    if data.note_decision(state_payload, packet["id"]):
        return _redirect(_back(packet["id"], error="already", anchor="note"))
    consent_status = data.household_consent(packet["household_id"])
    if decision == "approve" and consent_status == "pending":
        audit.record(
            "lpo",
            actor,
            "note.blocked",
            "packet",
            packet["id"],
            f"{role}. Consent pending for {packet['household_id']}. Server rejected the approval attempt.",
        )
        return _redirect(_back(packet["id"], error="consent", anchor="note"))
    if decision == "reject" and len(reason) < 8:
        return _redirect(_back(packet["id"], error="reason", anchor="note"))
    recording = llm.recorded(MODULE, logic.note_key(packet["id"]))
    if recording is None:
        return _redirect(_back(packet["id"], error="missing", anchor="note"))
    note = logic.parse_note(recording.text)
    extraction_recording = llm.recorded(MODULE, logic.extraction_key(packet["id"]))
    extraction = logic.parse_extraction(extraction_recording.text) if extraction_recording else None
    date_flag = _note_date_flag(packet, extraction, note, records)
    meta = _meta(recording)
    basis_parts = [
        f"Household consent {consent_status}.",
        f"Recorded note {logic.note_key(packet['id'])}.",
        date_flag,
        "Human approval is the decision of record.",
    ]
    evidence = _evidence_record(
        records,
        packet,
        state_payload,
        kind="status_note",
        decision="approved" if decision == "approve" else "rejected",
        scope="lp_status_note",
        scope_label="LP status note",
        resolved_kinds=[],
        actor=actor,
        role=role,
        note=reason if decision == "reject" else "Client-facing status note approved for release. Nothing is sent by this sample.",
        basis=" ".join(part for part in basis_parts if part),
        model_meta=meta,
    )
    state_payload["notes"][packet["id"]] = {
        "decision": "approved" if decision == "approve" else "rejected",
        "actor": actor,
        "role": role,
        "at": evidence["created_at"],
        "reason": reason if decision == "reject" else "",
        "evidence_id": evidence["id"],
    }
    state_payload["approvals"].insert(0, evidence)
    data.save_state(state_payload)
    action = "note.approved" if decision == "approve" else "note.rejected"
    detail = f"{role}. {packet['id']} note {decision}d. Evidence {evidence['id']}."
    if reason:
        detail = detail + " " + reason
    audit.record("lpo", actor, action, "packet", packet["id"], detail.strip())
    message = "note_approved" if decision == "approve" else "note_rejected"
    return _redirect(_back(packet["id"], msg=message, ev=evidence["id"], anchor="evidence"))


@router.post("/reset")
def reset():
    state.reset(MODULE)
    audit.record(
        "lpo",
        "Operator",
        "state.reset",
        "module",
        MODULE,
        "Demo decisions cleared. Generated data and model recordings unchanged.",
    )
    return _redirect(_back(msg="reset", anchor="pipeline"))
