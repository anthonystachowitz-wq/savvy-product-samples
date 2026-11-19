#!/usr/bin/env python3
"""Build the supervision module data from data/book.json.

Deterministic by construction: no clock, no randomness, no network. Two runs
produce byte-identical files. Run from the repository root:

    python3 scripts/generate_supervision.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BOOK_PATH = REPO_ROOT / "data" / "book.json"
OUT_DIR = REPO_ROOT / "data" / "modules" / "supervision"

AS_OF = "2026-11-03"
BRAND = "Whitfield Wealth Partners"
ADVISER = "Dana Whitfield"
REVIEWER = "Marta Ellsworth"
REVIEWER_ROLE = "Chief Compliance Officer"


def _dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _usd(amount: int) -> str:
    return "USD {:,}".format(amount)


def _audiences(households: list[dict]) -> list[dict]:
    received = [h["id"] for h in households if h["consent_status"] == "received"]
    pending = [h["id"] for h in households if h["consent_status"] == "pending"]
    no_marketing = [
        h["id"] for h in households if h["consent_status"] not in ("received", "pending")
    ]
    return [
        {
            "id": "consent_received",
            "label": "Clients with consent on file",
            "household_ids": received,
            "count": len(received),
            "blocked": False,
            "note": "Marketing is permitted for these households.",
        },
        {
            "id": "all_book",
            "label": "All households in the book",
            "household_ids": [h["id"] for h in households],
            "count": len(households),
            "blocked": True,
            "pending_ids": pending,
            "no_marketing_ids": no_marketing,
            "note": (
                "{n} households have consent pending and {m} are not cleared for marketing. "
                "The server refuses this audience."
            ).format(n=len(pending), m=len(no_marketing)),
        },
    ]


def _fact_sheet(book: dict, households: list[dict]) -> dict:
    totals = sum(int(h["aum"]) for h in households)
    declared = int(book["book"]["aum_total"])
    if totals != declared:
        raise SystemExit(
            "Household AUM {totals:,} does not match book aum_total {declared:,}".format(
                totals=totals, declared=declared
            )
        )
    facts = [
        {
            "id": "F-01",
            "section": "Firm",
            "label": "Assets under management",
            "statement": "{brand} advises {aum} for {n} client households as of {as_of}.".format(
                brand=BRAND, aum=_usd(totals), n=len(households), as_of=AS_OF
            ),
            "values": [_usd(totals), str(len(households))],
            "source": "book record, data/book.json aum_total",
            "as_of": AS_OF,
        },
        {
            "id": "F-02",
            "section": "Firm",
            "label": "Households served",
            "statement": "{brand} serves {n} client households.".format(
                brand=BRAND, n=len(households)
            ),
            "values": [str(len(households))],
            "source": "book record, data/book.json households",
            "as_of": AS_OF,
        },
        {
            "id": "F-03",
            "section": "Firm",
            "label": "Regulatory status",
            "statement": (
                "{brand} is a registered investment adviser in the United States. "
                "Registration does not imply a certain level of skill or training."
            ).format(brand=BRAND),
            "values": [],
            "source": "firm record, synthetic",
            "as_of": AS_OF,
        },
        {
            "id": "F-04",
            "section": "Market",
            "label": "Large cap index, 2025",
            "statement": "The S&P 500 total return was 12.4% in 2025.",
            "values": ["12.4%", "2025"],
            "source": "synthetic index series, invented for this demo",
            "as_of": "2026-01-15",
        },
        {
            "id": "F-05",
            "section": "Market",
            "label": "US bond index, 2025",
            "statement": "The Bloomberg US Aggregate Bond Index returned 3.1% in 2025.",
            "values": ["3.1%", "2025"],
            "source": "synthetic index series, invented for this demo",
            "as_of": "2026-01-15",
        },
        {
            "id": "F-06",
            "section": "Market",
            "label": "Developed markets index, 2025",
            "statement": "The MSCI EAFE Index returned 9.6% in 2025.",
            "values": ["9.6%", "2025"],
            "source": "synthetic index series, invented for this demo",
            "as_of": "2026-01-15",
        },
        {
            "id": "F-07",
            "section": "Product",
            "label": "Halcyon Balanced Fund, Class A",
            "statement": "Halcyon Balanced Fund Class A has a 0.72% annual expense ratio.",
            "values": ["0.72%"],
            "source": "fund fact card, synthetic",
            "as_of": "2026-09-30",
        },
        {
            "id": "F-08",
            "section": "Product",
            "label": "Total market ETF",
            "statement": "The total market ETF used in client accounts has a 0.03% annual expense ratio.",
            "values": ["0.03%"],
            "source": "fund fact card, synthetic",
            "as_of": "2026-09-30",
        },
        {
            "id": "F-09",
            "section": "Planning",
            "label": "IRA contribution limits, 2026",
            "statement": (
                "The 2026 IRA contribution limit is USD 7,000 for owners under 50 "
                "and USD 8,000 for owners 50 and older."
            ),
            "values": ["2026", "USD 7,000", "USD 8,000", "50"],
            "source": "synthetic tax reference, invented for this demo",
            "as_of": "2026-10-01",
        },
        {
            "id": "F-10",
            "section": "Planning",
            "label": "Required minimum distributions",
            "statement": (
                "Required minimum distributions begin at age 73 for owners born in 1951 or later."
            ),
            "values": ["73", "1951"],
            "source": "synthetic tax reference, invented for this demo",
            "as_of": "2026-10-01",
        },
        {
            "id": "F-11",
            "section": "Planning",
            "label": "Review calendar",
            "statement": (
                "Quarterly review windows are the first two weeks of January, April, July, and October."
            ),
            "values": ["January", "April", "July", "October", "two"],
            "source": "firm calendar, synthetic",
            "as_of": AS_OF,
        },
    ]
    disclosures = [
        {
            "id": "D-01",
            "label": "Performance",
            "text": "Past performance does not guarantee future results.",
        },
        {
            "id": "D-02",
            "label": "Index data",
            "text": (
                "Index returns are unmanaged, do not reflect fees or expenses, "
                "and cannot be invested in directly."
            ),
        },
        {
            "id": "D-03",
            "label": "Registration",
            "text": (
                "Whitfield Wealth Partners is a registered investment adviser. "
                "Registration does not imply a certain level of skill or training."
            ),
        },
        {
            "id": "D-04",
            "label": "Audience",
            "text": (
                "This material is for the named recipient only and is not investment, "
                "tax, or legal advice."
            ),
        },
        {
            "id": "D-05",
            "label": "Testimonials",
            "text": "Client testimonials are not used in firm marketing.",
        },
        {
            "id": "D-06",
            "label": "Tax",
            "text": "Consult your tax adviser about your own situation before acting.",
        },
    ]
    return {
        "id": "FS-2026-Q4",
        "title": "Approved marketing fact sheet, Q4 2026",
        "brand": BRAND,
        "as_of": AS_OF,
        "approved_by": REVIEWER + ", " + REVIEWER_ROLE,
        "review_due": "2027-02-03",
        "version": 3,
        "note": (
            "Synthetic reference facts for this product sample. The index, fund, and tax "
            "figures are invented and must not be used as real market or tax data."
        ),
        "facts": facts,
        "disclosures": disclosures,
    }


def _briefs(audiences: list[dict]) -> list[dict]:
    return [
        {
            "id": "B-01",
            "key": "market-update",
            "title": "Q4 2026 market update",
            "channel": "Email",
            "audience_type": "households",
            "default_audience": "consent_received",
            "cadence": "Quarterly",
            "objective": (
                "Explain what the 2025 index returns were without forecasting, and remind "
                "clients that the practice does not change a plan because of one year of returns."
            ),
            "key_points": [
                "State the 2025 total return for each of the three index series on the fact sheet, with its value.",
                "Say that index returns are unmanaged and do not reflect fees or expenses.",
                "Say that the practice reviews the plan in the quarterly windows rather than reacting to one year.",
                "Close with the required disclosures by id.",
            ],
            "must_use": [],
            "tone": "Calm and factual. No forecast, no recommendation, no advice.",
            "word_limit": "150 to 190 words",
            "required_disclosures": ["D-01", "D-02", "D-03", "D-04"],
            "fact_ids": ["F-01", "F-04", "F-05", "F-06", "F-11"],
            "status": "ready",
            "draft_key": "draft-B-01",
            "claims_key": "claims-B-01",
            "revision_key": None,
            "revision_claims_key": None,
            "seeded_risk": (
                "One supported performance claim is expected. The policy warns on performance "
                "claims but does not fail them when the number is on the fact sheet."
            ),
        },
        {
            "id": "B-02",
            "key": "tax-reminder",
            "title": "Tax-season reminder for 2026",
            "channel": "Email",
            "audience_type": "households",
            "default_audience": "consent_received",
            "cadence": "Annual",
            "objective": (
                "Tell clients which contribution limit and distribution age matter this season, "
                "without giving tax advice."
            ),
            "key_points": [
                "State the 2026 IRA contribution limits for owners under 50 and for owners 50 and older.",
                "State the age at which required minimum distributions begin.",
                "Say the practice does not give tax advice and that clients should confirm details with their tax preparer.",
                "Point to the quarterly review windows for questions.",
            ],
            "must_use": [],
            "tone": "Plain and useful. No tax advice and no deadline pressure.",
            "word_limit": "140 to 170 words",
            "required_disclosures": ["D-03", "D-04", "D-06"],
            "fact_ids": ["F-09", "F-10", "F-11"],
            "status": "ready",
            "draft_key": "draft-B-02",
            "claims_key": "claims-B-02",
            "revision_key": None,
            "revision_claims_key": None,
            "seeded_risk": (
                "None. The tax figures are synthetic and labelled as such on the fact sheet."
            ),
        },
        {
            "id": "B-03",
            "key": "client-newsletter",
            "title": "Client newsletter, winter 2026",
            "channel": "Email",
            "audience_type": "households",
            "default_audience": "consent_received",
            "cadence": "Quarterly",
            "objective": (
                "Give clients a short practice update: the size of the practice, the review "
                "calendar, one plain cost fact, and a statement of how the firm is registered."
            ),
            "key_points": [
                "State how many client households the practice serves.",
                "State the quarterly review windows.",
                "Mention the annual expense ratio of the total market ETF used in client accounts.",
                "State that the practice is a registered investment adviser and that registration does not imply a certain level of skill or training.",
            ],
            "must_use": [],
            "tone": "Warm but restrained. No testimonial, no client story, no numbers beyond the fact sheet.",
            "word_limit": "130 to 160 words",
            "required_disclosures": ["D-03", "D-04"],
            "fact_ids": ["F-02", "F-03", "F-08", "F-11"],
            "status": "ready",
            "draft_key": "draft-B-03",
            "claims_key": "claims-B-03",
            "revision_key": None,
            "revision_claims_key": None,
            "seeded_risk": "None. No performance claim belongs in this piece.",
        },
        {
            "id": "B-04",
            "key": "social-post",
            "title": "Social post, adviser-supplied performance line",
            "channel": "Social post",
            "audience_type": "public",
            "default_audience": "public",
            "cadence": "One-off",
            "objective": (
                "A short public post about planning for the year ahead, using one line the "
                "adviser wants to keep."
            ),
            "key_points": [
                "Keep it under 90 words.",
                "Use the adviser-supplied line exactly once.",
                "State that the practice is a registered investment adviser.",
                "Close with the required disclosures by id.",
            ],
            "must_use": ["Our clients have earned 14.2% a year since 2019."],
            "tone": "Short and plain. No performance promise.",
            "word_limit": "60 to 90 words",
            "required_disclosures": ["D-01", "D-02", "D-03"],
            "fact_ids": ["F-03", "F-11"],
            "status": "ready",
            "draft_key": "draft-B-04",
            "claims_key": "claims-B-04",
            "revision_key": "revise-B-04",
            "revision_claims_key": "claims-B-04-v2",
            "seeded_risk": (
                "The adviser pasted a performance line into the brief. 14.2% is not on the fact "
                "sheet, so the second pass and the server gate both flag it. The draft cannot be "
                "approved until it is revised."
            ),
        },
    ]


def _eval_cases() -> list[dict]:
    return [
        {
            "id": "E-01",
            "claim": (
                "Whitfield Wealth Partners advises USD 42,300,000 for 12 client households."
            ),
            "label": "supported",
            "expected_type": "factual",
            "expected_support": "supported",
            "source": "F-01",
            "note": "Two figures, both on F-01.",
        },
        {
            "id": "E-02",
            "claim": "Halcyon Balanced Fund Class A has a 0.72% annual expense ratio.",
            "label": "supported",
            "expected_type": "factual",
            "expected_support": "supported",
            "source": "F-07",
            "note": "Product fact, value matches F-07.",
        },
        {
            "id": "E-03",
            "claim": (
                "Quarterly review windows are the first two weeks of January, April, July, and October."
            ),
            "label": "supported",
            "expected_type": "factual",
            "expected_support": "supported",
            "source": "F-11",
            "note": "Process fact, matches F-11 word for word.",
        },
        {
            "id": "E-04",
            "claim": "The practice has grown 38% since 2023.",
            "label": "unsupported",
            "expected_type": "factual",
            "expected_support": "unsupported",
            "source": "none",
            "note": "No fact carries 38% or 2023.",
        },
        {
            "id": "E-05",
            "claim": "Clients hold an average of 14 positions across their accounts.",
            "label": "unsupported",
            "expected_type": "factual",
            "expected_support": "unsupported",
            "source": "none",
            "note": "Position counts are book data, not approved marketing facts.",
        },
        {
            "id": "E-06",
            "claim": "Whitfield Wealth Partners manages USD 61,000,000 for clients.",
            "label": "unsupported",
            "expected_type": "factual",
            "expected_support": "unsupported",
            "source": "F-01",
            "note": "Wrong AUM, cited to F-01. The source check should catch the mismatch.",
        },
        {
            "id": "E-07",
            "claim": "The S&P 500 total return was 12.4% in 2025.",
            "label": "performance",
            "expected_type": "performance",
            "expected_support": "supported",
            "source": "F-04",
            "note": "Supported by F-04, but it is still a performance claim and must be flagged.",
        },
        {
            "id": "E-08",
            "claim": "The MSCI EAFE Index returned 9.6% in 2025.",
            "label": "performance",
            "expected_type": "performance",
            "expected_support": "supported",
            "source": "F-06",
            "note": "Supported by F-06. The type matters, not only the support.",
        },
        {
            "id": "E-09",
            "claim": (
                "One client told us the plan finally made sense after the first review."
            ),
            "label": "testimonial",
            "expected_type": "testimonial",
            "expected_support": "unsupported",
            "source": "none",
            "note": "A client statement with no release on file.",
        },
        {
            "id": "E-10",
            "claim": (
                "Our clients say they sleep better since we took over the paperwork."
            ),
            "label": "testimonial",
            "expected_type": "testimonial",
            "expected_support": "unsupported",
            "source": "none",
            "note": "Implied client testimonial, no source.",
        },
        {
            "id": "E-11",
            "claim": "You will earn at least 7% a year with our income strategy.",
            "label": "promised_return",
            "expected_type": "promised_return",
            "expected_support": "unsupported",
            "source": "none",
            "note": "A promise about a future return.",
        },
        {
            "id": "E-12",
            "claim": "We guarantee a 6% annual return on the conservative sleeve.",
            "label": "promised_return",
            "expected_type": "promised_return",
            "expected_support": "unsupported",
            "source": "none",
            "note": "A guarantee. The firm cannot make one.",
        },
    ]


def main() -> int:
    book = json.loads(BOOK_PATH.read_text(encoding="utf-8"))
    households = book["households"]
    audiences = _audiences(households)
    briefs = _briefs(audiences)
    fact_sheet = _fact_sheet(book, households)
    cases = _eval_cases()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _dump(OUT_DIR / "briefs.json", {
        "as_of": AS_OF,
        "brand": BRAND,
        "adviser": ADVISER,
        "generated_from": "data/book.json",
        "reviewer": REVIEWER,
        "reviewer_role": REVIEWER_ROLE,
        "audiences": audiences,
        "briefs": briefs,
    })
    _dump(OUT_DIR / "fact_sheet.json", fact_sheet)
    _dump(OUT_DIR / "eval_cases.json", {
        "name": "Claim flag accuracy",
        "key": "eval-claims",
        "fact_sheet": "FS-2026-Q4",
        "cases": cases,
    })

    print("supervision data written to " + str(OUT_DIR))
    print("  briefs: " + str(len(briefs)))
    print("  facts: " + str(len(fact_sheet["facts"])))
    print("  disclosures: " + str(len(fact_sheet["disclosures"])))
    print("  eval cases: " + str(len(cases)))
    print("  audiences: " + ", ".join(
        "{id}={count}".format(id=item["id"], count=item["count"]) for item in audiences
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
