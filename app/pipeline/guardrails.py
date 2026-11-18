"""Policy checks for a draft before it can be approved.

Six named checks, each returning ``pass``, ``warn``, or ``fail``. The pattern
lists are deliberately readable and commented so a reviewer can see exactly what the
filters do and what they miss. These are drafting controls, not legal advice. The
policy references are synthetic.

Check order is stable and is what the review screen renders:

1. ``advice_language`` - recommendations, transaction instructions, guarantees
2. ``promised_timing`` - timelines the custodian or carrier controls
3. ``performance_claim`` - returns, yields, relative performance
4. ``testimonial`` - testimonials, endorsements, ratings
5. ``uncited_number`` - a number in the body with no cited claim behind it
6. ``missing_disclosure`` - audience-appropriate explanation disclaimer
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from . import iter_numbers

CHECKS = (
    "advice_language",
    "promised_timing",
    "performance_claim",
    "testimonial",
    "uncited_number",
    "missing_disclosure",
)

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


def check_draft(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """Run all six checks and return them in a stable order.

    ``draft`` needs at least ``body`` and ``claims``. ``kind`` or ``audience``
    selects the disclosure standard. Missing keys are treated as empty, not as a
    crash, because the UI renders a check panel for partially built drafts.
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


def blocking_checks(checks: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """Convenience for the UI: the failed checks that block approval."""

    return [check for check in (checks or []) if check.get("result") == "fail"]
