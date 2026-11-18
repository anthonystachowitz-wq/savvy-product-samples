"""Deterministic transition plan: tasks, critical path, and NIGO risks.

Rules, in order:

1. Every account gets one task per phase, sequenced from the signature date to the
   target live date. Phase due dates are fixed fractions of that window.
2. Accounts with a seeded hard case get extra tasks in the phase where the work
   actually blocks, plus two days of slip on the later phases. That slip is what
   puts them on the critical path.
3. A hard case raises a NIGO risk with a reason, a synthetic firm policy reference,
   and the artifact that clears it. Notes and position data can raise additional
   risks (missing beneficiary, stale address, registration mismatch, cost basis).
4. Every account with a seeded ``signature_count`` gets a form-packet task in document
   collection, and the plan reports the signature load by account and by household. A
   packet for a household whose Broker Protocol consent is pending is marked blocked.
5. Wave 1 uses the explicit ``accounts[].wave`` field when the book carries one, and
   falls back to the clean accounts that can start immediately. Blocked accounts are
   the ones carrying at least one high-severity risk.

No custodian integration is implied. Task dates are planning dates, not promises.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from . import (
    accounts,
    book_meta,
    households,
    load_book,
    parse_date,
    positions,
)

PHASES = (
    "intake",
    "account_opening",
    "document_collection",
    "transfer_initiation",
    "custodian_review",
    "confirmation",
    "handoff",
)

#: Fraction of the signature-to-live window at which each phase is due. The last
#: phase lands one day before the target so handoff never reads as after go-live.
PHASE_FRACTIONS = {
    "intake": 0.04,
    "account_opening": 0.16,
    "document_collection": 0.30,
    "transfer_initiation": 0.46,
    "custodian_review": 0.62,
    "confirmation": 0.80,
    "handoff": 0.96,
}

PHASE_TITLES = {
    "intake": "Confirm client authorization and account details",
    "account_opening": "Open the receiving account",
    "document_collection": "Collect signed forms and supporting documents",
    "transfer_initiation": "Initiate the transfer request",
    "custodian_review": "Track custodian review and validation",
    "confirmation": "Confirm assets received and reconcile cost basis",
    "handoff": "Hand off the funded account to the service team",
}

PHASE_OWNERS = {
    "intake": "Transition specialist",
    "account_opening": "Operations",
    "document_collection": "Transition specialist",
    "transfer_initiation": "Transfer desk",
    "custodian_review": "Transfer desk",
    "confirmation": "Operations",
    "handoff": "Relationship manager",
}

#: Hard-case catalogue. Each entry names the blocking phase, the extra tasks that
#: phase needs, the NIGO reason, the synthetic policy reference, and what clears it.
#: The strings are neutral operations language: no advice, no transaction steps.
HARD_CASES: dict[str, dict[str, Any]] = {
    "trust_certification": {
        "label": "Irrevocable trust",
        "phase": "document_collection",
        "severity": "high",
        "reason": (
            "The account is titled to an irrevocable trust. The receiving custodian "
            "needs trustee certification and the trust EIN before it can open the account."
        ),
        "clears_with": (
            "Signed trustee certification, trust EIN letter, and a signer-authority check "
            "on the trust document."
        ),
        "rule_ref": "Firm policy CP-06 (synthetic)",
        "tasks": [
            ("document_collection", "Obtain trustee certification and trust EIN", "Compliance"),
            ("document_collection", "Confirm signer authority against the trust document", "Compliance"),
            ("custodian_review", "Validate trust titling with the receiving custodian", "Operations"),
        ],
        "why": "Trust certification and EIN gate account opening.",
    },
    "inherited_ira_documentation": {
        "label": "Inherited IRA",
        "phase": "document_collection",
        "severity": "high",
        "reason": (
            "The account is an inherited IRA. Titling requires the beneficiary designation "
            "and the decedent documentation before the receiving custodian will accept it."
        ),
        "clears_with": (
            "Beneficiary designation, death certificate, and the original depositor's "
            "account data."
        ),
        "rule_ref": "Firm policy CP-05 (synthetic)",
        "tasks": [
            ("document_collection", "Collect beneficiary designation and decedent documentation", "Transition specialist"),
            ("custodian_review", "Confirm inherited IRA titling with the custodian", "Operations"),
        ],
        "why": "Inherited IRA titling needs decedent paperwork.",
    },
    "annuity_1035_decision": {
        "label": "Variable annuity",
        "phase": "custodian_review",
        "severity": "high",
        "reason": (
            "The annuity is an insurance contract, not an ACATS-eligible security. A 1035 "
            "exchange decision and a surrender review are required, and the carrier controls "
            "the timeline."
        ),
        "clears_with": (
            "A recorded 1035 election decision, the current surrender charge schedule, and "
            "carrier forms signed by the client."
        ),
        "rule_ref": "Firm policy CP-18 (synthetic)",
        "tasks": [
            ("document_collection", "Record the 1035 exchange election decision", "Advisor"),
            ("custodian_review", "Review the surrender charge schedule before election", "Compliance"),
        ],
        "why": "Carrier-controlled 1035 exchange and surrender review.",
    },
    "held_away_rollover": {
        "label": "Held-away 401(k)",
        "phase": "transfer_initiation",
        "severity": "high",
        "reason": (
            "The 401(k) sits at a plan recordkeeper. ACATS does not apply; a rollover "
            "election and plan distribution paperwork are required."
        ),
        "clears_with": (
            "Client rollover election, plan distribution form, and recordkeeper contact "
            "on file."
        ),
        "rule_ref": "Firm policy CP-12 (synthetic)",
        "tasks": [
            ("document_collection", "Request the plan statement and distribution paperwork", "Transition specialist"),
            ("transfer_initiation", "Obtain the rollover election and recordkeeper details", "Transition specialist"),
        ],
        "why": "Rollover, not ACATS; recordkeeper controls the request.",
    },
    "proprietary_fund_review": {
        "label": "Proprietary fund share class",
        "phase": "custodian_review",
        "severity": "high",
        "reason": (
            "The fund share class may not be available at the receiving custodian. A "
            "liquidation-or-in-kind review is required before the transfer is initiated."
        ),
        "clears_with": (
            "Written fund availability confirmation, or a client-approved liquidation or "
            "in-kind instruction recorded by the advisor."
        ),
        "rule_ref": "Firm policy CP-11 (synthetic)",
        "tasks": [
            ("transfer_initiation", "Review liquidation or in-kind options with the advisor", "Advisor"),
            ("custodian_review", "Confirm fund availability at the receiving custodian", "Operations"),
        ],
        "why": "Share class may fail in-kind eligibility.",
    },
    "lp_assignment": {
        "label": "LP alternative interest",
        "phase": "custodian_review",
        "severity": "high",
        "reason": (
            "The LP interest is not ACATS-eligible. Assignment paperwork and the receiving "
            "custodian's approval are required before anything moves."
        ),
        "clears_with": (
            "Signed assignment documents, issuer or general partner consent, and custodian "
            "approval of the alternative holding."
        ),
        "rule_ref": "Firm policy CP-21 (synthetic)",
        "tasks": [
            ("document_collection", "Prepare LP assignment paperwork", "Operations"),
            ("custodian_review", "Obtain custodian approval for the LP assignment", "Compliance"),
        ],
        "why": "Assignment and custodian approval gate the interest.",
    },
    "utma_custodian": {
        "label": "UTMA account",
        "phase": "document_collection",
        "severity": "high",
        "reason": (
            "The account is a UTMA. The custodian of record and the minor's state of "
            "residence must be confirmed before the receiving account is opened."
        ),
        "clears_with": (
            "Custodian-of-record confirmation and the UTMA account agreement that names "
            "the successor custodian."
        ),
        "rule_ref": "Firm policy CP-08 (synthetic)",
        "tasks": [
            ("document_collection", "Confirm the UTMA custodian of record", "Compliance"),
        ],
        "why": "Custodian-of-record confirmation gates opening.",
    },
    "margin_options_approval": {
        "label": "Margin account with options",
        "phase": "custodian_review",
        "severity": "high",
        "reason": (
            "Options approval does not transfer between firms. The receiving custodian "
            "underwrites the client again, and a signed margin agreement is required."
        ),
        "clears_with": (
            "New option approval at the receiving custodian, signed margin agreement, and "
            "an option transfer approval on the transfer request."
        ),
        "rule_ref": "Firm policy CP-16 (synthetic)",
        "tasks": [
            ("document_collection", "Collect the signed margin agreement", "Operations"),
            ("custodian_review", "Obtain option transfer approval at the receiving custodian", "Compliance"),
        ],
        "why": "Option approval does not port; margin agreement is missing.",
    },
}

#: Keyword routing for the seeded ``hard_case`` field. Order matters: the first
#: match wins, so narrower labels (UTMA, inherited IRA) come before trust and IRA.
HARD_CASE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("utma_custodian", ("utma", "ugma")),
    ("inherited_ira_documentation", ("inherited ira", "inherited_ira", "decedent")),
    ("annuity_1035_decision", ("annuity", "1035")),
    ("held_away_rollover", ("401(k)", "401k", "held-away", "held away", "recordkeeper")),
    (
        "margin_options_approval",
        (
            "margin with options",
            "margin and options",
            "margin account",
            "margin agreement",
            "listed options",
            "margin_options",
        ),
    ),
    (
        "lp_assignment",
        (
            "lp interest",
            "lp alternative",
            "lp_interest",
            "limited partnership",
            "alternative interest",
            "private placement",
        ),
    ),
    ("proprietary_fund_review", ("proprietary", "share class", "class c")),
    ("trust_certification", ("irrevocable", "trust certification", "trustee certification")),
)

#: NIGO risks that can come from notes or positions rather than the hard-case label.
NOTE_RULES: tuple[tuple[str, tuple[str, ...], str, str, str], ...] = (
    (
        "beneficiary_missing",
        ("beneficiary missing", "no beneficiary", "beneficiary not designated"),
        "No beneficiary designation is on file for the account.",
        "Firm policy CP-03 (synthetic)",
        "A signed beneficiary designation naming a primary and contingent beneficiary.",
    ),
    (
        "stale_address",
        ("stale address", "address returned", "returned mail", "verify address"),
        "The address on file is marked stale and has to be re-verified before documents are sent.",
        "Firm policy CP-07 (synthetic)",
        "A client-confirmed mailing address recorded in the CRM.",
    ),
    (
        "registration_mismatch",
        ("registration mismatch", "title mismatch", "titled to"),
        "The account title and the registration line disagree, which rejects the transfer request.",
        "Firm policy CP-09 (synthetic)",
        "A corrected account title or registration that matches the signed authorization.",
    ),
    (
        "non_transferable_share_class",
        ("class c", "proprietary", "not transferable", "non-transferable"),
        "A position is in a share class the receiving custodian may not accept in kind.",
        "Firm policy CP-11 (synthetic)",
        "Fund availability confirmation, or a recorded client election to liquidate in kind.",
    ),
    (
        "annuity_surrender_charge",
        ("surrender charge", "cdsc", "surrender period"),
        "The annuity carries a surrender charge inside the review window.",
        "Firm policy CP-18 (synthetic)",
        "The carrier surrender schedule and a recorded client acknowledgement.",
    ),
    (
        "trust_certification",
        (
            "revocable trust",
            "revocable living trust",
            "trust certification",
            "trustee certification",
        ),
        "The account is titled to a trust. Titling requires trustee certification and a signer-authority check.",
        "Firm policy CP-06 (synthetic)",
        "Signed trustee certification and a signer-authority check on the trust document.",
    ),
)

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


def build_plan(book: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Build the task plan, critical path, risks, and summary for one book."""

    if book is None:
        book = load_book()

    meta = book_meta(book)
    signed, target, window_days = _window(meta)
    account_rows = accounts(book)
    household_names = {
        str(item.get("id")): item.get("display_name") or item.get("name") or str(item.get("id"))
        for item in households(book)
    }

    tasks: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    critical_path: list[dict[str, Any]] = []
    clean_accounts = 0
    blocked_accounts = 0
    signature_total = 0
    signature_by_account: dict[str, int] = {}
    signature_by_household: dict[str, int] = {}
    consent_by_household = {
        str(item.get("id")): str(item.get("consent_status") or "").strip().lower()
        for item in households(book)
    }
    consent_pending_households = [
        household_id
        for household_id, status in consent_by_household.items()
        if status == "pending"
    ]

    for index, account in enumerate(account_rows):
        account_id = str(account.get("id") or f"account-{index + 1}")
        household_id = str(account.get("household_id") or account.get("household") or "")
        hard_case = _hard_case_kind(account)
        stagger = index % 3
        account_tasks = _account_tasks(
            account=account,
            account_id=account_id,
            household_id=household_id,
            hard_case=hard_case,
            signed=signed,
            window_days=window_days,
            stagger=stagger,
        )
        tasks.extend(account_tasks)

        signatures = _signature_count(account)
        if signatures:
            tasks.append(
                _signature_packet_task(
                    account=account,
                    account_id=account_id,
                    household_id=household_id,
                    signatures=signatures,
                    signed=signed,
                    window_days=window_days,
                    stagger=stagger,
                    consent_status=consent_by_household.get(household_id, ""),
                )
            )
            signature_total += signatures
            signature_by_account[account_id] = signatures
            signature_by_household[household_id] = (
                signature_by_household.get(household_id, 0) + signatures
            )

        account_risks = _account_risks(
            account=account,
            account_id=account_id,
            household_id=household_id,
            hard_case=hard_case,
            book=book,
        )
        risks.extend(account_risks)

        high_risks = [risk for risk in account_risks if risk["severity"] == "high"]
        if high_risks:
            blocked_accounts += 1
        if not hard_case and not account_risks and _is_wave1(account):
            clean_accounts += 1

        if hard_case:
            phase = HARD_CASES[hard_case]["phase"]
            blocking_tasks = [task for task in account_tasks if task["phase"] == phase]
            blocking_task = blocking_tasks[0] if blocking_tasks else account_tasks[0]
            downstream = [
                task["id"]
                for task in account_tasks
                if PHASES.index(task["phase"]) >= PHASES.index(blocking_task["phase"])
            ]
            critical_path.append(
                {
                    "account_id": account_id,
                    "household_id": household_id,
                    "household": household_names.get(household_id, household_id),
                    "registration": account.get("registration") or account.get("account_type") or "",
                    "hard_case": HARD_CASES[hard_case]["label"],
                    "blocking_phase": blocking_task["phase"],
                    "blocking_task_id": blocking_task["id"],
                    "task_ids": downstream,
                    "due_at": blocking_task["due_at"],
                    "why": HARD_CASES[hard_case]["why"],
                }
            )

    critical_path.sort(key=lambda item: (item["due_at"], item["account_id"]))
    tasks.sort(key=lambda item: (item["due_at"], item["account_id"], PHASES.index(item["phase"])))

    # Wave 1 is the explicit accounts[].wave field when the book carries one. A book
    # without wave data falls back to the clean accounts that can start immediately.
    explicit_waves = [str(account.get("wave") or "").strip() for account in account_rows]
    if any(explicit_waves):
        wave1 = sum(1 for wave in explicit_waves if wave == "1")
    else:
        wave1 = clean_accounts

    return {
        "tasks": tasks,
        "critical_path": critical_path,
        "risks": risks,
        "summary": {
            "accounts": len(account_rows),
            "wave1": wave1,
            "blocked": blocked_accounts,
            "high_risks": sum(1 for risk in risks if risk["severity"] == "high"),
        },
        "signature_load": {
            "total": signature_total,
            "by_account": signature_by_account,
            "by_household": signature_by_household,
        },
        "consent_pending_households": consent_pending_households,
        "phases": list(PHASES),
        "signed_at": signed.isoformat(),
        "target_live_at": target.isoformat(),
        "window_days": window_days,
    }


# ---------------------------------------------------------------------------
# Window and task construction
# ---------------------------------------------------------------------------


def _window(meta: dict[str, Any]) -> tuple[date, date, int]:
    signed = _first_date(meta, ("signed_at", "signed_date", "signature_date", "contract_signed_at"))
    target = _first_date(meta, ("target_live_at", "target_live_date", "go_live_at", "target_date"))
    if signed and target and target > signed:
        return signed, target, (target - signed).days
    if signed and not target:
        target = signed + timedelta(days=45)
        return signed, target, 45
    if target and not signed:
        signed = target - timedelta(days=45)
        return signed, target, 45
    today = date.today()
    return today, today + timedelta(days=45), 45


def _first_date(meta: dict[str, Any], keys: tuple[str, ...]) -> Optional[date]:
    for key in keys:
        parsed = parse_date(meta.get(key))
        if parsed:
            return parsed
    return None


def _account_tasks(
    account: dict[str, Any],
    account_id: str,
    household_id: str,
    hard_case: str,
    signed: date,
    window_days: int,
    stagger: int,
) -> list[dict[str, Any]]:
    # Hard-case accounts slip two days from document collection onward. That is a
    # planning allowance, not a forecast of a custodian's review time.
    slip = 2 if hard_case else 0
    tasks: list[dict[str, Any]] = []
    for phase in PHASES:
        extra = slip if PHASES.index(phase) >= PHASES.index("document_collection") else 0
        due = _due_date(signed, window_days, phase, stagger + extra)
        tasks.append(
            {
                "id": f"task-{account_id}-{phase}",
                "account_id": account_id,
                "household_id": household_id,
                "phase": phase,
                "title": PHASE_TITLES[phase],
                "owner_role": PHASE_OWNERS[phase],
                "due_at": due.isoformat(),
                "status": "open",
                "blocker": "",
            }
        )

    if hard_case:
        for phase, title, owner in HARD_CASES[hard_case]["tasks"]:
            extra_phase_index = PHASES.index(phase)
            due = _due_date(signed, window_days, phase, stagger + 1, phase_index=extra_phase_index)
            tasks.append(
                {
                    "id": f"task-{account_id}-{hard_case}-{slug_title(title)}",
                    "account_id": account_id,
                    "household_id": household_id,
                    "phase": phase,
                    "title": title,
                    "owner_role": owner,
                    "due_at": due.isoformat(),
                    "status": "blocked",
                    "blocker": HARD_CASES[hard_case]["clears_with"],
                }
            )
        # The base document task is blocked until the hard-case documents land.
        blocking_phase = HARD_CASES[hard_case]["phase"]
        for task in tasks:
            if task["phase"] == blocking_phase and not task["blocker"]:
                task["status"] = "blocked"
                task["blocker"] = HARD_CASES[hard_case]["clears_with"]
    return tasks


def _signature_count(account: dict[str, Any]) -> int:
    """The seeded signature estimate for one account, or 0 when it is absent.

    The count is a per-account estimate of the forms that need a client signature, 2
    to 6 in the seeded book. A missing value is unknown, not zero, so no packet task
    is invented for it.
    """

    raw = account.get("signature_count")
    if isinstance(raw, bool):
        return 0
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _signature_packet_task(
    account: dict[str, Any],
    account_id: str,
    household_id: str,
    signatures: int,
    signed: date,
    window_days: int,
    stagger: int,
    consent_status: str,
) -> dict[str, Any]:
    """One form-packet task per account, blocked while protocol consent is pending.

    Signature collection is client-facing work, so a household with pending Broker
    Protocol consent does not get a live packet until consent is received.
    """

    custodian = str(account.get("custodian") or "the receiving custodian")
    noun = "signature" if signatures == 1 else "signatures"
    phase = "document_collection"
    due = _due_date(signed, window_days, phase, stagger)
    pending = consent_status == "pending"
    return {
        "id": f"task-{account_id}-signature-packet",
        "account_id": account_id,
        "household_id": household_id,
        "phase": phase,
        "title": f"Collect {signatures} {noun} on {custodian} account opening packet",
        "owner_role": "Transition specialist",
        "due_at": due.isoformat(),
        "status": "blocked" if pending else "open",
        "blocker": (
            f"Broker Protocol consent is pending for household {household_id}."
            if pending
            else ""
        ),
    }


def _due_date(
    signed: date,
    window_days: int,
    phase: str,
    stagger: int,
    phase_index: Optional[int] = None,
) -> date:
    fraction = PHASE_FRACTIONS[phase]
    offset = int(round(fraction * window_days)) + stagger
    if phase_index is not None and phase == "document_collection":
        # Extra document work sits a day after the phase start, not on top of it.
        offset += 1
    offset = max(0, min(window_days, offset))
    return signed + timedelta(days=offset)


def slug_title(title: str) -> str:
    import re

    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return cleaned[:40] or "task"


# ---------------------------------------------------------------------------
# Hard-case classification
# ---------------------------------------------------------------------------


def _hard_case_kind(account: dict[str, Any]) -> str:
    raw = str(account.get("hard_case") or "").strip().lower().replace("-", " ")
    if raw:
        # The seed may store a catalogue key ("trust_certification") or prose
        # ("irrevocable trust"). Normalise both before keyword routing.
        key = raw.replace(" ", "_")
        if key in HARD_CASES:
            return key
        for kind, keywords in HARD_CASE_KEYWORDS:
            if any(keyword in raw for keyword in keywords):
                return kind
        if key in {"none", "no", "false", "n/a"}:
            raw = ""
        else:
            return "trust_certification" if "trust" in raw else ""
    text = _account_text(account)
    for kind, keywords in HARD_CASE_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return kind
    return ""


def _account_text(account: dict[str, Any]) -> str:
    parts = [
        account.get("account_type"),
        account.get("registration"),
        account.get("hard_case"),
        account.get("hard_case_note"),
        account.get("notes"),
        account.get("transfer_path"),
    ]
    return " ".join(str(part).lower() for part in parts if part)


def _is_wave1(account: dict[str, Any]) -> bool:
    status = str(account.get("status") or "").strip().lower()
    return status not in {"funded", "closed", "transferred", "blocked", "cancelled"}


# ---------------------------------------------------------------------------
# Risk construction
# ---------------------------------------------------------------------------


def _account_risks(
    account: dict[str, Any],
    account_id: str,
    household_id: str,
    hard_case: str,
    book: dict[str, Any],
) -> list[dict[str, Any]]:
    risks: dict[str, dict[str, Any]] = {}

    if hard_case:
        catalogue = HARD_CASES[hard_case]
        risks[hard_case] = _risk(
            account_id=account_id,
            household_id=household_id,
            kind=hard_case,
            severity=catalogue["severity"],
            reason=catalogue["reason"],
            rule_ref=catalogue["rule_ref"],
            clears_with=catalogue["clears_with"],
        )

    text = _account_text(account)
    for kind, keywords, reason, rule_ref, clears_with in NOTE_RULES:
        if kind in risks:
            continue
        if any(keyword in text for keyword in keywords):
            risks[kind] = _risk(
                account_id=account_id,
                household_id=household_id,
                kind=kind,
                severity="medium" if kind == "stale_address" else "high",
                reason=reason,
                rule_ref=rule_ref,
                clears_with=clears_with,
            )

    # Position data can raise a risk the account label does not carry. When the
    # account already has a hard case, that risk is the better explanation: an
    # annuity subaccount or a listed option is not a "share class" problem.
    for position in positions(book, account_id):
        if position.get("transferable") is False and not hard_case:
            risks.setdefault(
                "non_transferable_asset",
                _risk(
                    account_id=account_id,
                    household_id=household_id,
                    kind="non_transferable_asset",
                    severity="high",
                    reason=(
                        f"The position {position.get('symbol', 'unknown')} is marked not "
                        "transferable in the book data."
                    ),
                    rule_ref="Firm policy CP-11 (synthetic)",
                    clears_with=(
                        "Custodian confirmation that the asset can be held, or a recorded "
                        "client election to liquidate."
                    ),
                ),
            )
        cost_basis = position.get("cost_basis")
        if cost_basis in (None, "", 0, "0", "0.00") and position.get("value"):
            risks.setdefault(
                "cost_basis_missing",
                _risk(
                    account_id=account_id,
                    household_id=household_id,
                    kind="cost_basis_missing",
                    severity="low",
                    reason=(
                        f"The position {position.get('symbol', 'unknown')} has no cost basis "
                        "on the statement, which delays confirmation and reporting."
                    ),
                    rule_ref="Firm policy CP-15 (synthetic)",
                    clears_with="Carrying firm cost basis statement or a documented basis election.",
                ),
            )

    return list(risks.values())


def _risk(
    account_id: str,
    household_id: str,
    kind: str,
    severity: str,
    reason: str,
    rule_ref: str,
    clears_with: str,
) -> dict[str, Any]:
    return {
        "id": f"risk-{account_id}-{kind}",
        "account_id": account_id,
        "household_id": household_id,
        "kind": kind,
        "severity": severity,
        "reason": reason,
        "rule_ref": rule_ref,
        "clears_with": clears_with,
        "status": "open" if severity == "high" else "monitor",
    }
