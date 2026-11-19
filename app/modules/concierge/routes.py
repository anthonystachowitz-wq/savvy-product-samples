"""Concierge routes. One page, four form actions, no JavaScript required.

GET  /concierge            the workspace: record, question box, answer, evidence
POST /concierge/ask        load a recorded answer, or make one fresh live call
POST /concierge/hold       hold the answer for adviser approval (state + audit)
POST /concierge/decision   approve or reject a held answer (evidence + audit)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.platform import audit, config as suite_config, llm, state
from app.platform.nav import SUITE_MODULES
from app.platform.rate_limit import BudgetExceeded
from app.modules.concierge import data, logic

MODULE = "concierge"
ADVISER = logic.ADVISER
APP_DIR = Path(__file__).resolve().parents[2]
ROLES = ["Financial adviser", "Supervising principal"]

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.globals["suite_modules"] = SUITE_MODULES

router = APIRouter(prefix="/concierge", tags=["concierge"])


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _short_ts(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return moment.strftime("%d %b %Y, %H:%M")


def _day(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return moment.strftime("%d %b %Y")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plus_years(start: date, years: int) -> str:
    try:
        shifted = start.replace(year=start.year + years)
    except ValueError:
        shifted = start.replace(year=start.year + years, day=28)
    return shifted.isoformat()


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


async def _form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1].strip() for key, values in parsed.items()}


def _state() -> dict[str, Any]:
    payload = state.load(MODULE, {})
    if not isinstance(payload, dict):
        payload = {}
    holds = payload.get("holds")
    if not isinstance(holds, list):
        holds = []
    payload["holds"] = holds
    return payload


def _next_id(holds: list[dict[str, Any]]) -> str:
    highest = 0
    for item in holds:
        identifier = str(item.get("id") or "")
        if identifier.startswith("CH-"):
            try:
                highest = max(highest, int(identifier[3:]))
            except ValueError:
                continue
    return "CH-" + str(highest + 1).zfill(4)


def _save_hold(hold: dict[str, Any]) -> str:
    def mutate(current: Any) -> Any:
        if not isinstance(current, dict):
            current = {}
        holds = current.get("holds")
        if not isinstance(holds, list):
            holds = []
        hold["id"] = _next_id(holds)
        holds.insert(0, hold)
        current["holds"] = holds[:25]
        return current

    payload = state.mutate(MODULE, mutate, default={})
    return hold["id"]


def _update_hold(hold_id: str, changes: dict[str, Any]) -> bool:
    found = {"ok": False}

    def mutate(current: Any) -> Any:
        if not isinstance(current, dict):
            return current
        for item in current.get("holds", []):
            if item.get("id") == hold_id:
                item.update(changes)
                found["ok"] = True
        return current

    state.mutate(MODULE, mutate, default={})
    return bool(found["ok"])


MESSAGES = {
    "asked": ("ok", "Loaded the recorded answer. Nothing has been sent."),
    "live_ok": ("ok", "A fresh live call was recorded and is shown above. The recorded set used by the eval is unchanged."),
    "cached_only": ("warn", "This deployment replays recordings only, so a live call is not available here."),
    "budget": ("warn", "The daily live-call budget for this deployment is used up. Try again tomorrow."),
    "live_failed": ("fail", "The live call did not complete, so nothing was recorded. The recorded answer is below."),
    "gap": ("warn", "No recording matches that question. The assistant does not invent an answer when the recorder has not run."),
    "noq": ("warn", "Pick a preset question or type a question first."),
    "held": ("ok", "Held for adviser approval. The record is at the top of the review queue and an audit event was written."),
    "already_held": ("warn", "That answer is already held and waiting for a decision."),
    "not_recorded": ("warn", "There is no recording for that question, so there is nothing to hold."),
    "missing": ("warn", "That record is not in the demo state any more. Reload the page and try again."),
    "role_required": ("warn", "Pick the approver role. A named role is required before an answer can be released."),
    "approved": ("ok", "Approved. The evidence record is attached to the answer and the decision is in the audit trail."),
    "rejected": ("warn", "Rejected. The note is in the audit trail and the answer cannot be released."),
    "blocked": ("fail", "Release blocked: Broker Protocol consent is pending for this household."),
    "check_failed": ("fail", "Approval blocked: the answer failed the citation or number check."),
    "unknown": ("warn", "That action is not recognised."),
}


def _flash(code: str) -> dict[str, str]:
    level, text = MESSAGES.get(code, MESSAGES["unknown"])
    return {"code": code, "level": level, "text": text}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def _recordings(keys: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = data.recordings()
    canonical = [item for item in rows if item["key"] in keys]
    extras = [item for item in rows if item["key"] not in keys]
    latencies = [item["latency_ms"] for item in canonical if item["latency_ms"]]
    models = sorted({item["model"] for item in canonical if item["model"]})
    return {
        "count": len(canonical),
        "expected": len(keys),
        "missing": max(0, len(keys) - len(canonical)),
        "models": ", ".join(models) if models else "none recorded",
        "mean_latency": int(round(sum(latencies) / len(latencies))) if latencies else 0,
        "tokens": sum(item["tokens"] for item in canonical),
        "latest": max((item["created_at"] for item in canonical if item["created_at"]), default=""),
        "extras": len(extras),
        "live": sum(1 for item in canonical if item["live"]),
    }


@router.get("")
def page(
    request: Request,
    h: str = "H-1042",
    q: str = "",
    live: int = 0,
    msg: str = "",
    qt: str = "",
    src: str = "",
) -> Any:
    records_payload = data.records()
    questions_payload = data.questions()
    households = records_payload.get("households", [])
    keys = data.key_index(questions_payload)
    qindex = data.question_index(questions_payload)

    household = data.household(records_payload, h) or (households[0] if households else None)
    key = q.strip()
    entry = keys.get(key)
    if entry:
        household = data.household(records_payload, entry["household_id"]) or household

    question_text = ""
    bundle: dict[str, Any] | None = None
    answer_error = ""
    live_variant = False
    consent_blocked = ""

    if household is not None:
        consent_blocked = logic.blocked_reason(household)

    if entry and household is not None:
        question_text = str(qindex.get(entry["question_id"], {}).get("text") or "")
        if live:
            try:
                bundle = logic.ask(household, question_text, key="live-" + key, audit_live=False)
                live_variant = True
            except llm.LLMUnavailable:
                answer_error = "The fresh call for this question is not in the cache. The recorded answer is shown instead."
        if bundle is None:
            try:
                bundle = logic.ask(household, question_text, key=key, audit_live=False)
            except llm.LLMUnavailable:
                answer_error = (
                    "No recording for this question. Run scripts/record_concierge.py to record it, "
                    "or use Ask live where the budget allows."
                )

    payload = _state()
    holds = payload["holds"]
    hold_for_key = next((item for item in holds if item.get("key") == key), None)
    hold_stats = {
        "total": len(holds),
        "held": sum(1 for item in holds if item.get("status") == "held"),
        "approved": sum(1 for item in holds if item.get("status") == "approved"),
        "rejected": sum(1 for item in holds if item.get("status") == "rejected"),
        "blocked": sum(1 for item in holds if item.get("status") == "held" and item.get("blocked_reason")),
    }

    metric = data.metric()
    eval_payload = data.eval_report()
    if eval_payload:
        accuracy = float(eval_payload.get("accuracy") or 0)
        eval_summary = {
            "available": True,
            "accuracy": accuracy,
            "percent": str(round(accuracy * 100, 1)),
            "passed": sum(1 for item in eval_payload.get("results", []) if item.get("passed")),
            "cases": int(eval_payload.get("cases") or 0),
            "results": eval_payload.get("results", []),
            "failure_notes": eval_payload.get("failure_notes", []),
            "rubric": eval_payload.get("rubric", ""),
        }
    else:
        eval_summary = {
            "available": False,
            "accuracy": 0.0,
            "percent": "n/a",
            "passed": 0,
            "cases": len(data.eval_cases().get("cases", [])),
            "results": [],
            "failure_notes": [],
            "rubric": data.eval_cases().get("rubric", ""),
        }

    audit_rows = audit.recent(MODULE, 24)
    availability = llm.availability()
    context = {
        "active": "concierge",
        "records": records_payload,
        "household": household,
        "households": households,
        "questions": questions_payload,
        "presets": data.presets(questions_payload),
        "keys": keys,
        "key_count": len(keys),
        "entry": entry,
        "key": key,
        "question_text": question_text,
        "bundle": bundle,
        "answer_error": answer_error,
        "live_variant": live_variant,
        "consent_blocked": consent_blocked,
        "hold_for_key": hold_for_key,
        "holds": holds[:8],
        "hold_stats": hold_stats,
        "audit": audit_rows,
        "audit_count": audit.count(MODULE),
        "flash": _flash(msg) if msg else None,
        "gap_text": qt[:160],
        "from_free_text": src == "free",
        "metric": metric,
        "eval": eval_summary,
        "recording": _recordings(keys),
        "availability": availability,
        "roles": ROLES,
        "roles_json": ROLES,
        "disclosure": records_payload.get("disclosure", ""),
        "as_of": records_payload.get("as_of", ""),
        "adviser": ADVISER,
        "system_prompt": logic.SYSTEM,
        "blocked_reason": consent_blocked,
        "evidence_preview": logic.evidence_preview(household, records_payload) if household else {},
    }
    return templates.TemplateResponse(request=request, name="modules/concierge/index.html", context=context)


# ---------------------------------------------------------------------------
# Ask
# ---------------------------------------------------------------------------


@router.post("/ask")
async def ask(request: Request) -> Any:
    form = await _form(request)
    household_id = form.get("household", "").strip()
    question_id = form.get("question_id", "").strip()
    free_text = form.get("free_text", "").strip()
    action = form.get("action", "ask").strip() or "ask"
    src = "preset"

    records_payload = data.records()
    questions_payload = data.questions()
    household = data.household(records_payload, household_id)
    qindex = data.question_index(questions_payload)

    if not question_id and free_text:
        routed = logic.route_free_text(free_text, questions_payload)
        question_id = str(routed["question_id"])
        src = "free"
        if not question_id:
            return _redirect("/concierge?h=" + quote(household_id) + "&msg=gap&qt=" + quote(free_text[:160]))

    if household is None or question_id not in qindex:
        return _redirect("/concierge?h=" + quote(household_id or "H-1042") + "&msg=noq")

    key = logic.question_key(question_id, household_id)
    question_text = str(qindex[question_id]["text"])
    if key not in data.key_index(questions_payload):
        return _redirect("/concierge?h=" + quote(household_id) + "&msg=noq")

    if action == "live":
        if suite_config.llm_mode() != "live":
            return _redirect("/concierge?h=" + quote(household_id) + "&q=" + quote(key) + "&msg=cached_only#answer")
        try:
            logic.ask(household, question_text, key="live-" + key, refresh=True)
        except BudgetExceeded:
            return _redirect("/concierge?h=" + quote(household_id) + "&q=" + quote(key) + "&msg=budget#answer")
        except llm.LLMUnavailable:
            return _redirect("/concierge?h=" + quote(household_id) + "&q=" + quote(key) + "&msg=live_failed#answer")
        return _redirect(
            "/concierge?h=" + quote(household_id) + "&q=" + quote(key) + "&live=1&msg=live_ok#answer"
        )

    suffix = "#answer"
    if src == "free":
        suffix = "&src=free#answer"
    return _redirect(
        "/concierge?h=" + quote(household_id) + "&q=" + quote(key) + "&msg=asked" + suffix
    )


# ---------------------------------------------------------------------------
# Hold and decide
# ---------------------------------------------------------------------------


@router.post("/hold")
async def hold(request: Request) -> Any:
    form = await _form(request)
    household_id = form.get("household", "").strip()
    key = form.get("key", "").strip()
    live = form.get("live", "") in {"1", "true", "on", "yes"}

    records_payload = data.records()
    questions_payload = data.questions()
    household = data.household(records_payload, household_id)
    keys = data.key_index(questions_payload)
    entry = keys.get(key)
    if household is None or entry is None or entry["household_id"] != household_id:
        return _redirect("/concierge?h=" + quote(household_id or "H-1042") + "&msg=missing")

    question_text = str(data.question_index(questions_payload).get(entry["question_id"], {}).get("text") or "")
    source_key = ("live-" + key) if live else key
    try:
        bundle = logic.ask(household, question_text, key=source_key, audit_live=False)
    except llm.LLMUnavailable:
        return _redirect("/concierge?h=" + quote(household_id) + "&q=" + quote(key) + "&msg=not_recorded#answer")

    payload = _state()
    duplicate = next(
        (item for item in payload["holds"] if item.get("key") == key and item.get("status") == "held"), None
    )
    if duplicate is not None:
        return _redirect("/concierge?h=" + quote(household_id) + "&q=" + quote(key) + "&msg=already_held#answer")

    reason = logic.blocked_reason(household)
    if not reason and not bundle["ok"]:
        reason = "The answer failed the citation or number check, so it cannot be released."

    record = {
        "id": "",
        "household_id": household["id"],
        "household_name": household["display_name"],
        "key": key,
        "source_key": source_key,
        "question": question_text,
        "intent": bundle["intent"],
        "answer": bundle["answer"],
        "citations": [
            {"field": item["field"], "label": item["label"], "value": item["value"], "status": item["status"]}
            for item in bundle["citations"]
        ],
        "guardrail": bundle["guardrail"],
        "route_to_advisor": bundle["route_to_advisor"],
        "provenance": bundle["provenance"],
        "status": "held",
        "held_by": ADVISER,
        "held_at": _now_iso(),
        "version": "v1",
        "hash": logic.bundle_hash(bundle),
        "blocked_reason": reason,
        "decision": None,
        "evidence": None,
    }
    identifier = _save_hold(record)
    detail = (
        household_id
        + " "
        + question_text
        + " | intent "
        + bundle["intent"]
        + " | check "
        + ("pass" if bundle["guardrail"]["ok"] else "fail")
        + " | source "
        + source_key
    )
    audit.record(MODULE, ADVISER, "answer_held", "answer", identifier, detail)
    return _redirect("/concierge?h=" + quote(household_id) + "&q=" + quote(key) + "&msg=held#answer")


@router.post("/decision")
async def decision(request: Request) -> Any:
    form = await _form(request)
    hold_id = form.get("hold_id", "").strip()
    action = form.get("action", "").strip()
    role = form.get("role", "").strip()
    note = form.get("note", "").strip()[:240]

    payload = _state()
    record = next((item for item in payload["holds"] if item.get("id") == hold_id), None)
    if record is None:
        return _redirect("/concierge?msg=missing#queue")

    back = "/concierge?h=" + quote(record["household_id"]) + "&q=" + quote(record["key"])

    if action == "reject":
        _update_hold(
            hold_id,
            {
                "status": "rejected",
                "decision": {
                    "actor": ADVISER,
                    "role": role or "Financial adviser",
                    "action": "reject",
                    "at": _now_iso(),
                    "note": note,
                },
            },
        )
        audit.record(
            MODULE,
            ADVISER,
            "answer_rejected",
            "answer",
            hold_id,
            record["household_id"] + " " + record["question"] + " | " + (note or "No reason given."),
        )
        return _redirect(back + "&msg=rejected#queue")

    if action != "approve":
        return _redirect(back + "&msg=unknown#queue")

    if role not in ROLES:
        return _redirect(back + "&msg=role_required#queue")

    household = data.household(data.records(), record["household_id"])
    reason = logic.blocked_reason(household) if household else "The household is not in the record."
    if reason:
        audit.record(MODULE, ADVISER, "release_blocked", "answer", hold_id, reason)
        return _redirect(back + "&msg=blocked#queue")

    check = record.get("guardrail") or {}
    if not check.get("ok"):
        audit.record(
            MODULE,
            ADVISER,
            "approval_blocked",
            "answer",
            hold_id,
            "The answer failed the citation or number check, so it cannot be released.",
        )
        return _redirect(back + "&msg=check_failed#queue")

    approved_at = _now_iso()
    evidence = {
        "approver": ADVISER,
        "role": role,
        "approved_at": approved_at,
        "version": "v1",
        "audience": "Client portal, household only",
        "channel": "Portal message (simulated, not sent)",
        "disclosure": data.records().get("disclosure", ""),
        "retention_until": _plus_years(date.today(), 5),
        "hash": record.get("hash", ""),
    }
    _update_hold(
        hold_id,
        {
            "status": "approved",
            "decision": {
                "actor": ADVISER,
                "role": role,
                "action": "approve",
                "at": approved_at,
                "note": note,
            },
            "evidence": evidence,
        },
    )
    audit.record(
        MODULE,
        ADVISER,
        "answer_approved",
        "answer",
        hold_id,
        record["household_id"]
        + " "
        + record["question"]
        + " | role "
        + role
        + " | evidence v1, retention to "
        + evidence["retention_until"]
        + " | nothing sent",
    )
    return _redirect(back + "&msg=approved#queue")


templates.env.filters["short_ts"] = _short_ts
templates.env.filters["day"] = _day
