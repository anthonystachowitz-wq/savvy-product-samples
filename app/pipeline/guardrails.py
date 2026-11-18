"""Policy checks for a draft before it can be approved.

Seven named checks, each returning ``pass``, ``warn``, or ``fail``. The pattern
lists are deliberately readable and commented so a reviewer can see exactly what the
filters do and what they miss. These are drafting controls, not legal advice. The
policy references are synthetic.

Check order is stable and is what the review screen renders. The first six predate the
Broker Protocol work and keep their order; ``protocol_consent`` is appended after them.

1. ``advice_language`` - recommendations, transaction instructions, guarantees
2. ``promised_timing`` - timelines the custodian or carrier controls
3. ``performance_claim`` - returns, yields, relative performance
4. ``testimonial`` - testimonials, endorsements, ratings
5. ``uncited_number`` - a number in the body with no cited claim behind it
6. ``missing_disclosure`` - audience-appropriate explanation disclaimer
7. ``protocol_consent`` - client-facing work for a household with pending consent
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from . import find_household, iter_numbers, load_book

CHECKS = (
    "advice_language",
    "promised_timing",
    "performance_claim",
    "testimonial",
    "uncited_number",
    "missing_disclosure",
    "protocol_consent",
)

#: The five data elements the Broker Protocol permits, in the order the packet lists
#: them. The book's ``book.protocol.permitted_fields`` wins when it carries its own
#: wording; these are the fallback and the labels the UI can render.
PROTOCOL_PERMITTED_FIELDS = (
    "client name",
    "address",
    "phone",
    "email",
    "account title",
)

#: The restriction that matters most for a draft queue: no client contact yet.
PROTOCOL_NO_CONTACT = "no client contact before resignation"

# ---------------------------------------------------------------------------
# Pattern lists
# ---------------------------------------------------------------------------

#: Recommendation and transaction language. The line between explaining an account
#: and advising on it is specificity plus personalization plus a recommended action,
#: so these patterns target the action verbs, not the topic words. "Recommend" only
#: counts with a first-person subject: a disclosure that says "this does not recommend
#: a change" must pass.
ADVICE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:we|i|our team|your advisor)\s+(?:strongly\s+)?(?:recommend|suggest|advise|urge)\b", "recommendation"),
    (r"\b(?:our|my)\s+recommendation\b", "recommendation"),
    (r"\byou\s+(?:should|ought to|need to|must)\b", "instruction to the client"),
    (r"\b(?:buy|sell|purchase|redeem|rebalance|switch|liquidate)\b", "transaction verb"),
    (r"\broll(?:ing)?\s+(?:it|the|your|this)\s+(?:over|into)\b", "rollover instruction"),
    (r"\b(?:increase|reduce|raise|lower)\s+(?:your|the)\s+(?:position|allocation|exposure)\b", "allocation instruction"),
    (r"\bmove\s+(?:your|the client's)\s+(?:money|assets|funds)\s+into\b", "allocation instruction"),
    (r"\b(?:guaranteed|risk[- ]free|can't lose|cannot lose|assured return)\b", "guarantee"),
    (r"\bwe(?:'ll| will)\s+(?:put|move)\s+you\s+into\b", "recommendation"),
    (r"\bin our (?:view|opinion)\b", "recommendation framing"),
    (r"\bthe best (?:option|choice|fund|allocation)\b", "recommendation framing"),
)

#: Promises about timing. Custodian and carrier review clocks are outside the firm's
#: control, so a draft must not commit to them. Saying "the custodian controls the
#: timeline" is fine because it makes no commitment.
TIMING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:will|should|expected to|scheduled to)\s+(?:be\s+)?(?:complete|completed|funded|transferred|settled|processed|available|delivered)\b", "completion promise"),
    (r"\bwithin\s+\d+\s*(?:business\s+)?(?:days?|weeks?|hours?)\b", "fixed service window"),
    (r"\bby\s+(?:the\s+)?(?:end\s+of\s+)?(?:this\s+|next\s+)?(?:week|month|quarter|friday|monday|tuesday|wednesday|thursday)\b", "deadline promise"),
    (r"\bon\s+or\s+before\s+\w+", "deadline promise"),
    (r"\b(?:guaranteed|promised)\s+(?:timeline|date|completion|settlement)\b", "guaranteed timing"),
    (r"\b\d+\s*(?:business\s+)?(?:days?|weeks?)\s+(?:to|until|before)\s+(?:fund|funding|settle|settlement|complete|completion)\b", "service window"),
    (r"\b(?:acats|transfer|account opening)\s+(?:settles?|completes?)\s+in\s+\d+", "service window"),
    (r"\b(?:funds|assets)\s+(?:will|should)\s+(?:arrive|land|post)\b", "completion promise"),
)

#: Performance and return language. "Return" as a verb ("return the form") is
#: excluded, and "tax return" is excluded, because neither is a performance claim.
PERFORMANCE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b(?:total|annual|annualized|net|gross|historical|past|recent)\s+returns?\b", "return claim"),
    (r"\breturns?\s+(?:of|since|over|for the|on)\b", "return claim"),
    (r"\b(?:out|under)perform(?:ed|s|ing)?\b", "relative performance"),
    (r"\bbeat(?:ing)?\s+the\s+(?:market|benchmark|index)\b", "relative performance"),
    (r"\b(?:gained|gain|earned|appreciated|appreciation)\b", "gain claim"),
    (r"\b(?:returned|gained|earned)\s+\d+(?:\.\d+)?\s*%", "percentage return"),
    (r"\b\d+(?:\.\d+)?\s*%\s*(?:return|gain|yield|annualized|growth)\b", "percentage return"),
    (r"\byield(?:ed|s|ing)?\b", "yield claim"),
    (r"\bannualized\b", "annualized claim"),
    (r"\bperformance\b", "performance claim"),
    (r"\brisk[- ]adjusted\b", "performance claim"),
    (r"\balpha\b", "performance claim"),
    (r"\bprofit(?:s|ed|able)?\b", "profit claim"),
)

#: Testimonial and endorsement language, plus ratings. The marketing rule treats
#: these as a separate category with their own disclosure requirements.
TESTIMONIAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\btestimonials?\b", "testimonial"),
    (r"\bendorsements?\b", "endorsement"),
    (r"\bour clients?\s+(?:love|say|tell us|rave)\b", "client quote"),
    (r"\b(?:five|5)[- ]star\b", "rating"),
    (r"\b(?:rated|rating)\b", "rating"),
    (r"\b(?:best|top)\s+(?:advisor|adviser|firm|RIA)\b", "ranking claim"),
    (r"\baward[- ]winning\b", "award claim"),
    (r'["\u201c][^"\u201d]{12,}["\u201d]\s*(?:[-\u2014]|said|says)', "quoted client statement"),
    (r"\bcase study\b", "endorsement framing"),
)

#: Disclosure markers by audience. Client notes must say the note explains rather
#: than advises. Custodian covers must say the letter is for processing only. Internal
#: notes carry no client disclosure but should be marked internal.
CLIENT_DISCLOSURE_MARKERS = (
    "not investment advice",
    "does not constitute investment advice",
    "does not provide investment advice",
    "explanation only",
    "for explanation",
    "explain-only",
)
CLIENT_ROUTE_MARKERS = (
    "advisor",
    "adviser",
    "transition lead",
)
CUSTODIAN_DISCLOSURE_MARKERS = (
    "transfer processing",
    "does not instruct",
    "processing only",
    "does not constitute investment advice",
)
INTERNAL_MARKERS = ("internal use", "internal only", "internal digest")


def check_draft(
    draft: dict[str, Any], book: Optional[dict[str, Any]] = None
) -> list[dict[str, Any]]:
    """Run all seven checks and return them in a stable order.

    ``draft`` needs at least ``body`` and ``claims``. ``kind`` or ``audience``
    selects the disclosure standard. Missing keys are treated as empty, not as a
    crash, because the UI renders a check panel for partially built drafts.

    ``book`` supplies the household consent records for ``protocol_consent``. When it
    is omitted the check falls back to ``data/book.json``, but only for a client-facing
    draft that names a household.
    """

    if not isinstance(draft, dict):
        raise TypeError("check_draft expects a draft dict")

    body = str(draft.get("body") or "")
    claims = draft.get("claims") if isinstance(draft.get("claims"), list) else []

    return [
        _check_patterns("advice_language", body, ADVICE_PATTERNS, "recommendation, transaction, or guarantee language"),
        _check_patterns("promised_timing", body, TIMING_PATTERNS, "timing promise"),
        _check_patterns("performance_claim", body, PERFORMANCE_PATTERNS, "performance or return claim"),
        _check_patterns("testimonial", body, TESTIMONIAL_PATTERNS, "testimonial, endorsement, or rating"),
        _check_uncited_numbers(body, claims),
        _check_disclosure(draft, body),
        _check_protocol_consent(draft, book),
    ]


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------


def _check_patterns(
    name: str,
    body: str,
    patterns: Iterable[tuple[str, str]],
    label: str,
) -> dict[str, Any]:
    hits: list[str] = []
    for pattern, reason in patterns:
        for match in re.finditer(pattern, body, flags=re.IGNORECASE):
            phrase = match.group(0).strip()
            if phrase and f"{phrase} ({reason})" not in hits:
                hits.append(f"{phrase} ({reason})")
    if hits:
        shown = "; ".join(hits[:4])
        extra = "" if len(hits) <= 4 else f" and {len(hits) - 4} more"
        return {
            "check": name,
            "result": "fail",
            "detail": f"Found {label}: {shown}{extra}.",
        }
    return {"check": name, "result": "pass", "detail": f"No {label} found."}


def _normalise_number(token: str) -> str:
    return token.replace(",", "").strip()


def _check_uncited_numbers(body: str, claims: list[Any]) -> dict[str, Any]:
    """Every number in the body must appear in a cited claim.

    Claims with ``status: uncited`` or a blank ``source_ref`` do not count. Matching
    is token to token after comma removal, so "4821" matches "4821" and does not match
    inside "148219".
    """

    body_tokens = [_normalise_number(token) for token in iter_numbers(body)]
    if not body_tokens:
        return {
            "check": "uncited_number",
            "result": "pass",
            "detail": "The body contains no numbers.",
        }

    cited_tokens: set[str] = set()
    uncited_claims: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("text") or "")
        tokens = {_normalise_number(token) for token in iter_numbers(text)}
        status = str(claim.get("status") or "").lower()
        source_ref = str(claim.get("source_ref") or "").strip()
        if status == "cited" and source_ref:
            cited_tokens.update(tokens)
        elif tokens:
            uncited_claims.append(text[:80])

    missing = [token for token in body_tokens if token not in cited_tokens]
    if missing:
        detail = (
            "These numbers have no cited claim: "
            + ", ".join(dict.fromkeys(missing))
            + "."
        )
        if uncited_claims:
            detail += " Claims without a source: " + "; ".join(uncited_claims[:3]) + "."
        return {"check": "uncited_number", "result": "fail", "detail": detail}

    if uncited_claims:
        return {
            "check": "uncited_number",
            "result": "warn",
            "detail": (
                "Every body number is cited, but some claims carry no source: "
                + "; ".join(uncited_claims[:3])
                + "."
            ),
        }
    return {
        "check": "uncited_number",
        "result": "pass",
        "detail": f"All {len(set(body_tokens))} distinct numbers in the body trace to cited claims.",
    }


def _audience(draft: dict[str, Any]) -> str:
    audience = str(draft.get("audience") or "").strip().lower()
    if audience in {"client", "custodian", "internal"}:
        return audience
    kind = str(draft.get("kind") or "").strip().lower()
    return {
        "client_update": "client",
        "custodian_cover": "custodian",
        "internal_digest": "internal",
    }.get(kind, "client")


def _check_disclosure(draft: dict[str, Any], body: str) -> dict[str, Any]:
    audience = _audience(draft)
    disclosures = draft.get("disclosures")
    disclosure_text = ""
    if isinstance(disclosures, list):
        disclosure_text = " ".join(str(item) for item in disclosures)
    elif disclosures:
        disclosure_text = str(disclosures)
    text = f"{body} {disclosure_text}".lower()

    if audience == "client":
        has_disclosure = any(marker in text for marker in CLIENT_DISCLOSURE_MARKERS)
        has_route = any(marker in text for marker in CLIENT_ROUTE_MARKERS)
        if not has_disclosure:
            return {
                "check": "missing_disclosure",
                "result": "fail",
                "detail": (
                    "Client-facing note needs an explain-only disclosure such as "
                    '"This note is for explanation only and is not investment advice."'
                ),
            }
        if not has_route:
            return {
                "check": "missing_disclosure",
                "result": "warn",
                "detail": "The note does not tell the client where questions go (advisor).",
            }
        return {
            "check": "missing_disclosure",
            "result": "pass",
            "detail": "Explain-only disclosure and a route to the advisor are present.",
        }

    if audience == "custodian":
        if not any(marker in text for marker in CUSTODIAN_DISCLOSURE_MARKERS):
            return {
                "check": "missing_disclosure",
                "result": "fail",
                "detail": (
                    "Custodian cover needs a processing-only statement, for example "
                    '"This letter is for transfer processing only."'
                ),
            }
        return {
            "check": "missing_disclosure",
            "result": "pass",
            "detail": "Processing-only statement is present.",
        }

    if not any(marker in text for marker in INTERNAL_MARKERS):
        return {
            "check": "missing_disclosure",
            "result": "warn",
            "detail": "Internal draft is not marked internal use only.",
        }
    return {
        "check": "missing_disclosure",
        "result": "pass",
        "detail": "Internal-use marker is present; no client disclosure is required.",
    }


# ---------------------------------------------------------------------------
# Broker Protocol consent
# ---------------------------------------------------------------------------


def consent_blocked(
    book: Optional[dict[str, Any]], household_id: str
) -> tuple[bool, str]:
    """Whether a client-facing draft for this household is blocked, and why.

    Returns ``(True, detail)`` when the household's ``consent_status`` is ``pending``,
    and ``(False, detail)`` when consent is received, not required, or not on file.
    The detail is written to be shown as-is in the UI, so it names the household and
    the rule rather than only returning a boolean.

    A household with no consent record cannot be shown to be blocked, so it does not
    gate the draft; the detail says so instead of implying consent was received.
    """

    if not isinstance(book, dict) or not book:
        return False, "No book is loaded, so no protocol consent record is available."
    household = find_household(book, str(household_id))
    if household is None:
        return False, f"Household {household_id!r} is not in the book, so consent cannot be checked."
    status = str(household.get("consent_status") or "").strip().lower()
    if status == "pending":
        return True, _pending_consent_detail(book, str(household.get("id") or household_id))
    if status == "received":
        return False, f"Consent is received for household {household_id}; a client-facing draft is permitted."
    if status == "not required":
        return False, (
            f"Household {household_id} does not require protocol consent (new account, "
            "new money), so a client-facing draft is permitted."
        )
    return False, (
        f"Household {household_id} has no consent status on file, so the protocol check "
        "does not gate this draft."
    )


def _permitted_fields(book: Optional[dict[str, Any]]) -> list[str]:
    """The protocol's permitted data elements, from the book when it carries them."""

    meta = book.get("book") if isinstance(book, dict) else None
    protocol = meta.get("protocol") if isinstance(meta, dict) else None
    fields = protocol.get("permitted_fields") if isinstance(protocol, dict) else None
    if isinstance(fields, list):
        cleaned = [str(field).strip() for field in fields if str(field).strip()]
        if cleaned:
            return cleaned
    return list(PROTOCOL_PERMITTED_FIELDS)


def _pending_consent_detail(book: Optional[dict[str, Any]], household_id: str) -> str:
    fields = ", ".join(field.lower() for field in _permitted_fields(book))
    return (
        f"Broker Protocol consent is pending for household {household_id}. Only the five "
        f"permitted data elements may be used until consent is received: {fields}. "
        f"{PROTOCOL_NO_CONTACT.capitalize()}. This draft is client-facing, so it stays "
        "blocked until consent is received."
    )


def _check_protocol_consent(
    draft: dict[str, Any], book: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Fail a client-facing draft when the household's consent is still pending.

    Custodian covers and internal digests are not client-facing, so the protocol does
    not gate them and the check still reports a pass.
    """

    if _audience(draft) != "client":
        return {
            "check": "protocol_consent",
            "result": "pass",
            "detail": "The draft is not client-facing, so Broker Protocol consent does not gate it.",
        }

    household_id = str(draft.get("household_id") or draft.get("household") or "").strip()
    if not household_id:
        return {
            "check": "protocol_consent",
            "result": "pass",
            "detail": "No household is attached to the draft, so no consent record applies.",
        }

    if book is None:
        embedded = draft.get("book")
        book = embedded if isinstance(embedded, dict) else load_book()
    blocked, detail = consent_blocked(book, household_id)
    return {
        "check": "protocol_consent",
        "result": "fail" if blocked else "pass",
        "detail": detail,
    }


def blocking_checks(checks: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """Convenience for the UI: the failed checks that block approval."""

    return [check for check in (checks or []) if check.get("result") == "fail"]
