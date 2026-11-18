"""Three explain-only draft kinds, assembled from seeded facts.

Every fact in a draft is a claim with a source reference that resolves against the
book (or the plan). The body is assembled from claim-bearing blocks; a final word-count
fit drops optional blocks or adds neutral filler, and never invents a number. Any
number left without a cited claim shows up as status ``uncited``, and
:func:`pipeline.guardrails.check_draft` blocks approval.

Kinds and lengths:

* ``client_update`` - household status note, 90 to 160 words
* ``custodian_cover`` - transfer cover letter, 60 to 120 words
* ``internal_digest`` - transition lead digest, 60 to 120 words

The drafts explain what the records show and what happens next. They do not recommend
an investment action, promise a custodian timeline, or state a performance result.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.llm import MODE_DETERMINISTIC_TEMPLATE, utc_now_iso

from . import (
    accounts,
    book_meta,
    find_household,
    get_path,
    households,
    load_book,
    money,
    positions,
    to_number,
    word_count,
)
from .guardrails import check_draft, blocking_checks
from .plan import build_plan

KINDS = ("client_update", "custodian_cover", "internal_digest")

WORD_LIMITS = {
    "client_update": (90, 160),
    "custodian_cover": (60, 120),
    "internal_digest": (60, 120),
}

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

#: Body separators. chr(10) keeps the escape out of the source and the strings
#: explicit at the call site.
PARAGRAPH = chr(10) * 2
LINE = chr(10)

#: Neutral fillers carrying no numbers and no action language. Used only to reach the
#: minimum length, never to pad a missing fact.
FILLERS = {
    "client_update": (
        "Your advisor can explain any line on the statement before you sign a form.",
        "We keep a record of every document we receive and every draft we send.",
        "This note covers only the accounts listed above.",
    ),
    "custodian_cover": (
        "The signed authorization is the only instruction this letter carries.",
        "Please reply to the transition lead if a form is incomplete.",
        "The package is assembled for transfer processing only.",
    ),
    "internal_digest": (
        "Each owner and due date is listed in the plan.",
        "Every risk carries a policy reference and the artifact that clears it.",
        "No client-facing draft is sent without a recorded approver role and an evidence file.",
    ),
}

DISCLOSURES = {
    "client_update": "This note is for explanation only. All data in this sample is synthetic.",
    "custodian_cover": "This letter is for transfer processing only. All data in this sample is synthetic.",
    "internal_digest": "Internal use only. All data in this sample is synthetic.",
}


def draft(
    kind: str,
    household_id: str,
    book: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build one draft.

    ``kind`` is one of ``client_update``, ``custodian_cover``, ``internal_digest``.
    ``book`` defaults to ``data/book.json``. The return shape is stable:
    subject, body, claims, blocked, plus the audience, word count, and policy check
    results the review screen needs.
    """

    if kind not in KINDS:
        raise ValueError(f"Unknown draft kind {kind!r}. Expected one of {KINDS}.")
    if book is None:
        book = load_book()

    meta = book_meta(book)
    household_list = households(book)
    household = _select_household(book, household_id)
    resolved_id = str(household.get("id")) if household else str(household_id or "")

    if household is None and kind != "internal_digest":
        detail = (
            f"Household {household_id!r} was not found in the book, so no facts could be cited."
        )
        return _finalise(
            kind=kind,
            household_id=resolved_id,
            subject="Draft unavailable",
            blocks=[
                _block(
                    "This draft could not be generated because the household is not in the book.",
                    required=True,
                )
            ],
            fillers=(),
            extra_blocked=[{"check": "household_lookup", "detail": detail}],
            book=book,
        )

    if kind == "client_update":
        blocks = _client_blocks(book, household, meta)
    elif kind == "custodian_cover":
        blocks = _custodian_blocks(book, household, meta)
    else:
        blocks = _digest_blocks(book, household, meta, household_list)

    extra_blocked: list[dict[str, Any]] = []
    if kind != "internal_digest" and not accounts(book, resolved_id):
        extra_blocked.append(
            {
                "check": "account_data",
                "detail": f"No accounts are linked to household {resolved_id}.",
            }
        )

    return _finalise(
        kind=kind,
        household_id=resolved_id,
        subject=_subject(kind, household, meta, book),
        blocks=blocks,
        fillers=FILLERS[kind],
        extra_blocked=extra_blocked,
        book=book,
    )


# ---------------------------------------------------------------------------
# Block builders
# ---------------------------------------------------------------------------


def _client_blocks(
    book: dict[str, Any],
    household: dict[str, Any],
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    household_id = str(household.get("id"))
    household_index = _household_index(book, household_id)
    account_rows = accounts(book, household_id)
    account_count = len(account_rows)
    name = household.get("display_name") or household.get("name") or "your household"
    advisor = meta.get("advisor_name") or "your transition team"

    blocks: list[dict[str, Any]] = [
        _block("Hello.", required=True),
    ]

    if account_count:
        count_text = (
            f"This note covers the {_count_phrase(account_count, 'account')} we have "
            f"started to receive for {name}."
        )
        blocks.append(
            _block(
                count_text,
                _claim(
                    _count_phrase(account_count, "account"),
                    "book",
                    "book.accounts",
                    book,
                ),
                required=True,
                break_before=PARAGRAPH,
            )
        )
    else:
        blocks.append(
            _block(
                f"This note covers the accounts we have started to receive for {name}.",
                required=True,
                break_before=PARAGRAPH,
            )
        )

    aum, aum_ref = _household_aum(book, household, household_index, account_rows)
    if aum is not None and aum > 0:
        value_text = f"The accounts on file show a combined value of {money(aum)}."
        blocks.append(
            _block(
                value_text,
                _claim(
                    f"combined value of {money(aum)}",
                    "book",
                    aum_ref,
                    book,
                ),
                required=False,
            )
        )

    # Two account sentences at most. The count sentence already states the total.
    for account, account_index in _account_rows_with_index(book, household_id)[:2]:
        label = account.get("account_type") or account.get("registration") or "an account"
        label_field = "account_type" if account.get("account_type") else "registration"
        masked = str(account.get("masked_number") or "")
        last4 = _last_digits(masked)
        claims: list[dict[str, Any]] = []
        if last4:
            sentence = f"Account ending {last4} is titled {label}."
            claims.append(
                _claim(
                    f"Account ending {last4}",
                    "book",
                    f"book.accounts[{account_index}].masked_number",
                    book,
                )
            )
        else:
            sentence = f"One account is titled {label}."
        if account.get("account_type") or account.get("registration"):
            claims.append(
                _claim(
                    f"titled {label}",
                    "book",
                    f"book.accounts[{account_index}].{label_field}",
                    book,
                )
            )
        blocks.append(_block(sentence, *claims, required=False))

    blocks.extend(
        [
            _block(
                "This note explains what we see in your records.",
                required=True,
                break_before=PARAGRAPH,
            ),
            _block(
                "It does not recommend any change to how your accounts are invested, and it is not investment advice.",
                _claim(
                    "It does not recommend any change and is not investment advice.",
                    "policy",
                    "Firm policy CP-02 (synthetic)",
                    book,
                ),
                required=True,
            ),
            _block(
                "The next step is collecting the signed forms for each account.",
                required=True,
            ),
            _block("Your advisor can explain what each form is for.", required=True),
            _block(f"Thank you, {advisor}", required=True, break_before=PARAGRAPH),
            _block(
                str(meta.get("firm_to") or "The transition team"),
                required=True,
                break_before=LINE,
            ),
            _block(DISCLOSURES["client_update"], required=True, break_before=PARAGRAPH),
        ]
    )
    return blocks


def _custodian_blocks(
    book: dict[str, Any],
    household: dict[str, Any],
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    household_id = str(household.get("id"))
    all_rows = _account_rows_with_index(book, household_id)
    name = household.get("display_name") or household.get("name") or "the household"
    custodian = ""
    if all_rows:
        custodian = str(all_rows[0][0].get("custodian") or "")
    custodian = custodian or str(meta.get("custodian") or "the receiving custodian")
    # A cover letter goes to one custodian; list only that custodian's accounts.
    account_rows = [
        row for row in all_rows if str(row[0].get("custodian") or "") == custodian
    ] or all_rows

    blocks: list[dict[str, Any]] = [
        _block(f"To the transfer team at {custodian},", required=True),
        _block(
            f"Please accept the enclosed transfer documents for {name}.",
            required=True,
            break_before=PARAGRAPH,
        ),
    ]

    if account_rows:
        endings = [
            _last_digits(str(account.get("masked_number") or ""))
            for account, _ in account_rows
        ]
        endings = [ending for ending in endings if ending][:3]
        count = len(account_rows)
        if endings:
            listing = _join_list(endings)
            joiner = "ending" if len(endings) == count else "including"
            sentence = (
                f"The package covers {_count_phrase(count, 'account')} at {custodian}, "
                f"{joiner} {listing}."
            )
            blocks.append(
                _block(
                    sentence,
                    _claim(_count_phrase(count, "account"), "book", "book.accounts", book),
                    *[
                        _claim(
                            ending,
                            "book",
                            f"book.accounts[{account_rows[index][1]}].masked_number",
                            book,
                        )
                        for index, ending in enumerate(endings)
                    ],
                    required=True,
                )
            )
        else:
            sentence = f"The package covers {_count_phrase(count, 'account')} at {custodian}."
            blocks.append(
                _block(
                    sentence,
                    _claim(_count_phrase(count, "account"), "book", "book.accounts", book),
                    required=True,
                )
            )

    blocks.extend(
        [
            _block(
                "Signed authorization and the account applications are attached.",
                required=True,
                break_before=PARAGRAPH,
            ),
            _block("This letter is for transfer processing only.", required=True),
            _block(
                "It does not instruct a trade and does not constitute investment advice.",
                _claim(
                    "This letter does not instruct a trade and is not investment advice.",
                    "policy",
                    "Firm policy CP-02 (synthetic)",
                    book,
                ),
                required=True,
            ),
        ]
    )

    lead = meta.get("transition_lead")
    if lead:
        blocks.append(
            _block(
                f"Questions about the enclosed forms can go to {lead}, the transition lead.",
                required=True,
            )
        )
    else:
        blocks.append(
            _block("Questions about the enclosed forms can go to the transition lead.", required=True)
        )

    blocks.extend(
        [
            _block(
                f"Thank you, {meta.get('advisor_name') or 'The transition team'}",
                required=True,
                break_before=PARAGRAPH,
            ),
            _block(DISCLOSURES["custodian_cover"], required=True, break_before=PARAGRAPH),
        ]
    )
    return blocks


def _digest_blocks(
    book: dict[str, Any],
    household: Optional[dict[str, Any]],
    meta: dict[str, Any],
    household_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plan = build_plan(book)
    summary = plan.get("summary", {})
    account_count = int(summary.get("accounts") or len(accounts(book)))
    aum, aum_ref = _book_aum(book)
    # The digest is book-level, not household-level, even when a household id is passed.
    advisor = meta.get("advisor_name")
    scope = f"{advisor}'s book" if advisor else "the book"

    blocks: list[dict[str, Any]] = [
        _block(f"Internal transition digest for {scope}.", required=True),
        _block(
            f"The book has {_count_phrase(account_count, 'account')} and {money(aum)} in assets."
            if aum
            else f"The book has {_count_phrase(account_count, 'account')}.",
            _claim(_count_phrase(account_count, "account"), "book", "book.accounts", book),
            *(
                [_claim(f"{money(aum)} in assets", "book", aum_ref, book)]
                if aum
                else []
            ),
            required=True,
        ),
    ]

    wave1 = int(summary.get("wave1") or 0)
    blocked = int(summary.get("blocked") or 0)
    high_risks = int(summary.get("high_risks") or 0)
    wave_text = f"Wave 1 covers {_count_phrase(wave1, 'account')}."
    blocks.append(
        _block(
            wave_text,
            _claim(wave_text, "plan", "plan.summary.wave1", book, plan),
            required=True,
        )
    )
    blocked_text = (
        f"{_count_phrase(blocked, 'account')} "
        f"{'is' if blocked == 1 else 'are'} blocked on documentation, and "
        f"{_count_phrase(high_risks, 'high risk')} "
        f"{'is' if high_risks == 1 else 'are'} open."
    )
    blocks.append(
        _block(
            blocked_text,
            _claim(blocked_text, "plan", "plan.summary.blocked", book, plan),
            _claim(blocked_text, "plan", "plan.summary.high_risks", book, plan),
            required=True,
        )
    )

    hard_cases = [item.get("hard_case") for item in plan.get("critical_path", []) if item.get("hard_case")]
    if hard_cases:
        unique_cases = list(dict.fromkeys(hard_cases))[:3]
        sentence = "Critical path items: " + _join_list(unique_cases) + "."
        blocks.append(_block(sentence, required=True))
    else:
        blocks.append(_block("No hard-case items are on the critical path.", required=True))

    blocks.extend(
        [
            _block(
                "Each risk in the plan carries a policy reference and the artifact that clears it.",
                required=True,
                break_before=PARAGRAPH,
            ),
            _block(
                "No client-facing draft is sent without a recorded approver role and an evidence file.",
                required=True,
            ),
            _block(DISCLOSURES["internal_digest"], required=True, break_before=PARAGRAPH),
        ]
    )
    return blocks


# ---------------------------------------------------------------------------
# Assembly, claims, and return shape
# ---------------------------------------------------------------------------


def _block(
    text: str,
    *claims: dict[str, Any],
    required: bool,
    break_before: str = " ",
) -> dict[str, Any]:
    return {
        "text": text,
        "claims": [claim for claim in claims if claim],
        "required": required,
        "break_before": break_before,
    }


def _finalise(
    kind: str,
    household_id: str,
    subject: str,
    blocks: list[dict[str, Any]],
    fillers: tuple[str, ...],
    extra_blocked: list[dict[str, Any]],
    book: dict[str, Any],
) -> dict[str, Any]:
    minimum, maximum = WORD_LIMITS[kind]
    retained = list(blocks)

    # Drop optional blocks from the end until the body fits. No required block carries
    # a number that is not already in another required block.
    while _body_length(retained) > maximum:
        drop_index = next(
            (index for index in range(len(retained) - 1, -1, -1) if not retained[index]["required"]),
            None,
        )
        if drop_index is None:
            break
        retained.pop(drop_index)

    # Add neutral filler only to reach the minimum.
    for filler in fillers:
        if _body_length(retained) >= minimum:
            break
        retained.append(_block(filler, required=False))

    pieces: list[str] = []
    for block in retained:
        if pieces:
            pieces.append(block.get("break_before") or " ")
        pieces.append(block["text"])
    body = "".join(pieces).strip()
    words = word_count(body)
    claims: list[dict[str, Any]] = []
    for block in retained:
        for claim in block["claims"]:
            if claim not in claims:
                claims.append(claim)

    payload = {
        "subject": subject,
        "body": body,
        "claims": claims,
        "blocked": list(extra_blocked),
        "kind": kind,
        "household_id": household_id,
        "audience": AUDIENCE[kind],
        "channel": CHANNEL[kind],
        "word_count": words,
        "mode": MODE_DETERMINISTIC_TEMPLATE,
        "generated_at": utc_now_iso(),
    }
    checks = check_draft(payload)
    payload["policy_checks"] = checks
    for failed in blocking_checks(checks):
        question = {
            "check": failed["check"],
            "detail": failed["detail"],
        }
        if question not in payload["blocked"]:
            payload["blocked"].append(question)

    # A length miss is a product bug, and the caller should see it rather than send
    # a note outside the policy range.
    if not (minimum <= words <= maximum):
        payload["blocked"].append(
            {
                "check": "word_count",
                "detail": f"{kind} body is {words} words; policy range is {minimum} to {maximum}.",
            }
        )
    return payload


def _body_length(blocks: list[dict[str, Any]]) -> int:
    return word_count(" ".join(block["text"] for block in blocks))


def _claim(
    text: str,
    source_type: str,
    source_ref: str,
    book: dict[str, Any],
    plan: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    status = "cited" if _resolves(source_type, source_ref, book, plan) else "uncited"
    return {
        "text": text,
        "source_type": source_type,
        "source_ref": source_ref,
        "status": status,
    }


def _resolves(
    source_type: str,
    source_ref: str,
    book: dict[str, Any],
    plan: Optional[dict[str, Any]] = None,
) -> bool:
    if source_type == "policy":
        return bool(source_ref)
    if not source_ref:
        return False
    if source_ref.startswith("plan."):
        if plan is None:
            return False
        found, _ = get_path(plan, source_ref[len("plan."):])
        return bool(found)
    if source_ref.startswith("book."):
        tail = source_ref[len("book."):]
        found, _ = get_path(book, tail)
        if found:
            return True
        nested = book.get("book")
        if isinstance(nested, dict):
            found, _ = get_path(nested, tail)
            return bool(found)
        return False
    found, _ = get_path(book, source_ref)
    return bool(found)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _select_household(
    book: dict[str, Any], household_id: Optional[str]
) -> Optional[dict[str, Any]]:
    if household_id:
        return find_household(book, str(household_id))
    candidates = households(book)
    return candidates[0] if candidates else None


def _household_index(book: dict[str, Any], household_id: str) -> Optional[int]:
    for index, household in enumerate(households(book)):
        if str(household.get("id")) == str(household_id):
            return index
    return None


def _account_rows_with_index(
    book: dict[str, Any], household_id: str
) -> list[tuple[dict[str, Any], int]]:
    rows: list[tuple[dict[str, Any], int]] = []
    for index, account in enumerate(accounts(book)):
        if str(account.get("household_id") or account.get("household") or "") == str(household_id):
            rows.append((account, index))
    return rows


def _household_aum(
    book: dict[str, Any],
    household: dict[str, Any],
    household_index: Optional[int],
    account_rows: list[dict[str, Any]],
) -> tuple[Optional[float], str]:
    value = to_number(household.get("aum"))
    if value is not None and household_index is not None:
        return value, f"book.households[{household_index}].aum"
    total = 0.0
    found = False
    for account in account_rows:
        for position in positions(book, str(account.get("id"))):
            position_value = to_number(position.get("value"))
            if position_value is not None:
                total += position_value
                found = True
    if found:
        return total, "book.accounts"
    return None, ""


def _book_aum(book: dict[str, Any]) -> tuple[Optional[float], str]:
    meta = book_meta(book)
    value = to_number(meta.get("aum_total"))
    if value is not None:
        return value, "book.book.aum_total"
    total = 0.0
    found = False
    for account in accounts(book):
        for position in positions(book, str(account.get("id"))):
            position_value = to_number(position.get("value"))
            if position_value is not None:
                total += position_value
                found = True
    if found:
        return total, "book.accounts"
    return None, ""


def _count_phrase(count: int, singular: str, plural: Optional[str] = None) -> str:
    noun = singular if int(count) == 1 else (plural or f"{singular}s")
    return f"{int(count)} {noun}"


def _last_digits(value: str) -> str:
    digits = re.findall(r"\d{3,6}", value)
    return digits[-1] if digits else ""


def _join_list(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _subject(
    kind: str,
    household: Optional[dict[str, Any]],
    meta: dict[str, Any],
    book: dict[str, Any],
) -> str:
    name = (household or {}).get("display_name") or (household or {}).get("name") or "your household"
    if kind == "client_update":
        return f"Your transition status: {name}"
    if kind == "custodian_cover":
        return f"Transfer documents for {name}"
    return "Transition digest"
