"""Book IQ: plays, prompts, parsing, opportunity arithmetic, and guardrails.

Every number the page shows is either read from the seeded book, read from the
deterministic enrichment file, or computed here from a stated assumption. The
two model calls are the only non-deterministic input. Their output is generated
text: it is ranked next to the record, drafted into outreach, and held for a
named human approval. Nothing sends.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any, Optional

MODULE = "bookiq"
RANK_KEY = "rank-book-whitfield-v1"
PROMPT_VERSION = "v1"
FEE_RATE = 0.0090
AS_OF = "2026-11-19"


def draft_key(household_id: str) -> str:
    return f"draft-outreach-{household_id}-v1"

# ---------------------------------------------------------------------------
# Play catalogue
# ---------------------------------------------------------------------------

PLAYS: dict[str, dict[str, Any]] = {
    "consolidate_held_away": {
        "label": "Consolidate held-away assets",
        "short": "Consolidate held-away",
        "required_signal": "held_away",
        "input_field": "held_away_usd",
        "share": 0.25,
        "rule": "Assets in the record that sit at another firm. Priority rises with the share of household assets held away.",
        "basis": "25 percent of held-away assets, the modelled share that moves in the first year.",
    },
    "reinvest_cash": {
        "label": "Reinvest excess cash",
        "short": "Reinvest cash",
        "required_signal": "cash_drag",
        "input_field": "excess_cash_usd",
        "share": 1.0,
        "rule": "Cash above the 5 percent target that is not invested and has no auto-invest instruction.",
        "basis": "All cash above the 5 percent target, modelled as invested.",
    },
    "schedule_review": {
        "label": "Schedule an overdue review",
        "short": "Schedule review",
        "required_signal": "overdue_review",
        "input_field": "aum",
        "share": 0.025,
        "rule": "A review past its 365 day cadence, especially when the last meeting was over a year ago.",
        "basis": "AUM x 10 percent modelled at risk x 25 percent protected by a review.",
    },
    "rmd_plan": {
        "label": "Plan for a required distribution",
        "short": "RMD plan",
        "required_signal": "rmd_window",
        "input_field": "rmd_amount_usd",
        "share": 0.35,
        "rule": "A required minimum distribution due this calendar year with no plan recorded.",
        "basis": "35 percent of the estimated RMD, modelled as retained and reinvested.",
    },
    "reduce_concentration": {
        "label": "Reduce concentration",
        "short": "Reduce concentration",
        "required_signal": "concentration",
        "input_field": "restricted_usd",
        "share": 0.20,
        "rule": "One restricted or non-transferable holding above 25 percent of household assets.",
        "basis": "20 percent of the restricted position value, modelled as diversified over time.",
    },
    "referral": {
        "label": "Ask for a referral",
        "short": "Referral",
        "required_signal": "referral_source",
        "input_field": "aum",
        "share": 0.20,
        "rule": "A referral history and no ask logged in the last 12 months.",
        "basis": "One new household at 20 percent of the referrer's assets, the modelled size of a qualified introduction.",
    },
    "life_event": {
        "label": "Act on a life event",
        "short": "Life event",
        "required_signal": "life_event",
        "input_field": "life_event_value_usd",
        "share": 0.50,
        "rule": "A dated event in the next 12 months, such as retirement or an annuity election.",
        "basis": "50 percent of the assets tied to the event, modelled as repositioned.",
    },
    "no_action": {
        "label": "No action this cycle",
        "short": "No action",
        "required_signal": None,
        "input_field": None,
        "share": 0.0,
        "rule": "An open transfer or service item and no signal that supports a growth play this cycle.",
        "basis": "No growth play clears the record this cycle.",
    },
}

PLAY_ORDER = [
    "consolidate_held_away",
    "reinvest_cash",
    "schedule_review",
    "rmd_plan",
    "reduce_concentration",
    "referral",
    "life_event",
    "no_action",
]

SIGNAL_LABELS = {
    "held_away": "Held-away assets",
    "cash_drag": "Cash drag",
    "overdue_review": "Overdue review",
    "rmd_window": "RMD window",
    "concentration": "Concentration",
    "referral_source": "Referral source",
    "life_event": "Life event",
    "transfer_hold": "Transfer hold",
}

SIGNAL_RULES = {
    "held_away": "assets at another firm, with the balance and the custodian",
    "cash_drag": "cash above the 5 percent target, with the excess amount",
    "overdue_review": "last meeting older than 365 days, with the days since",
    "rmd_window": "a required minimum distribution due this calendar year",
    "concentration": "one restricted holding at 25 percent of assets or more",
    "referral_source": "a referral history and the date of the last ask",
    "life_event": "a dated event in the next 12 months and the assets tied to it",
    "transfer_hold": "the only open item is a transfer or service action",
}

PLAY_ALIASES = {
    "consolidate": "consolidate_held_away",
    "consolidate_held_away": "consolidate_held_away",
    "consolidate_held_away_assets": "consolidate_held_away",
    "consolidate_heldaway": "consolidate_held_away",
    "consolidate_held_away_asset": "consolidate_held_away",
    "held_away": "consolidate_held_away",
    "reinvest": "reinvest_cash",
    "reinvest_cash": "reinvest_cash",
    "reinvest_excess_cash": "reinvest_cash",
    "cash": "reinvest_cash",
    "schedule_review": "schedule_review",
    "review": "schedule_review",
    "schedule_an_overdue_review": "schedule_review",
    "overdue_review": "schedule_review",
    "rmd_plan": "rmd_plan",
    "rmd": "rmd_plan",
    "plan_for_rmd": "rmd_plan",
    "reduce_concentration": "reduce_concentration",
    "concentration": "reduce_concentration",
    "referral": "referral",
    "ask_for_referral": "referral",
    "life_event": "life_event",
    "act_on_a_life_event": "life_event",
    "no_action": "no_action",
    "none": "no_action",
    "hold": "no_action",
}

# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("USD", "").replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


def usd(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    if abs(number - round(number)) < 0.005:
        return "USD " + f"{int(round(number)):,}"
    return "USD " + f"{number:,.2f}"


def usd_short(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return "USD " + f"{int(round(number)):,}"


def share_pct(share: Any) -> str:
    number = _number(share)
    if number is None:
        return "n/a"
    value = number * 100
    if abs(value - round(value)) < 0.001:
        return f"{int(round(value))}%"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text + "%"


def display_pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{number:.1f}%"


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def date_long(value: Any) -> str:
    parsed = _as_date(value)
    if parsed is None:
        return "Not recorded"
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def date_short(value: Any) -> str:
    parsed = _as_date(value)
    if parsed is None:
        return "Not recorded"
    return f"{parsed.strftime('%b')} {parsed.day}"


def iso_or_empty(value: Any) -> str:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed else ""


def plays_label(value: Any) -> str:
    item = PLAYS.get(str(value or ""))
    return item["label"] if item else str(value or "").replace("_", " ").capitalize()


def plays_short(value: Any) -> str:
    item = PLAYS.get(str(value or ""))
    return item["short"] if item else str(value or "").replace("_", " ").capitalize()


def canonical_play(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if text in PLAYS:
        return text
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if slug in PLAY_ALIASES:
        return PLAY_ALIASES[slug]
    if slug in PLAYS:
        return slug
    return None


def confidence_label(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    if number >= 0.8:
        return "High"
    if number >= 0.6:
        return "Medium"
    return "Low"


def confidence_pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "n/a"
    return f"{int(round(number * 100))}%"


def strength_label(value: Any) -> str:
    text = str(value or "").lower()
    return {"high": "High", "medium": "Medium", "low": "Low"}.get(text, text.capitalize())


def timestamp_label(value: Any) -> str:
    if not value:
        return "Not recorded"
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return str(value)
    if parsed.tzinfo is not None:
        return parsed.strftime("%b %d, %Y, %H:%M UTC")
    return parsed.strftime("%b %d, %Y, %H:%M")


# ---------------------------------------------------------------------------
# Opportunity arithmetic
# ---------------------------------------------------------------------------


def _field_value(household: dict[str, Any], key: str) -> Any:
    field = (household.get("fields") or {}).get(key)
    if isinstance(field, dict):
        return field.get("value")
    return field


def signal_keys(household: dict[str, Any]) -> set[str]:
    return {str(item.get("key")) for item in household.get("signals") or [] if item.get("key")}


def opportunity(
    household: dict[str, Any],
    play_key: Any,
    fee_rate: float = FEE_RATE,
) -> dict[str, Any]:
    """Return the modelled asset opportunity and fee for one play.

    The play only applies when its required signal is in the household record.
    This stops the arithmetic from manufacturing an opportunity for a play the
    record does not support.
    """

    key = canonical_play(play_key)
    if key is None:
        return {
            "play": None,
            "assets_usd": 0,
            "fee_usd": 0,
            "applicable": False,
            "input_field": None,
            "input_usd": 0,
            "share": 0.0,
            "formula": "The model returned a play that is not in the catalogue.",
            "note": "Unknown play. No opportunity is modelled.",
        }
    play = PLAYS[key]
    if key == "no_action":
        return {
            "play": key,
            "label": play["label"],
            "assets_usd": 0,
            "fee_usd": 0,
            "applicable": True,
            "input_field": None,
            "input_usd": 0,
            "share": 0.0,
            "formula": "No growth play this cycle. Modelled opportunity USD 0.",
            "note": play["basis"],
        }

    required = play.get("required_signal")
    if required and required not in signal_keys(household):
        return {
            "play": key,
            "label": play["label"],
            "assets_usd": 0,
            "fee_usd": 0,
            "applicable": False,
            "input_field": play.get("input_field"),
            "input_usd": 0,
            "share": play.get("share", 0.0),
            "formula": "No " + SIGNAL_LABELS.get(str(required), str(required)).lower() + " signal in the record.",
            "note": "The play needs a signal this household does not have. Modelled at USD 0.",
        }

    input_field = str(play.get("input_field") or "")
    input_value = _number(_field_value(household, input_field)) or 0.0
    if input_value <= 0:
        label = (household.get("fields", {}).get(input_field) or {}).get("label") or input_field
        return {
            "play": key,
            "label": play["label"],
            "assets_usd": 0,
            "fee_usd": 0,
            "applicable": False,
            "input_field": input_field,
            "input_usd": 0,
            "share": play.get("share", 0.0),
            "formula": f"No {label.lower()} in the record.",
            "note": "The play has no source value in this household record. Modelled at USD 0.",
        }

    share = float(play.get("share") or 0.0)
    assets = int(round(input_value * share))
    fee = int(round(assets * fee_rate))
    formula = f"{usd(input_value)} x {share_pct(share)} = {usd(assets)}"
    return {
        "play": key,
        "label": play["label"],
        "assets_usd": assets,
        "fee_usd": fee,
        "applicable": True,
        "input_field": input_field,
        "input_usd": int(round(input_value)),
        "share": share,
        "formula": formula,
        "basis": play["basis"],
        "note": play["basis"],
    }


def rule_play(household: dict[str, Any], fee_rate: float = FEE_RATE) -> dict[str, Any]:
    """Deterministic fallback. It picks the largest modelled opportunity."""

    best_key = "no_action"
    best = opportunity(household, "no_action", fee_rate)
    for key in PLAY_ORDER:
        if key == "no_action":
            continue
        candidate = opportunity(household, key, fee_rate)
        if candidate["assets_usd"] > best["assets_usd"]:
            best = candidate
            best_key = key
    basis = PLAYS[best_key]["basis"]
    return {
        "play": best_key,
        "confidence": None,
        "reason": f"Rule check: {basis}",
        "signal": (best.get("input_field") and _signal_for_play(household, best_key)) or None,
        "opportunity": opportunity(household, best_key, fee_rate),
    }


def _signal_for_play(household: dict[str, Any], play_key: str) -> Optional[str]:
    required = PLAYS.get(play_key, {}).get("required_signal")
    if required and required in signal_keys(household):
        return required
    return None


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_RANK = (
    "You are a research assistant inside the growth team of a US registered investment adviser. "
    "You rank a synthetic book of households for an adviser's next-best-action queue. "
    "You are not the adviser. You do not give advice to clients, and you do not contact anyone. "
    "You choose one play per household from a fixed list, using only the fields and signals supplied. "
    "Return strict JSON only. No prose, no markdown, no commentary."
)

SYSTEM_DRAFT = (
    "You draft adviser outreach for a US registered investment adviser. "
    "A named human reviews every draft before anything could be sent, and nothing is sent from this system. "
    "You use only the facts supplied. You never promise or imply investment performance, never quote a fee, "
    "and never recommend a specific security or transaction. "
    "Return strict JSON only. No prose, no markdown, no commentary."
)


def _ranking_record(household: dict[str, Any]) -> dict[str, Any]:
    return {
        "household_id": household["id"],
        "name": household["name"],
        "aum_usd": household["aum"],
        "relationship": household["relationship"],
        "consent_status": household["consent_status"],
        "accounts": [
            f"{account['type']} at {account['custodian']} ({account['status']})"
            for account in household.get("accounts") or []
        ],
        "signals": [
            {
                "key": signal["key"],
                "strength": signal["strength"],
                "value": signal["display"],
                "detail": signal.get("detail", ""),
            }
            for signal in household.get("signals") or []
        ],
    }


def build_rank_prompt(households_payload: dict[str, Any]) -> tuple[str, str]:
    households = households_payload["households"]
    lines: list[str] = []
    lines.append(
        f"Rank all {len(households)} households for the adviser's next-best-action queue. "
        "The queue is sorted by modelled asset opportunity, but your job is judgment: choose the play the record supports."
    )
    lines.append("")
    lines.append("PLAYS (choose exactly one key per household):")
    for key in PLAY_ORDER:
        play = PLAYS[key]
        lines.append(f"- {key}: {play['rule']}")
    lines.append("")
    lines.append("SIGNALS (a signal exists only when it appears in the household record):")
    for key, rule in SIGNAL_RULES.items():
        lines.append(f"- {key}: {rule}")
    lines.append("")
    lines.append("HOUSEHOLDS:")
    lines.append(json.dumps([_ranking_record(h) for h in households], indent=1, ensure_ascii=False))
    lines.append("")
    lines.append(
        "Return a JSON array of exactly "
        f"{len(households)} objects, one per household, in any order. "
        "Each object has exactly these keys:"
    )
    lines.append("- household_id: the id, for example H-1042")
    lines.append("- play: one key from the play list")
    lines.append("- confidence: a number from 0 to 1, your own estimate")
    lines.append(
        "- reason: one sentence under 30 words that cites a figure from that household's record"
    )
    lines.append("- signal: the exact key of the household signal that drove the choice")
    lines.append(
        "If a household has an open transfer and no signal that supports a growth play, choose no_action. "
        "Do not invent signals. Return the array only."
    )
    return SYSTEM_RANK, "\n".join(lines)


def _draft_fact_lines(household: dict[str, Any]) -> list[str]:
    order = [
        "aum",
        "account_count",
        "client_since_year",
        "top_holding_name",
        "top_holding_pct",
        "cash_usd",
        "cash_pct",
        "excess_cash_usd",
        "held_away_usd",
        "held_away_custodian",
        "last_meeting_date",
        "days_since_meeting",
        "next_review_date",
        "referral_count",
        "referral_last_ask",
        "rmd_age",
        "rmd_amount_usd",
        "rmd_due_date",
        "restricted_holding",
        "restricted_usd",
        "restricted_pct",
        "restricted_until",
        "life_event_type",
        "life_event_date",
        "life_event_value_usd",
    ]
    lines: list[str] = []
    for key in order:
        field = (household.get("fields") or {}).get(key)
        if not isinstance(field, dict):
            continue
        value = field.get("value")
        if value in (None, "", 0, 0.0):
            continue
        lines.append(f"- {key}: {field.get('display')} ({field.get('label')})")
    return lines


def build_draft_prompt(
    household: dict[str, Any],
    play_key: str,
    signal: str = "",
) -> tuple[str, str]:
    play = PLAYS.get(play_key) or PLAYS["no_action"]
    lines: list[str] = []
    lines.append("Draft one adviser outreach email for human review.")
    lines.append("")
    lines.append("ADVISER: Dana Whitfield. Sign the email as Dana Whitfield, your adviser.")
    lines.append(f"HOUSEHOLD: {household['name']} ({household['id']}).")
    lines.append(f"RELATIONSHIP: {household['relationship']}.")
    lines.append(f"CONSENT STATUS: {household['consent_status']}. The draft is held for approval and is not sent.")
    lines.append("")
    lines.append(f"PLAY: {play['label']}.")
    if signal:
        lines.append(f"SIGNAL CITED BY THE RANKING: {signal}.")
    lines.append("")
    lines.append(
        "The internal ranking reason and the opportunity arithmetic stay out of the email. "
        "Do not quote a share, a fee, a target, or a modelled amount."
    )
    lines.append("")
    lines.append("FACTS (use only these values; copy numbers exactly as written):")
    lines.extend(_draft_fact_lines(household))
    lines.append("")
    lines.append("ACCOUNTS:")
    for account in household.get("accounts") or []:
        lines.append(
            f"- {account['type']} at {account['custodian']}, status {account['status']}"
        )
    lines.append("")
    lines.append("RULES:")
    lines.append("- The email is from Dana Whitfield to the household. Never address it to Dana.")
    lines.append("- Subject line under 70 characters. Body 90 to 140 words, plain and direct.")
    lines.append("- First person. No hype adjectives. No em-dashes. No exclamation marks.")
    lines.append("- Every number in the subject and body must be copied exactly from the facts above. Do not compute, round, or combine numbers.")
    lines.append("- No performance promises, no return figures, no words like guaranteed or risk-free.")
    lines.append("- Do not mention our fee, our revenue, or the modelled opportunity.")
    lines.append("- Do not recommend a specific security or a transaction. A review or a conversation is the call to action.")
    lines.append("- Do not claim that anything was sent, filed, or scheduled.")
    if play_key == "no_action":
        lines.append("- This is a service note, not a growth pitch. Confirm the open item and say that no action is needed from the household this month.")
    if household.get("consent_status") == "pending":
        lines.append("- Consent is pending for this household. The draft will be blocked from approval. Do not imply that contact has happened.")
    lines.append("- Claims: 2 to 5 items. Each claim text must appear in the body. source_field must be one of the field keys above.")
    lines.append("")
    lines.append("Return JSON only:")
    lines.append('{"subject": "...", "body": "...", "claims": [{"text": "...", "source_field": "..."}]}')
    return SYSTEM_DRAFT, "\n".join(lines)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _strip_to_json(text: str, opener: str, closer: str) -> Optional[str]:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^~~~(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*~~~$", "", cleaned)
    start = cleaned.find(opener)
    end = cleaned.rfind(closer)
    if start == -1 or end == -1 or end <= start:
        return None
    return cleaned[start : end + 1]


def parse_ranking(text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse the batched ranking. Always returns the rows that were usable."""

    meta: dict[str, Any] = {"error": "", "raw": text or "", "rows": 0, "unknown_households": []}
    snippet = _strip_to_json(text or "", "[", "]")
    if snippet is None:
        meta["error"] = "No JSON array found in the model response."
        return [], meta
    try:
        payload = json.loads(snippet)
    except ValueError as exc:
        meta["error"] = f"JSON parse failed: {exc}"
        return [], meta
    if isinstance(payload, dict):
        for key in ("rankings", "households", "rows", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        meta["error"] = "The model response was JSON but not an array."
        return [], meta
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        household_id = str(item.get("household_id") or item.get("id") or "").strip()
        if not household_id or household_id in seen:
            continue
        play = canonical_play(item.get("play"))
        confidence = _number(item.get("confidence"))
        if confidence is None:
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        rows.append(
            {
                "household_id": household_id,
                "play": play,
                "play_raw": str(item.get("play") or ""),
                "confidence": confidence,
                "reason": str(item.get("reason") or "").strip(),
                "signal": str(item.get("signal") or "").strip(),
            }
        )
        seen.add(household_id)
    meta["rows"] = len(rows)
    if not rows:
        meta["error"] = "The array contained no usable household rows."
    return rows, meta


def parse_draft(text: str) -> tuple[Optional[dict[str, Any]], str]:
    snippet = _strip_to_json(text or "", "{", "}")
    if snippet is None:
        return None, "No JSON object found in the model response."
    try:
        payload = json.loads(snippet)
    except ValueError as exc:
        return None, f"JSON parse failed: {exc}"
    if not isinstance(payload, dict):
        return None, "The model response was JSON but not an object."
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not subject or not body:
        return None, "The draft is missing a subject or a body."
    claims: list[dict[str, str]] = []
    raw_claims = payload.get("claims")
    if isinstance(raw_claims, list):
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("text") or "").strip()
            source = str(item.get("source_field") or "").strip()
            if claim:
                claims.append({"text": claim, "source_field": source})
    return {"subject": subject, "body": body, "claims": claims}, ""


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w])")

PERFORMANCE_TERMS = [
    "guarantee",
    "guaranteed",
    "outperform",
    "outperforms",
    "beat the market",
    "beats the market",
    "risk-free",
    "risk free",
    "no downside",
    "assured return",
    "promised return",
    "annualized return",
    "market-beating",
    "double your",
    "safe return",
    "certain to grow",
    "will grow",
    "will return",
    "we promise",
    "i promise",
]

ADVICE_TERMS = [
    "you should buy",
    "you should sell",
    "i recommend buying",
    "i recommend selling",
    "we recommend buying",
    "we recommend selling",
    "buy the",
    "sell the",
    "invest in the",
]


def _normalize_number(token: str) -> str:
    text = token.replace(",", "").strip()
    try:
        if "." in text:
            value = float(text)
            return f"{value:.6f}".rstrip("0").rstrip(".")
        return str(int(text))
    except ValueError:
        return text


def allowed_numbers(household: dict[str, Any]) -> dict[str, str]:
    """Map every value in the household record to the field that holds it."""

    out: dict[str, str] = {}
    for key, field in (household.get("fields") or {}).items():
        if not isinstance(field, dict):
            continue
        value = field.get("value")
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            out[_normalize_number(str(value))] = key
        elif isinstance(value, str):
            for year in re.findall(r"\b(?:19|20)\d{2}\b", value):
                out[year] = key
            parsed = _as_date(value)
            if parsed is not None:
                out[str(parsed.day)] = key
                out[str(parsed.year)] = key
    # The display forms add the exact strings a draft is most likely to quote.
    for key, field in (household.get("fields") or {}).items():
        if not isinstance(field, dict):
            continue
        for token in NUMBER_RE.findall(str(field.get("display") or "")):
            out.setdefault(_normalize_number(token), key)
    return out


def trace_numbers(text: str, household: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = allowed_numbers(household)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in NUMBER_RE.finditer(text or ""):
        token = match.group(0)
        normalized = _normalize_number(token)
        source = allowed.get(normalized)
        key = (token, source or "")
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "number": token,
                "normalized": normalized,
                "field": source or "",
                "label": (household.get("fields", {}).get(source) or {}).get("label", "") if source else "",
                "status": "traced" if source else "untraced",
            }
        )
    return rows


FIELD_ALIASES = {
    "accounts": "account_count",
    "account": "account_count",
    "cash": "cash_usd",
    "aum_usd": "aum",
    "rmd": "rmd_amount_usd",
    "last_meeting": "last_meeting_date",
    "next_review": "next_review_date",
    "held_away": "held_away_usd",
}


def verify_claims(claims: list[dict[str, str]], household: dict[str, Any], body: str) -> list[dict[str, Any]]:
    fields = household.get("fields") or {}
    body_norm = re.sub(r"\s+", " ", (body or "").lower())
    rows: list[dict[str, Any]] = []
    for claim in claims or []:
        text = str(claim.get("text") or "").strip()
        source_raw = str(claim.get("source_field") or "").strip()
        source = FIELD_ALIASES.get(source_raw, source_raw)
        field = fields.get(source) if source else None
        row = {
            "text": text,
            "source_field": source,
            "source_raw": source_raw,
            "source_label": field.get("label") if isinstance(field, dict) else "",
            "source_value": field.get("display") if isinstance(field, dict) else "",
            "status": "unverified",
            "note": "",
        }
        if not isinstance(field, dict):
            row["status"] = "unknown_field"
            row["note"] = "The cited field is not in the household record."
            rows.append(row)
            continue
        value = field.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            tokens = NUMBER_RE.findall(text)
            if not tokens:
                row["status"] = "no_number"
                row["note"] = "The claim has no number to compare with the source field."
            elif any(_normalize_number(token) == _normalize_number(str(value)) for token in tokens):
                row["status"] = "verified"
                row["note"] = "Number matches the record."
            else:
                row["status"] = "mismatch"
                row["note"] = "The claim number does not match the record value."
        else:
            display = str(field.get("display") or "")
            candidates = [str(value), display]
            parsed = _as_date(value)
            if parsed is not None:
                candidates.extend([parsed.strftime("%B"), parsed.strftime("%b"), parsed.isoformat()])
            if any(candidate and str(candidate).lower() in text.lower() for candidate in candidates):
                row["status"] = "verified"
                row["note"] = "Text matches the record."
            else:
                row["status"] = "unverified"
                row["note"] = "The claim text does not quote the record value."
        if text and re.sub(r"\s+", " ", text.lower()) not in body_norm:
            if row["status"] in {"unknown_field", "mismatch"}:
                row["status"] = "stale"
                row["note"] = "The claim text is not in the current draft, so it does not block approval."
            else:
                row["note"] = (row["note"] + " " if row["note"] else "") + "The claim is a paraphrase, not an exact quote."
        rows.append(row)
    return rows


def policy_flags(text: str) -> list[dict[str, str]]:
    lowered = (text or "").lower()
    flags: list[dict[str, str]] = []
    for term in PERFORMANCE_TERMS:
        if term in lowered:
            flags.append({"term": term, "category": "performance", "severity": "fail"})
    for term in ADVICE_TERMS:
        if term in lowered:
            flags.append({"term": term, "category": "advice", "severity": "warn"})
    return flags


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’]+\b", text or ""))


def guardrail(draft: dict[str, Any], household: dict[str, Any]) -> dict[str, Any]:
    subject = str(draft.get("subject") or "")
    body = str(draft.get("body") or "")
    text = subject + "\n" + body
    trace = trace_numbers(text, household)
    untraced = [row for row in trace if row["status"] == "untraced"]
    claims = verify_claims(draft.get("claims") or [], household, body)
    flags = policy_flags(text)
    words = word_count(body)
    checks: list[dict[str, str]] = []

    if untraced:
        checks.append(
            {
                "key": "numbers",
                "label": "Every number traces to a field",
                "status": "fail",
                "detail": f"{len(untraced)} of {len(trace)} numbers do not match a household field: "
                + ", ".join(row["number"] for row in untraced[:6]),
            }
        )
    else:
        checks.append(
            {
                "key": "numbers",
                "label": "Every number traces to a field",
                "status": "pass",
                "detail": f"{len(trace)} numbers checked against the household record.",
            }
        )

    bad_claims = [row for row in claims if row["status"] in {"unknown_field", "mismatch"}]
    if bad_claims:
        checks.append(
            {
                "key": "claims",
                "label": "Claims cite a real field and match it",
                "status": "fail",
                "detail": f"{len(bad_claims)} claims cite a missing field or a different value.",
            }
        )
    elif claims:
        verified = [row for row in claims if row["status"] == "verified"]
        checks.append(
            {
                "key": "claims",
                "label": "Claims cite a real field and match it",
                "status": "pass",
                "detail": f"{len(verified)} of {len(claims)} claims verified against the record.",
            }
        )
    else:
        checks.append(
            {
                "key": "claims",
                "label": "Claims cite a real field and match it",
                "status": "warn",
                "detail": "The draft returned no claims.",
            }
        )

    failing_flags = [flag for flag in flags if flag["severity"] == "fail"]
    if failing_flags:
        checks.append(
            {
                "key": "performance",
                "label": "No performance promise",
                "status": "fail",
                "detail": "Blocked terms: " + ", ".join(sorted({flag["term"] for flag in failing_flags})),
            }
        )
    else:
        checks.append(
            {
                "key": "performance",
                "label": "No performance promise",
                "status": "pass",
                "detail": "No promise or performance language found.",
            }
        )

    advice = [flag for flag in flags if flag["category"] == "advice"]
    checks.append(
        {
            "key": "advice",
            "label": "No product recommendation",
            "status": "warn" if advice else "pass",
            "detail": (
                "Possible recommendation language: " + ", ".join(sorted({flag["term"] for flag in advice}))
                if advice
                else "No recommendation language found."
            ),
        }
    )

    if 40 <= words <= 220:
        checks.append(
            {
                "key": "length",
                "label": "Length in range",
                "status": "pass",
                "detail": f"{words} words.",
            }
        )
    else:
        checks.append(
            {
                "key": "length",
                "label": "Length in range",
                "status": "warn",
                "detail": f"{words} words. The target is 40 to 220.",
            }
        )

    if subject:
        checks.append(
            {
                "key": "subject",
                "label": "Subject line present",
                "status": "pass",
                "detail": f"{len(subject)} characters.",
            }
        )
    else:
        checks.append(
            {
                "key": "subject",
                "label": "Subject line present",
                "status": "fail",
                "detail": "The draft has no subject.",
            }
        )

    blocking = [check for check in checks if check["status"] == "fail"]
    return {
        "ok": not blocking,
        "checks": checks,
        "claims": claims,
        "trace": trace,
        "untraced": untraced,
        "flags": flags,
        "blocking": blocking,
        "words": words,
    }


def approval_block(household: dict[str, Any], draft: Optional[dict[str, Any]], report: Optional[dict[str, Any]]) -> Optional[str]:
    """Return the reason approval is blocked, or None when a human may approve."""

    if draft is None:
        return "nodraft"
    if str(household.get("consent_status") or "").lower() == "pending":
        return "consent"
    if report is None or not report.get("ok"):
        return "guardrail"
    return None


def draft_hash(draft: dict[str, Any]) -> str:
    text = str(draft.get("subject") or "") + "\n" + str(draft.get("body") or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def make_evidence(
    household: dict[str, Any],
    draft: dict[str, Any],
    report: dict[str, Any],
    approver: str,
    role: str,
    note: str,
    model: str,
    mode: str,
    cache_key: str,
    sequence: int,
    decided_at: Optional[datetime] = None,
    version: int = 1,
) -> dict[str, Any]:
    decided = decided_at or datetime.utcnow()
    retention_year = decided.year + 5
    try:
        retention = decided.replace(year=retention_year).date().isoformat()
    except ValueError:
        retention = decided.replace(year=retention_year, day=28).date().isoformat()
    verified = len([row for row in report["claims"] if row["status"] == "verified"])
    traced = len([row for row in report["trace"] if row["status"] == "traced"])
    return {
        "id": f"EV-BIQ-{household['id']}-{sequence:02d}",
        "approver": approver,
        "role": role,
        "approved_at": decided.isoformat(timespec="seconds"),
        "retention_until": retention,
        "version": version,
        "household_id": household["id"],
        "household_name": household["name"],
        "audience": "Client",
        "channel": "Email draft, not sent",
        "model": model,
        "model_mode": mode,
        "cache_key": cache_key,
        "draft_hash": draft_hash(draft),
        "claims_verified": verified,
        "claims_total": len(report["claims"]),
        "numbers_traced": traced,
        "numbers_total": len(report["trace"]),
        "checks_passed": len([check for check in report["checks"] if check["status"] != "fail"]),
        "checks_total": len(report["checks"]),
        "note": note,
    }


# ---------------------------------------------------------------------------
# View assembly
# ---------------------------------------------------------------------------


def build_rows(
    households: list[dict[str, Any]],
    ranking_rows: list[dict[str, Any]],
    state: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Merge model ranking, rule fallback, and demo state into table rows."""

    state = state or {}
    decisions = state.get("households") or {}
    by_id = {}
    for row in ranking_rows:
        if row.get("play"):
            by_id[row["household_id"]] = row
    rows: list[dict[str, Any]] = []
    for household in households:
        decision = decisions.get(household["id"]) or {}
        model_row = by_id.get(household["id"])
        rule = rule_play(household)
        if model_row:
            play_key = model_row["play"]
            source = "model"
            confidence = model_row["confidence"]
            reason = model_row["reason"]
            signal = model_row["signal"]
            model_play = model_row["play"]
        else:
            play_key = rule["play"]
            source = "rule"
            confidence = None
            reason = rule["reason"]
            signal = rule.get("signal")
            model_play = None
        opp = opportunity(household, play_key)
        signal_set = signal_keys(household)
        flags: list[str] = []
        if source == "rule":
            flags.append("Rule fallback: the model did not return a usable row.")
        if signal and signal not in signal_set:
            flags.append(f"The model cited {signal}, which is not a signal in this household record.")
        if model_play is None and play_key != rule["play"]:
            flags.append("The rule check would choose a different play.")
        if not opp.get("applicable"):
            flags.append(opp.get("formula") or "The play has no source value in this record.")
        if model_play and model_play != rule["play"]:
            flags.append(f"The rule check chooses {plays_short(rule['play'])}.")
        rows.append(
            {
                "household": household,
                "id": household["id"],
                "name": household["name"],
                "aum": household["aum"],
                "aum_display": usd(household["aum"]),
                "relationship": household["relationship"],
                "consent_status": household["consent_status"],
                "signals": household.get("signals") or [],
                "play": play_key,
                "play_label": plays_label(play_key),
                "play_short": plays_short(play_key),
                "play_source": source,
                "confidence": confidence,
                "confidence_label": confidence_label(confidence) if confidence is not None else "n/a",
                "confidence_display": confidence_pct(confidence) if confidence is not None else "n/a",
                "reason": reason,
                "signal": signal,
                "signal_label": SIGNAL_LABELS.get(str(signal), str(signal or "")),
                "rule_play": rule["play"],
                "rule_play_label": plays_short(rule["play"]),
                "opportunity": opp,
                "opportunity_usd": opp["assets_usd"],
                "fee_usd": opp["fee_usd"],
                "status": decision.get("status") or "in_review",
                "decision": decision,
                "flags": flags,
                "model_ranked": source == "model",
            }
        )
    rows.sort(key=lambda row: (-row["opportunity_usd"], row["name"]))
    return rows


def summary(rows: list[dict[str, Any]], book: dict[str, Any], state: Optional[dict[str, Any]]) -> dict[str, Any]:
    by_play: dict[str, int] = {}
    for row in rows:
        by_play[row["play"]] = by_play.get(row["play"], 0) + 1
    total_assets = sum(row["opportunity_usd"] for row in rows)
    total_fee = int(round(total_assets * FEE_RATE))
    statuses: dict[str, int] = {"in_review": 0, "approved": 0, "rejected": 0}
    for row in rows:
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    model_rows = [row for row in rows if row["model_ranked"]]
    agreement = len([row for row in rows if row["model_ranked"] and row["play"] == row["rule_play"]])
    largest = max(rows, key=lambda row: row["opportunity_usd"]) if rows else None
    growth_rows = [row for row in rows if row["play"] != "no_action"]
    average_growth = (
        int(round(total_assets / len(growth_rows))) if growth_rows else 0
    )
    return {
        "total_assets_usd": total_assets,
        "total_assets_display": usd(total_assets),
        "total_fee_usd": total_fee,
        "total_fee_display": usd(total_fee),
        "households": len(rows),
        "growth_plays": len([row for row in rows if row["play"] != "no_action"]),
        "consent_pending": len([row for row in rows if row["consent_status"] == "pending"]),
        "by_play": [
            {"key": key, "label": plays_label(key), "count": by_play.get(key, 0)}
            for key in PLAY_ORDER
            if by_play.get(key, 0)
        ],
        "statuses": statuses,
        "model_rows": len(model_rows),
        "agreement": agreement,
        "book_aum": book.get("aum_total", 0),
        "book_aum_display": usd(book.get("aum_total", 0)),
        "share_of_book": (
            f"{total_assets / book['aum_total'] * 100:.1f}%" if book.get("aum_total") else "n/a"
        ),
        "flagged": len([row for row in rows if row["flags"]]),
        "largest_name": largest["name"] if largest else "",
        "largest_play": largest["play_short"] if largest else "",
        "largest_display": usd(largest["opportunity_usd"]) if largest else usd(0),
        "average_growth_display": usd(average_growth),
    }


def play_key_for_label(label: str) -> str:
    for key, play in PLAYS.items():
        if play["label"] == label or play["short"] == label:
            return key
    return ""
