#!/usr/bin/env python3
"""Generate the Book IQ data files from the seeded book.

Two runs produce identical bytes. Every household value comes from
data/book.json or from the fixed enrichment tables below. Nothing is random and
nothing is read from the clock except the fixed as-of date.

    python3 scripts/generate_bookiq.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.bookiq.logic import (
    FEE_RATE,
    PLAY_ORDER,
    PLAYS,
    display_pct,
    date_long,
    share_pct,
    usd,
)

BOOK_PATH = ROOT / "data" / "book.json"
OUT_DIR = ROOT / "data" / "modules" / "bookiq"
AS_OF = date(2026, 11, 19)
CASH_TARGET = 0.05
REVIEW_DAYS = 365
RMD_FACTOR_73 = 26.5
RMD_RETAINED = 0.35
FEE_RATE = 0.0090

ENRICHMENT: dict[str, dict[str, Any]] = {
    "H-1042": {
        "last_meeting": "2025-10-24",
        "held_away": {
            "usd": 1840000,
            "custodian": "Halden Private Client",
            "note": "Next-generation accounts are held at the family's former adviser. Two accounts, no transfer paperwork on file.",
        },
    },
    "H-1043": {
        "last_meeting": "2026-09-22",
        "referral": {
            "count": 2,
            "last_ask": "2025-04-18",
            "note": "Introduced two households in 2024 and 2025. No referral ask is logged in 19 months.",
        },
    },
    "H-1044": {
        "last_meeting": "2026-10-02",
    },
    "H-1045": {
        "last_meeting": "2026-06-11",
        "life_event": {
            "type": "Retirement",
            "date": "2027-04-30",
            "value_usd": 1300000,
            "note": "Retiring April 2027. The annuity income election is due January 2027.",
        },
    },
    "H-1046": {
        "last_meeting": "2026-07-30",
    },
    "H-1047": {
        "last_meeting": "2026-10-16",
        "held_away": {
            "usd": 1150000,
            "custodian": "Northline Retirement Services",
            "note": "Former employer 401k. Rollover approval is pending since September 2026.",
        },
    },
    "H-1048": {
        "last_meeting": "2025-09-12",
    },
    "H-1049": {
        "last_meeting": "2025-10-30",
        "referral": {
            "count": 1,
            "last_ask": "2025-02-11",
            "note": "Introduced a business partner in 2025. No formal ask since.",
        },
    },
    "H-1050": {
        "last_meeting": "2026-09-09",
    },
    "H-1051": {
        "last_meeting": "2026-06-18",
    },
    "H-1052": {
        "last_meeting": "2026-10-08",
        "rmd": {
            "birth_year": 1953,
            "age": 73,
            "first_year": 2026,
            "factor": RMD_FACTOR_73,
            "due": "2026-12-31",
        },
    },
    "H-1053": {
        "last_meeting": "2026-11-05",
        "held_away": {
            "usd": 1350000,
            "custodian": "Alcott Retirement Services",
            "note": "Former employer plan. No rollover paperwork has started.",
        },
    },
}

LABELS: dict[str, dict[str, Any]] = {
    "H-1042": {
        "expected": "consolidate_held_away",
        "required": "held_away",
        "alternatives": [],
        "rationale": "USD 1,840,000 sits at Halden Private Client. The review is 26 days past due and is a smaller play.",
    },
    "H-1043": {
        "expected": "referral",
        "required": "referral_source",
        "alternatives": [],
        "rationale": "Two introductions and no ask in 19 months, on a 17 year relationship.",
    },
    "H-1044": {
        "expected": "no_action",
        "required": "transfer_hold",
        "alternatives": [],
        "rationale": "The only account is an inherited IRA in transfer. No growth signal clears.",
    },
    "H-1045": {
        "expected": "life_event",
        "required": "life_event",
        "alternatives": [{"play": "reduce_concentration", "signals": ["concentration"]}],
        "rationale": "Retirement in April 2027 and the annuity election are dated. The restricted holdings are a real secondary play.",
    },
    "H-1046": {
        "expected": "reinvest_cash",
        "required": "cash_drag",
        "alternatives": [],
        "rationale": "Cash is 9.3 percent of assets after a contribution and has no auto-invest instruction.",
    },
    "H-1047": {
        "expected": "consolidate_held_away",
        "required": "held_away",
        "alternatives": [{"play": "reinvest_cash", "signals": ["cash_drag"]}],
        "rationale": "USD 1,150,000 sits in a former employer plan. Cash drag is close behind.",
    },
    "H-1048": {
        "expected": "schedule_review",
        "required": "overdue_review",
        "alternatives": [],
        "rationale": "The last meeting was 433 days ago. No other signal clears.",
    },
    "H-1049": {
        "expected": "referral",
        "required": "referral_source",
        "alternatives": [],
        "rationale": "One introduction and no ask in 21 months. The review is 20 days past due.",
    },
    "H-1050": {
        "expected": "reduce_concentration",
        "required": "concentration",
        "alternatives": [],
        "rationale": "The Kestrel Ridge fund is 42.2 percent of assets and restricted through June 2027.",
    },
    "H-1051": {
        "expected": "reinvest_cash",
        "required": "cash_drag",
        "alternatives": [],
        "rationale": "Cash is 10.3 percent of assets with no auto-invest instruction.",
    },
    "H-1052": {
        "expected": "rmd_plan",
        "required": "rmd_window",
        "alternatives": [],
        "rationale": "The first required distribution is due December 31, 2026 and no plan is recorded.",
    },
    "H-1053": {
        "expected": "consolidate_held_away",
        "required": "held_away",
        "alternatives": [{"play": "reinvest_cash", "signals": ["cash_drag"]}],
        "rationale": "USD 1,350,000 sits in a former employer plan. Cash is a smaller secondary play.",
    },
}


def load_book() -> dict[str, Any]:
    return json.loads(BOOK_PATH.read_text(encoding="utf-8"))


def usd_int(value: float) -> str:
    return "USD " + f"{int(round(value)):,}"


def field(label: str, value: Any, display: str, source: str) -> dict[str, Any]:
    return {"label": label, "value": value, "display": display, "source": source}


def month_label(iso: str) -> str:
    parsed = date.fromisoformat(iso)
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"


def positions_for(book: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    account_household = {account["id"]: account["household_id"] for account in book["accounts"]}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for position in book["positions"]:
        grouped[account_household[position["account_id"]]].append(position)
    return grouped


def build_household(
    household: dict[str, Any],
    accounts: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    enrichment: dict[str, Any],
) -> dict[str, Any]:
    household_id = household["id"]
    aum = int(household["aum"])
    as_of = AS_OF

    # Cash and concentration come from the seeded positions, not from a guess.
    cash_usd = sum(position["value"] for position in positions if position["symbol"] == "CASH")
    cash_usd = int(round(cash_usd))
    cash_pct = round(cash_usd / aum * 100, 1) if aum else 0.0
    cash_target_usd = int(round(aum * CASH_TARGET))
    excess_cash_usd = max(0, cash_usd - cash_target_usd)

    by_symbol: dict[str, dict[str, Any]] = {}
    for position in positions:
        if position["symbol"] == "CASH":
            continue
        entry = by_symbol.setdefault(
            position["symbol"],
            {"symbol": position["symbol"], "name": position["name"], "value": 0.0},
        )
        entry["value"] += position["value"]
    top = max(by_symbol.values(), key=lambda item: item["value"]) if by_symbol else None
    top_name = f"{top['name']} ({top['symbol']})" if top else "No positions"
    top_usd = int(round(top["value"])) if top else 0
    top_pct = round(top_usd / aum * 100, 1) if aum else 0.0

    restricted = [position for position in positions if not position.get("transferable", True)]
    restricted_usd = int(round(sum(position["value"] for position in restricted)))
    restricted_pct = round(restricted_usd / aum * 100, 1) if aum else 0.0
    restricted_names = sorted({position["name"] for position in restricted})
    if len(restricted_names) > 1:
        if any("Halden" in name for name in restricted_names):
            restricted_holding = "Meridian Select Growth Fund, Class C and Halden annuity subaccounts"
        else:
            restricted_holding = ", ".join(restricted_names)
    elif restricted_names:
        restricted_holding = restricted_names[0]
    else:
        restricted_holding = ""

    last_meeting = str(enrichment["last_meeting"])
    last_meeting_date = date.fromisoformat(last_meeting)
    days_since = (as_of - last_meeting_date).days
    next_review = last_meeting_date.fromordinal(last_meeting_date.toordinal() + REVIEW_DAYS)
    overdue_days = max(0, (as_of - next_review).days)

    client_since_year = 0
    for token in household.get("relationship", "").split():
        if token.isdigit() and len(token) == 4:
            client_since_year = int(token)
            break
    client_years = as_of.year - client_since_year if client_since_year else 0

    fields: dict[str, dict[str, Any]] = {
        "aum": field("Household AUM", aum, usd_int(aum), "Seeded book"),
        "account_count": field(
            "Accounts",
            len(accounts),
            f"{len(accounts)} account" + ("" if len(accounts) == 1 else "s"),
            "Seeded book",
        ),
        "client_since_year": field(
            "Client since",
            client_since_year,
            f"{client_since_year} ({client_years} years)",
            "Seeded book",
        ),
        "top_holding_name": field(
            "Largest holding", top_name, top_name, "Seeded book positions"
        ),
        "top_holding_pct": field(
            "Largest holding share",
            top_pct,
            f"{display_pct(top_pct)} of AUM",
            "Computed from seeded positions",
        ),
        "top_holding_usd": field(
            "Largest holding value",
            top_usd,
            usd_int(top_usd),
            "Computed from seeded positions",
        ),
        "cash_usd": field(
            "Cash", cash_usd, usd_int(cash_usd), "Seeded book positions"
        ),
        "cash_pct": field(
            "Cash share",
            cash_pct,
            f"{display_pct(cash_pct)} of AUM",
            "Computed from seeded positions",
        ),
        "excess_cash_usd": field(
            "Excess cash",
            excess_cash_usd,
            usd_int(excess_cash_usd),
            "Cash above the 5 percent target",
        ),
        "last_meeting_date": field(
            "Last meeting", last_meeting, month_label(last_meeting), "Synthetic enrichment"
        ),
        "days_since_meeting": field(
            "Days since the last meeting",
            days_since,
            f"{days_since} days",
            "Computed from the last meeting date",
        ),
        "next_review_date": field(
            "Review due",
            next_review.isoformat(),
            month_label(next_review.isoformat()),
            "Last meeting plus 365 days",
        ),
    }

    held_away = enrichment.get("held_away")
    if held_away:
        fields["held_away_usd"] = field(
            "Held-away assets",
            int(held_away["usd"]),
            usd_int(held_away["usd"]),
            "Synthetic enrichment",
        )
        fields["held_away_custodian"] = field(
            "Held-away custodian",
            held_away["custodian"],
            held_away["custodian"],
            "Synthetic enrichment",
        )

    referral = enrichment.get("referral")
    if referral:
        fields["referral_count"] = field(
            "Referral history",
            int(referral["count"]),
            f"{referral['count']} introduction" + ("" if referral["count"] == 1 else "s"),
            "Synthetic enrichment",
        )
        fields["referral_last_ask"] = field(
            "Last referral ask",
            referral["last_ask"],
            month_label(referral["last_ask"]),
            "Synthetic enrichment",
        )

    rmd = enrichment.get("rmd")
    if rmd:
        rmd_amount = int(round(aum / float(rmd["factor"])))
        fields["rmd_age"] = field(
            "Age in 2026", int(rmd["age"]), str(rmd["age"]), "Synthetic enrichment"
        )
        fields["rmd_amount_usd"] = field(
            "Estimated RMD",
            rmd_amount,
            usd_int(rmd_amount),
            "AUM divided by the Uniform Lifetime Table factor",
        )
        fields["rmd_due_date"] = field(
            "RMD due", rmd["due"], month_label(rmd["due"]), "Synthetic enrichment"
        )

    if restricted_usd:
        fields["restricted_holding"] = field(
            "Restricted holding",
            restricted_holding,
            restricted_holding,
            "Seeded book positions",
        )
        fields["restricted_usd"] = field(
            "Restricted position value",
            restricted_usd,
            usd_int(restricted_usd),
            "Seeded book positions",
        )
        fields["restricted_pct"] = field(
            "Restricted share",
            restricted_pct,
            f"{display_pct(restricted_pct)} of AUM",
            "Computed from seeded positions",
        )
        note_dates = sorted(
            {
                match.group(0)
                for position in restricted
                for match in [re.search(r"\d{4}-\d{2}-\d{2}", position.get("note", ""))]
                if match
            }
        )
        if note_dates:
            fields["restricted_until"] = field(
                "Restriction ends",
                note_dates[0],
                month_label(note_dates[0]),
                "Seeded book positions",
            )

    life_event = enrichment.get("life_event")
    if life_event:
        fields["life_event_type"] = field(
            "Life event",
            life_event["type"],
            life_event["type"],
            "Synthetic enrichment",
        )
        fields["life_event_date"] = field(
            "Life event date",
            life_event["date"],
            month_label(life_event["date"]),
            "Synthetic enrichment",
        )
        fields["life_event_value_usd"] = field(
            "Assets tied to the event",
            int(life_event["value_usd"]),
            usd_int(life_event["value_usd"]),
            "Seeded book positions",
        )

    # Signals are derived from the fields, in a fixed order.
    signals: list[dict[str, Any]] = []
    if held_away:
        share = held_away["usd"] / aum
        signals.append(
            {
                "key": "held_away",
                "label": "Held-away assets",
                "strength": "high" if share >= 0.25 else "medium",
                "display": f"{usd_int(held_away['usd'])} at {held_away['custodian']}",
                "source_field": "held_away_usd",
                "detail": held_away["note"],
                "value_usd": int(held_away["usd"]),
            }
        )
    if cash_pct >= 7.0:
        signals.append(
            {
                "key": "cash_drag",
                "label": "Cash drag",
                "strength": "high" if cash_pct >= 9.0 else "medium",
                "display": f"{usd_int(cash_usd)}, {display_pct(cash_pct)} of AUM in cash",
                "source_field": "excess_cash_usd",
                "detail": f"{usd_int(excess_cash_usd)} sits above the 5 percent target with no auto-invest instruction.",
                "value_usd": excess_cash_usd,
            }
        )
    if days_since > REVIEW_DAYS:
        signals.append(
            {
                "key": "overdue_review",
                "label": "Overdue review",
                "strength": "high" if overdue_days >= 60 else "medium",
                "display": f"Last meeting {month_label(last_meeting)}, {days_since} days ago",
                "source_field": "days_since_meeting",
                "detail": f"The review cadence is 365 days. It is {overdue_days} days past due.",
                "value": days_since,
            }
        )
    if rmd:
        rmd_amount = int(round(aum / float(rmd["factor"])))
        signals.append(
            {
                "key": "rmd_window",
                "label": "RMD window",
                "strength": "high",
                "display": f"Age {rmd['age']} in 2026, first RMD due {month_label(rmd['due'])}",
                "source_field": "rmd_amount_usd",
                "detail": f"Estimated {usd_int(rmd_amount)} at the Uniform Lifetime Table factor of {rmd['factor']}.",
                "value_usd": rmd_amount,
            }
        )
    if restricted_pct >= 25.0:
        signals.append(
            {
                "key": "concentration",
                "label": "Concentration",
                "strength": "high" if restricted_pct >= 35.0 else "medium",
                "display": f"{usd_int(restricted_usd)}, {display_pct(restricted_pct)} of AUM",
                "source_field": "restricted_pct",
                "detail": restricted_holding
                + (
                    ". Assignment needs general partner consent through June 30, 2027."
                    if "Kestrel" in restricted_holding
                    else ". The holding is not transferable in kind."
                ),
                "value_usd": restricted_usd,
            }
        )
    if referral:
        last_ask = date.fromisoformat(referral["last_ask"])
        months_since = (as_of - last_ask).days // 30
        signals.append(
            {
                "key": "referral_source",
                "label": "Referral source",
                "strength": "high"
                if referral["count"] >= 2 or months_since >= 18
                else "medium",
                "display": f"{referral['count']} introduction"
                + ("" if referral["count"] == 1 else "s")
                + f", last ask {month_label(referral['last_ask'])}",
                "source_field": "referral_count",
                "detail": f"No ask logged in {months_since} months. {referral['note']}",
                "value": referral["count"],
            }
        )
    if life_event:
        signals.append(
            {
                "key": "life_event",
                "label": "Life event",
                "strength": "high",
                "display": f"Retires {month_label(life_event['date'])}; annuity election due January 2027",
                "source_field": "life_event_value_usd",
                "detail": life_event["note"],
                "value_usd": int(life_event["value_usd"]),
            }
        )
    if not signals:
        signals.append(
            {
                "key": "transfer_hold",
                "label": "Transfer hold",
                "strength": "high",
                "display": "Inherited IRA transfer in flight",
                "source_field": "account_count",
                "detail": "No funded managed account yet. The only open item is the transfer.",
                "value": len(accounts),
            }
        )

    # Evidence is the record view behind the queue row.
    evidence: list[dict[str, Any]] = []
    evidence.append(
        {
            "label": "Household AUM",
            "value": usd_int(aum),
            "source_field": "aum",
            "source": "Seeded book",
            "detail": f"{household_id} in {household['relationship'].lower()}.",
        }
    )
    evidence.append(
        {
            "label": "Accounts",
            "value": f"{len(accounts)} account"
            + ("" if len(accounts) == 1 else "s")
            + " at "
            + ", ".join(sorted({account['custodian'] for account in accounts})),
            "source_field": "account_count",
            "source": "Seeded book",
            "detail": "; ".join(
                f"{account['account_type']} ({account['status']})" for account in accounts
            ),
        }
    )
    evidence.append(
        {
            "label": "Consent",
            "value": household["consent_status"].replace("_", " ").capitalize(),
            "source_field": "consent_status",
            "source": "Seeded book",
            "detail": "Client outreach needs recorded consent before approval.",
        }
    )
    evidence.append(
        {
            "label": "Last meeting",
            "value": f"{month_label(last_meeting)}, {days_since} days ago",
            "source_field": "last_meeting_date",
            "source": "Synthetic enrichment",
            "detail": f"Review due {month_label(next_review.isoformat())} on a 365 day cadence.",
        }
    )
    evidence.append(
        {
            "label": "Cash",
            "value": f"{usd_int(cash_usd)}, {display_pct(cash_pct)} of AUM",
            "source_field": "cash_usd",
            "source": "Seeded book positions",
            "detail": (
                f"{usd_int(excess_cash_usd)} above the 5 percent target."
                if excess_cash_usd
                else "No cash above the 5 percent target."
            ),
        }
    )
    evidence.append(
        {
            "label": "Largest holding",
            "value": f"{top_name}, {display_pct(top_pct)} of AUM",
            "source_field": "top_holding_pct",
            "source": "Computed from seeded positions",
            "detail": f"{usd_int(top_usd)} in one position.",
        }
    )
    for signal in signals:
        if signal["key"] in {"held_away", "referral_source", "rmd_window", "concentration", "life_event"}:
            evidence.append(
                {
                    "label": signal["label"],
                    "value": signal["display"],
                    "source_field": signal["source_field"],
                    "source": "Synthetic enrichment"
                    if signal["key"] in {"held_away", "referral_source", "life_event"}
                    else "Seeded book positions",
                    "detail": signal["detail"],
                }
            )

    return {
        "id": household_id,
        "name": household["display_name"],
        "aum": aum,
        "relationship": household["relationship"],
        "notes": household["notes"],
        "consent_status": household["consent_status"],
        "accounts": [
            {
                "id": account["id"],
                "custodian": account["custodian"],
                "type": account["account_type"],
                "registration": account["registration"],
                "masked": account["masked_number"],
                "status": account["status"],
            }
            for account in accounts
        ],
        "account_count": len(accounts),
        "custodians": sorted({account["custodian"] for account in accounts}),
        "signals": signals,
        "fields": fields,
        "evidence": evidence,
        "review": {
            "last_meeting": last_meeting,
            "next_due": next_review.isoformat(),
            "days_since": days_since,
            "overdue_days": overdue_days,
        },
        "held_away": held_away or None,
        "referral": referral or None,
        "rmd": (
            {
                **rmd,
                "amount_usd": int(round(aum / float(rmd["factor"]))),
            }
            if rmd
            else None
        ),
        "life_event": life_event or None,
        "concentration": {
            "restricted_usd": restricted_usd,
            "restricted_pct": restricted_pct,
            "holdings": restricted_names,
        },
        "cash": {
            "usd": cash_usd,
            "pct": cash_pct,
            "excess_usd": excess_cash_usd,
            "target_usd": cash_target_usd,
        },
    }


def build_assumptions() -> dict[str, Any]:
    plays = []
    for key in PLAY_ORDER:
        play = PLAYS[key]
        plays.append(
            {
                "key": key,
                "label": play["label"],
                "short": play["short"],
                "required_signal": play["required_signal"],
                "input_field": play["input_field"],
                "share": play["share"],
                "share_display": share_pct(play["share"]) if play["share"] else "n/a",
                "basis": play["basis"],
            }
        )
    return {
        "module": "bookiq",
        "as_of": AS_OF.isoformat(),
        "currency": "USD",
        "fee_rate": FEE_RATE,
        "fee_rate_display": "0.90%",
        "plays": plays,
        "parameters": {
            "cash_target_pct": CASH_TARGET,
            "review_cadence_days": REVIEW_DAYS,
            "review_at_risk_pct": 0.10,
            "review_saved_pct": 0.25,
            "rmd_factor_age_73": RMD_FACTOR_73,
            "rmd_retained_pct": RMD_RETAINED,
            "held_away_target_pct": 0.25,
            "concentration_target_pct": 0.20,
            "referral_size_pct": 0.20,
            "life_event_moved_pct": 0.50,
        },
        "notes": [
            "Every figure is modelled from the seeded book plus the fixed enrichment tables in scripts/generate_bookiq.py. Nothing here is measured.",
            "Modelled net new assets is an annual opportunity, not a forecast. The fee line applies the 0.90 percent standard rate to that opportunity.",
            "Held-away balances, meeting dates, referral history, RMD age, and the life event are synthetic enrichment. Cash, holdings, restricted positions, and AUM come from data/book.json.",
            "The queue sorts by modelled net new assets. Confidence is the model's own estimate and is not a calibrated probability.",
        ],
    }


def build_eval_cases(households: list[dict[str, Any]]) -> dict[str, Any]:
    names = {household["id"]: household["name"] for household in households}
    items = []
    for household in households:
        label = LABELS[household["id"]]
        items.append(
            {
                "household_id": household["id"],
                "household": names[household["id"]],
                "expected_play": label["expected"],
                "required_signal": label["required"],
                "alternatives": label["alternatives"],
                "rationale": label["rationale"],
            }
        )
    return {
        "module": "bookiq",
        "name": "Play and signal accuracy",
        "as_of": AS_OF.isoformat(),
        "scoring": "Pass when the top play equals the expected play (or an accepted alternative) and the cited signal matches that play's required signal.",
        "cases": len(items),
        "items": items,
    }


def write_json(name: str, payload: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    book = load_book()
    positions_by_household = positions_for(book)
    accounts_by_household: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for account in book["accounts"]:
        accounts_by_household[account["household_id"]].append(account)

    households: list[dict[str, Any]] = []
    total_positions_value = 0
    for household in book["households"]:
        household_id = household["id"]
        positions = positions_by_household[household_id]
        accounts = accounts_by_household[household_id]
        if not accounts:
            raise SystemExit(f"{household_id} has no accounts in data/book.json")
        if household_id not in ENRICHMENT:
            raise SystemExit(f"{household_id} has no enrichment row")
        positions_value = sum(position["value"] for position in positions)
        total_positions_value += positions_value
        if abs(positions_value - household["aum"]) > 1:
            raise SystemExit(
                f"{household_id} positions total {positions_value} but AUM is {household['aum']}"
            )
        households.append(
            build_household(household, accounts, positions, ENRICHMENT[household_id])
        )

    if len(households) != 12:
        raise SystemExit(f"Expected 12 households, found {len(households)}")
    missing = set(LABELS) - {household["id"] for household in households}
    if missing:
        raise SystemExit(f"Labels missing for {sorted(missing)}")
    for household in households:
        label = LABELS[household["id"]]
        if label["expected"] not in PLAYS:
            raise SystemExit(f"{household['id']} expects an unknown play")
        if label["required"] not in {signal["key"] for signal in household["signals"]}:
            raise SystemExit(
                f"{household['id']} requires {label['required']} but the record does not have it"
            )

    payload = {
        "module": "bookiq",
        "as_of": AS_OF.isoformat(),
        "source": "data/book.json plus the fixed enrichment tables in scripts/generate_bookiq.py",
        "book": {
            "id": book["book"]["id"],
            "advisor_name": book["book"]["advisor_name"],
            "firm_from": book["book"]["firm_from"],
            "aum_total": book["book"]["aum_total"],
            "household_count": book["book"]["household_count"],
            "signed_at": book["book"]["signed_at"],
            "custodians": book["book"]["custodians"],
        },
        "households": households,
    }
    households_path = write_json("households.json", payload)
    assumptions_path = write_json("assumptions.json", build_assumptions())
    eval_path = write_json("eval_cases.json", build_eval_cases(households))

    signal_count = sum(len(household["signals"]) for household in households)
    held_away_total = sum(
        household["held_away"]["usd"] for household in households if household["held_away"]
    )
    restricted_total = sum(
        household["concentration"]["restricted_usd"] for household in households
    )
    print(f"households: {len(households)}")
    print(f"signals: {signal_count}")
    print(f"eval cases: {len(households)}")
    print(f"held-away total: {usd(held_away_total)}")
    print(f"restricted total: {usd(restricted_total)}")
    print(f"seeded positions total: {usd(total_positions_value)}")
    print(f"wrote: {households_path.relative_to(ROOT)}")
    print(f"wrote: {assumptions_path.relative_to(ROOT)}")
    print(f"wrote: {eval_path.relative_to(ROOT)}")
    digest = hashlib.sha256(households_path.read_bytes()).hexdigest()[:16]
    print(f"households.json sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
