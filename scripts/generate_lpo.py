#!/usr/bin/env python3
"""Generate the deterministic LP Ops data files from the seeded book.

Writes only:
  data/modules/lpo/lp_records.json
  data/modules/lpo/packets.json
  data/modules/lpo/eval_cases.json

No clock reads, no randomness, no network. Two runs must produce identical bytes.
The subscription packets are synthetic text. They are deliberately messy: fields
split across sections, one unsigned packet, one missing KYC file, one wire bank
that does not match the custodian of record, one commitment that disagrees with
the administrator schedule, and one closing date before its subscription date.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BOOK_PATH = ROOT / "data" / "book.json"
OUT_DIR = ROOT / "data" / "modules" / "lpo"

AS_OF = "2026-11-19"
CURRENCY = "USD"

FIELD_DEFINITIONS = [
    {"key": "lp_name", "label": "LP name", "type": "text"},
    {"key": "entity_type", "label": "Entity type", "type": "text"},
    {"key": "commitment_amount", "label": "Commitment", "type": "money"},
    {"key": "qualified_status", "label": "QP or accredited status", "type": "enum"},
    {"key": "custodian", "label": "Custodian", "type": "text"},
    {"key": "bank_details_present", "label": "Bank details present", "type": "yesno"},
    {"key": "signature_present", "label": "Signature present", "type": "yesno"},
    {"key": "tax_id_present", "label": "Tax ID present", "type": "yesno"},
    {"key": "side_letter", "label": "Side letter", "type": "yesno"},
    {"key": "subscription_date", "label": "Subscription date", "type": "date"},
    {"key": "closing_date", "label": "Closing date", "type": "date"},
    {"key": "wire_instructions", "label": "Wire instructions", "type": "enum"},
]
FIELD_KEYS = [item["key"] for item in FIELD_DEFINITIONS]

STAGE_ORDER = ["document_intake", "exception_review", "ready_to_fund", "funded"]
STAGE_LABELS = {
    "document_intake": "Document intake",
    "exception_review": "Exception review",
    "ready_to_fund": "Ready to fund",
    "funded": "Funded",
}
KYC_LABELS = {
    "verified": "Verified",
    "pending": "Pending",
    "in_review": "In review",
}
DOCUMENT_LABELS = {
    "complete": "Complete",
    "exception": "Exception",
    "in_review": "In review",
}
KIND_LABELS = {
    "missing_signature": "Missing signature",
    "missing_kyc": "Missing KYC",
    "bank_name_mismatch": "Bank name mismatch",
    "commitment_mismatch": "Commitment mismatch",
    "date_anomaly": "Date anomaly",
}
SEVERITY_LABELS = {"high": "High", "medium": "Medium", "low": "Low"}
CALENDAR_STATUS_LABELS = {
    "settled": "Settled",
    "partially_funded": "Partially funded",
    "noticed": "Noticed",
    "declared": "Declared",
    "scheduled": "Scheduled",
    "projected": "Projected",
}
CALENDAR_KIND_LABELS = {
    "capital_call": "Capital call",
    "distribution": "Distribution",
}

PROGRAM = {
    "name": "Private Markets Access Program",
    "desk": "Private Markets Desk",
    "platform": "Allocate",
    "eligibility": "Qualified purchasers only",
    "mandate": "Venture and growth first",
    "owner": "Priya Raman",
    "owner_role": "Operations lead",
    "as_of": AS_OF,
    "currency": CURRENCY,
    "source": "Synthetic Allocate capital account export and administrator records",
}

FUND = {
    "name": "Kestrel Ridge Venture Access Fund IV, LP",
    "short_name": "Kestrel Ridge Venture IV",
    "strategy": "Venture",
    "vintage": 2026,
    "target_usd": 40000000,
    "first_close": "2026-09-30",
    "final_close": "2027-06-30",
    "administrator": "Ridgeline Fund Services",
    "general_partner": "Kestrel Ridge Partners GP, LLC",
    "retention_years": 7,
    "retention_until": "2034-06-30",
}

TEAM = [
    {"name": "Priya Raman", "role": "Operations lead"},
    {"name": "Marcus Hale", "role": "Compliance reviewer"},
    {"name": "Dana Whitfield", "role": "Adviser of record"},
]

LP_CONFIG = [
    {
        "id": "LP-01",
        "status_note": "Closed at the September first close. No open items. This packet is the clean control.",
        "household_id": "H-1050",
        "name": "Ferraro Family Office LLC",
        "short_name": "Ferraro Family Office",
        "entity_type": "Family office LLC",
        "commitment": 1_500_000,
        "funded": 450_000,
        "kyc_status": "verified",
        "kyc_detail": "Verified 2026-09-22",
        "document_status": "complete",
        "stage": "funded",
        "packet_id": "P-01",
        "prefer_trust_account": False,
        "next_event": {"date": "2027-01-15", "label": "Distribution D-01", "kind": "distribution"},
    },
    {
        "id": "LP-02",
        "status_note": "Signature page and KYC file are outstanding. The call 2 balance is held until both clear.",
        "household_id": "H-1043",
        "name": "Calloway Family Irrevocable Trust",
        "short_name": "Calloway Family Trust",
        "entity_type": "Irrevocable trust",
        "commitment": 1_000_000,
        "funded": 195_000,
        "kyc_status": "pending",
        "kyc_detail": "Trust certification and EIN pending",
        "document_status": "exception",
        "stage": "exception_review",
        "packet_id": "P-02",
        "prefer_trust_account": True,
        "next_event": {"date": "2026-11-28", "label": "Call 2 balance and KYC", "kind": "capital_call"},
    },
    {
        "id": "LP-03",
        "status_note": "Wire bank does not match the custodian of record. Confirm before the next release.",
        "household_id": "H-1042",
        "name": "Rowan Family Trust",
        "short_name": "Rowan Family Trust",
        "entity_type": "Revocable trust",
        "commitment": 1_250_000,
        "funded": 375_000,
        "kyc_status": "verified",
        "kyc_detail": "Verified 2026-10-03",
        "document_status": "exception",
        "stage": "exception_review",
        "packet_id": "P-03",
        "prefer_trust_account": True,
        "next_event": {"date": "2026-11-28", "label": "Wire confirmation", "kind": "capital_call"},
    },
    {
        "id": "LP-04",
        "status_note": "Commitment and date discrepancies need correction before funding.",
        "household_id": "H-1049",
        "name": "Lindqvist Capital LLC",
        "short_name": "Lindqvist Capital",
        "entity_type": "Limited liability company",
        "commitment": 750_000,
        "funded": 225_000,
        "kyc_status": "verified",
        "kyc_detail": "Verified 2026-10-16",
        "document_status": "exception",
        "stage": "exception_review",
        "packet_id": "P-04",
        "prefer_trust_account": False,
        "next_event": {"date": "2026-11-28", "label": "Commitment correction", "kind": "capital_call"},
    },
    {
        "id": "LP-05",
        "status_note": "Admitted at the September first close. Funding is held until the signed packet and wire details are complete.",
        "household_id": "H-1053",
        "name": "Achterberg Holdings LLC",
        "short_name": "Achterberg Holdings",
        "entity_type": "Limited liability company",
        "commitment": 500_000,
        "funded": 0,
        "kyc_status": "in_review",
        "kyc_detail": "In review since 2026-11-12",
        "document_status": "in_review",
        "stage": "document_intake",
        "packet_id": "P-05",
        "prefer_trust_account": False,
        "next_event": {"date": "2026-12-12", "label": "Countersigned packet", "kind": "closing"},
    },
    {
        "id": "LP-06",
        "status_note": "Closed and funded. No packet in the current queue.",
        "household_id": "H-1044",
        "name": "Barrett Legacy Trust",
        "short_name": "Barrett Legacy Trust",
        "entity_type": "Irrevocable trust",
        "commitment": 1_500_000,
        "funded": 450_000,
        "kyc_status": "verified",
        "kyc_detail": "Verified 2026-09-29",
        "document_status": "complete",
        "stage": "funded",
        "packet_id": None,
        "prefer_trust_account": True,
        "next_event": {"date": "2026-12-26", "label": "Capital call 3", "kind": "capital_call"},
    },
]

KNOWN_EXCEPTIONS = {
    "P-01": [],
    "P-02": [
        {
            "kind": "missing_signature",
            "severity": "high",
            "detail": "Trustee signature block is blank. The uploaded form has no executed signature page.",
        },
        {
            "kind": "missing_kyc",
            "severity": "high",
            "detail": "KYC review has not started. Trust certification and EIN are outstanding.",
        },
    ],
    "P-03": [
        {
            "kind": "bank_name_mismatch",
            "severity": "medium",
            "detail": "Wire bank Northline Bank, N.A. is not the custodian of record, Morrow Trust. Confirm before funding.",
        }
    ],
    "P-04": [
        {
            "kind": "commitment_mismatch",
            "severity": "high",
            "detail": "Packet states USD 975,000. The administrator schedule is USD 750,000.",
        },
        {
            "kind": "date_anomaly",
            "severity": "medium",
            "detail": "Closing date 2026-10-14 is before the subscription date 2026-10-28.",
        },
    ],
    "P-05": [
        {
            "kind": "missing_signature",
            "severity": "high",
            "detail": "Upload has no signature page. The subscription form is unsigned.",
        }
    ],
}

PACKET_META = {
    "P-01": {
        "short_title": "Ferraro Family Office",
        "title": "Ferraro Family Office, subscription packet",
        "received_at": "2026-09-18 09:42 ET",
        "received": "2026-09-18",
        "channel": "Allocate portal upload",
        "pages": 6,
        "status": "closed",
        "source_note": "Executed agreement, W-9, and signed side letter. Control case.",
    },
    "P-02": {
        "short_title": "Calloway Family Trust",
        "title": "Calloway Family Irrevocable Trust, subscription packet",
        "received_at": "2026-11-06 16:05 ET",
        "received": "2026-11-06",
        "channel": "Allocate portal upload",
        "pages": 4,
        "status": "in_queue",
        "source_note": "Scanned form and trust certificate. No signature page in the upload.",
    },
    "P-03": {
        "short_title": "Rowan Family Trust",
        "title": "Rowan Family Trust, subscription summary",
        "received_at": "2026-10-04 11:22 ET",
        "received": "2026-10-04",
        "channel": "Allocate portal export",
        "pages": 2,
        "status": "in_queue",
        "source_note": "Portal summary with investor-entered wire instructions.",
    },
    "P-04": {
        "short_title": "Lindqvist Capital",
        "title": "Lindqvist Capital LLC, subscription agreement extract",
        "received_at": "2026-10-30 14:10 ET",
        "received": "2026-10-30",
        "channel": "PDF text extract",
        "pages": 7,
        "status": "in_queue",
        "source_note": "Seven-page agreement, text extract only. No page images.",
    },
    "P-05": {
        "short_title": "Achterberg Holdings",
        "title": "Achterberg Holdings LLC, subscription packet",
        "received_at": "2026-11-10 08:17 ET",
        "received": "2026-11-10",
        "channel": "Email attachment",
        "pages": 3,
        "status": "in_queue",
        "source_note": "Cover email, form, and certificate of formation. Wire details deferred.",
    },
}

EXPECTED_OVERRIDES = {
    "P-01": {},
    "P-02": {"signature_present": "No", "tax_id_present": "No"},
    "P-03": {"wire_instructions": "International wire"},
    "P-04": {"commitment_amount": 975_000},
    "P-05": {
        "qualified_status": "Not stated",
        "bank_details_present": "No",
        "signature_present": "No",
        "wire_instructions": "Not stated",
    },
}


def load_book() -> dict[str, Any]:
    return json.loads(BOOK_PATH.read_text(encoding="utf-8"))


def household_name(book: dict[str, Any], household_id: str) -> str:
    for item in book["households"]:
        if item["id"] == household_id:
            return item["display_name"]
    raise KeyError(household_id)


def household_record(book: dict[str, Any], household_id: str) -> dict[str, Any]:
    for item in book["households"]:
        if item["id"] == household_id:
            return item
    raise KeyError(household_id)


def custodian_for(book: dict[str, Any], household_id: str, prefer_trust: bool) -> str:
    accounts = [item for item in book["accounts"] if item["household_id"] == household_id]
    if not accounts:
        raise KeyError(household_id)
    if prefer_trust:
        trusts = [item for item in accounts if "trust" in str(item["account_type"]).lower()]
        if trusts:
            return trusts[0]["custodian"]
    return accounts[0]["custodian"]


def stable_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json(payload), encoding="utf-8")


def packet_text(packet_id: str, lp: dict[str, Any], fund_name: str) -> str:
    """Five different layouts, with the seeded issues written into the text."""

    if packet_id == "P-01":
        return "\n".join(
            [
                "From: Ferraro Family Office LLC <ops@ferrarofamily.example> (synthetic)",
                "To: Private Markets Desk",
                "Subject: Subscription packet, Kestrel Ridge Venture Access Fund IV",
                "Received: 2026-09-18 09:42 ET",
                "Attachments: Subscription Agreement (executed); W-9; Side Letter",
                "Channel: Allocate investor portal upload",
                "",
                "Ops note: Qualified purchaser evidence is attached. The bank letter is on file",
                "with the administrator. No open items from intake.",
                "",
                "INVESTOR",
                "Legal name of investor: Ferraro Family Office LLC",
                "Entity type: Family office LLC",
                "State of organization: Connecticut",
                "",
                "SUBSCRIPTION",
                f"Fund: {fund_name}",
                "Commitment: USD 1,500,000 (one million five hundred thousand dollars)",
                "Subscription date: 2026-09-18",
                "Closing date: 2026-09-30",
                "",
                "ELIGIBILITY",
                "Qualified purchaser: Yes. The entity owns more than USD 5,000,000 in investments.",
                "Accredited investor: not relied on.",
                "Tax ID (TIN): 88-4419072. W-9 attached.",
                "",
                "CUSTODY AND WIRE",
                "Custodian of record: Morrow Trust",
                "Bank name: Morrow Trust",
                "ABA: 021000089",
                "Account: 4471-8820-31",
                "Wire instructions: domestic wire",
                "Reference: KRV-IV / FERRARO",
                "",
                "SIDE LETTER",
                "Side letter: Yes. Fee discount schedule attached and signed by the general partner.",
                "",
                "SIGNATURE",
                "Signed by: A. Ferraro, Managing Member",
                "Signature: /s/ A. Ferraro",
                "Date: 2026-09-18",
                "",
                "COMPLIANCE NOTE",
                "KYC: verified 2026-09-22.",
            ]
        )

    if packet_id == "P-02":
        return "\n".join(
            [
                "ALLOCATE INVESTOR PORTAL - SUBSCRIPTION PACKET (synthetic)",
                "Uploaded: 2026-11-06 16:05 ET",
                "Investor: Calloway Family Irrevocable Trust",
                "Packet: scanned form, 4 pages; no signature page uploaded",
                "",
                "INVESTOR INFORMATION",
                "Legal name of investor: Calloway Family Irrevocable Trust",
                "Form of entity: Irrevocable trust",
                "Jurisdiction: Connecticut",
                "Trust date: 2022-03-18",
                "Trustee: Devon Calloway",
                "",
                "SUBSCRIPTION",
                f"Fund: {fund_name}",
                "Aggregate commitment: $1,000,000.00",
                "Subscription date: 11/06/2026",
                "Anticipated closing: December 12, 2026",
                "",
                "ELIGIBILITY",
                "Qualified purchaser: The trustee confirms the trust is a qualified purchaser.",
                "Accredited investor: not claimed.",
                "TIN box: blank.",
                "Trust EIN: pending. The application was filed with the IRS. No EIN on this packet.",
                "",
                "CUSTODY AND WIRE",
                "Custodian: Alder Clearing",
                "Bank name: Alder Clearing",
                "ABA: 011401533",
                "Account: 7715-0042",
                "Wire instructions: domestic wire",
                "Reference: KRV-IV / CALLOWAY TRUST",
                "",
                "SIDE LETTER",
                "Side letter: None requested.",
                "",
                "SIGNATURE BLOCK",
                "Trustee: Devon Calloway",
                "Signature: ______________________",
                "Date: ____________",
                "",
                "INTERNAL INTAKE NOTE",
                "KYC review: not started. Trust certification and EIN are outstanding.",
                "Packet received without a signature page.",
            ]
        )

    if packet_id == "P-03":
        return "\n".join(
            [
                "ALLOCATE INVESTOR PORTAL - SUBSCRIPTION SUMMARY (synthetic export)",
                "Export time: 2026-10-04 11:22 ET",
                "Investor: Rowan Family Trust",
                "Household: H-1042",
                f"Fund: {fund_name}",
                "",
                "WIRE INSTRUCTIONS (entered by investor)",
                "Bank name: Northline Bank, N.A.",
                "ABA: 026009593",
                "Account: 3390-1187",
                "Beneficiary: Rowan Family Trust",
                "Wire instructions: international wire",
                "Reference: KRV-IV-ROWAN",
                "",
                "SUBSCRIPTION TERMS",
                "Entity type: Revocable trust",
                "Commitment: USD 1,250,000.00",
                "Subscription date: 2026-10-02",
                "Closing date: 2026-10-30",
                "",
                "CUSTODY",
                "Custodian of record: Morrow Trust",
                "",
                "TAX AND ELIGIBILITY",
                "Tax ID: 06-3381244",
                "Qualified purchaser: Yes, trust assets exceed USD 5,000,000.",
                "Accredited investor: not claimed.",
                "Side letter: No.",
                "",
                "SIGNATURE",
                "Signature: executed in portal, signature ID SIG-2026-10-02-4471.",
                "Signer: Jordan Rowan, trustee.",
                "",
                "COMPLIANCE NOTE",
                "KYC: verified 2026-10-03.",
            ]
        )

    if packet_id == "P-04":
        return "\n".join(
            [
                "[Page 1 of 7]",
                "SUBSCRIPTION AGREEMENT - TEXT EXTRACT (synthetic)",
                fund_name,
                "",
                "SECTION 1. INVESTOR",
                "Name: Lindqvist Capital LLC",
                "Entity type: Limited liability company",
                "State of formation: Delaware",
                "Principal place of business: 12 Halden Way, Greenwich, CT 06830",
                "",
                "SECTION 2. SUBSCRIPTION",
                "The Investor hereby subscribes for a capital commitment of",
                "USD 975,000 (nine hundred seventy-five thousand dollars).",
                "Subscription date: 28 October 2026",
                "Closing date: 14 October 2026",
                "Fund target: USD 40,000,000",
                "Minimum commitment: USD 500,000",
                "",
                "[Page 3 of 7]",
                "SECTION 4. ELIGIBILITY",
                "The Investor represents that it is a qualified purchaser within the meaning",
                "of Section 2(a)(51) of the Investment Company Act.",
                "Accredited investor representation: not applicable.",
                "",
                "[Page 5 of 7]",
                "SECTION 7. CUSTODY AND PAYMENTS",
                "Custodian: Alder Clearing",
                "Bank name: Alder Clearing",
                "ABA: 011401533",
                "Account: 6128-9930",
                "Wire instructions: domestic wire",
                "",
                "[Page 6 of 7]",
                "SECTION 9. TAX AND SIDE LETTER",
                "Tax ID (TIN): 81-2297044. W-9 attached.",
                "Side letter: Yes. Reporting side letter at Exhibit C.",
                "KYC: verified 2026-10-16.",
                "",
                "[Page 7 of 7]",
                "SIGNATURE",
                "/s/ E. Lindqvist, Managing Member",
                "Date: 2026-10-27",
            ]
        )

    if packet_id == "P-05":
        return "\n".join(
            [
                "From: Achterberg Holdings LLC <ops@achterbergholdings.example> (synthetic)",
                "To: Private Markets Desk",
                "Subject: Subscription packet, Kestrel Ridge Venture Access Fund IV",
                "Received: 2026-11-10 08:17 ET",
                "Attachments: Subscription form (3 pages); certificate of formation",
                "",
                "Hello,",
                "Attaching our subscription for the venture access fund. Our counsel is reviewing",
                "the side letter question, so no side letter is included. We will send wire",
                "details once the bank account is opened.",
                "",
                "INVESTOR",
                "Investor: Achterberg Holdings LLC",
                "Entity type: Limited liability company",
                "State of formation: Connecticut",
                "",
                "SUBSCRIPTION",
                f"Fund: {fund_name}",
                "Commitment: USD 500,000",
                "Subscription date: November 10, 2026",
                "Closing date: December 12, 2026",
                "",
                "ELIGIBILITY",
                "Qualified purchaser: [section left blank in the uploaded form]",
                "Accredited investor: [not completed]",
                "Tax ID: 92-1187744",
                "",
                "CUSTODY",
                "Custodian: Alder Clearing",
                "Bank details: not provided. Wire instructions to follow.",
                "",
                "SIDE LETTER",
                "Side letter: No.",
                "",
                "SIGNATURE",
                "Signature: [no signature page in the upload]",
                "Date: [blank]",
                "",
                "COMPLIANCE NOTE",
                "KYC: in review as of 2026-11-12.",
            ]
        )

    raise KeyError(packet_id)


def expected_fields(packet_id: str, lp: dict[str, Any], custodian: str) -> dict[str, Any]:
    base = {
        "lp_name": lp["name"],
        "entity_type": lp["entity_type"],
        "commitment_amount": lp["commitment"],
        "qualified_status": "Qualified purchaser",
        "custodian": custodian,
        "bank_details_present": "Yes",
        "signature_present": "Yes",
        "tax_id_present": "Yes",
        "side_letter": "No",
        "subscription_date": "",
        "closing_date": "",
        "wire_instructions": "Domestic wire",
    }
    dates = {
        "P-01": ("2026-09-18", "2026-09-30"),
        "P-02": ("2026-11-06", "2026-12-12"),
        "P-03": ("2026-10-02", "2026-10-30"),
        "P-04": ("2026-10-28", "2026-10-14"),
        "P-05": ("2026-11-10", "2026-12-12"),
    }
    base["subscription_date"], base["closing_date"] = dates[packet_id]
    if packet_id == "P-01":
        base["side_letter"] = "Yes"
    if packet_id == "P-04":
        base["side_letter"] = "Yes"
    base.update(EXPECTED_OVERRIDES[packet_id])
    return {key: base[key] for key in FIELD_KEYS}


def main() -> None:
    book = load_book()
    custodian_by_lp = {
        item["id"]: custodian_for(book, item["household_id"], item["prefer_trust_account"])
        for item in LP_CONFIG
    }

    lps: list[dict[str, Any]] = []
    for item in LP_CONFIG:
        called = int(round(item["commitment"] * 0.30))
        funded = int(item["funded"])
        outstanding = called - funded
        household = household_record(book, item["household_id"])
        known = KNOWN_EXCEPTIONS.get(item["packet_id"] or "", [])
        lps.append(
            {
                "id": item["id"],
                "household_id": item["household_id"],
                "household_name": household["display_name"],
                "consent_status": household["consent_status"],
                "name": item["name"],
                "short_name": item["short_name"],
                "entity_type": item["entity_type"],
                "commitment": item["commitment"],
                "called": called,
                "funded": funded,
                "outstanding": outstanding,
                "funded_pct": round(funded / item["commitment"] * 100, 1),
                "paid_of_called_pct": round(funded / called * 100, 1) if called else 0.0,
                "kyc_status": item["kyc_status"],
                "kyc_label": KYC_LABELS[item["kyc_status"]],
                "kyc_detail": item["kyc_detail"],
                "document_status": item["document_status"],
                "document_label": DOCUMENT_LABELS[item["document_status"]],
                "stage": item["stage"],
                "stage_label": STAGE_LABELS[item["stage"]],
                "packet_id": item["packet_id"],
                "custodian": custodian_by_lp[item["id"]],
                "exception_count": len(known),
                "next_event": item["next_event"],
                "status_note": item.get("status_note", ""),
            }
        )

    packets: list[dict[str, Any]] = []
    eval_cases: list[dict[str, Any]] = []
    for item in LP_CONFIG:
        packet_id = item["packet_id"]
        if not packet_id:
            continue
        lp = next(lp for lp in lps if lp["id"] == item["id"])
        text = packet_text(packet_id, lp, FUND["name"]).strip() + "\n"
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        known = KNOWN_EXCEPTIONS[packet_id]
        meta = PACKET_META[packet_id]
        expected = expected_fields(packet_id, lp, custodian_by_lp[item["id"]])
        household = household_record(book, item["household_id"])
        packet = {
            "id": packet_id,
            "lp_id": item["id"],
            "household_id": item["household_id"],
            "household_name": household["display_name"],
            "consent_status": household["consent_status"],
            "fund": FUND["name"],
            "short_title": meta["short_title"],
            "title": meta["title"],
            "received": meta["received"],
            "received_at": meta["received_at"],
            "channel": meta["channel"],
            "pages": meta["pages"],
            "status": meta["status"],
            "source_note": meta["source_note"],
            "sha256": sha,
            "sha_short": sha[:12],
            "text": text,
            "administrator_record": {
                "lp_name": lp["name"],
                "commitment": lp["commitment"],
                "kyc_status": lp["kyc_status"],
                "custodian": lp["custodian"],
                "fund": FUND["name"],
                "final_close": FUND["final_close"],
            },
            "known_exceptions": known,
            "known_exception_count": len(known),
            "expected_fields": expected,
            "expected_exceptions": sorted({entry["kind"] for entry in known}),
        }
        packets.append(packet)
        eval_cases.append(
            {
                "case": packet_id,
                "packet_id": packet_id,
                "lp_id": item["id"],
                "household_id": item["household_id"],
                "expected": expected,
                "expected_exceptions": sorted({entry["kind"] for entry in known}),
                "expected_exception_details": [
                    {"kind": entry["kind"], "severity": entry["severity"]}
                    for entry in sorted(known, key=lambda item: item["kind"])
                ],
                "notes": meta["source_note"],
            }
        )

    total_commitment = sum(lp["commitment"] for lp in lps)
    total_called = sum(lp["called"] for lp in lps)
    total_funded = sum(lp["funded"] for lp in lps)
    total_outstanding = total_called - total_funded
    total_unfunded = total_commitment - total_called
    open_exceptions = sum(lp["exception_count"] for lp in lps)
    packets_with_exceptions = sum(1 for packet in packets if packet["known_exception_count"])

    calendar = [
        {
            "id": "C-01",
            "kind": "capital_call",
            "number": 1,
            "notice_date": "2026-09-30",
            "date": "2026-10-14",
            "date_label": "Due",
            "amount": 1_300_000,
            "pct": 0.20,
            "status": "settled",
            "status_label": CALENDAR_STATUS_LABELS["settled"],
            "outstanding": 0,
            "meta": "Settled 2026-10-14.",
        },
        {
            "id": "C-02",
            "kind": "capital_call",
            "number": 2,
            "notice_date": "2026-11-14",
            "date": "2026-11-28",
            "date_label": "Due",
            "amount": 650_000,
            "pct": 0.10,
            "status": "partially_funded",
            "status_label": CALENDAR_STATUS_LABELS["partially_funded"],
            "outstanding": 255_000,
            "meta": "USD 255,000 outstanding from LP-02 and LP-05.",
        },
        {
            "id": "C-03",
            "kind": "capital_call",
            "number": 3,
            "notice_date": "2026-12-12",
            "date": "2026-12-26",
            "date_label": "Due",
            "amount": 650_000,
            "pct": 0.10,
            "status": "noticed",
            "status_label": CALENDAR_STATUS_LABELS["noticed"],
            "outstanding": 650_000,
            "meta": "Notice issued 2026-12-12.",
        },
        {
            "id": "D-01",
            "kind": "distribution",
            "number": 1,
            "notice_date": None,
            "date": "2027-01-15",
            "date_label": "Pay",
            "amount": 220_000,
            "pct": None,
            "status": "declared",
            "status_label": CALENDAR_STATUS_LABELS["declared"],
            "outstanding": 0,
            "meta": "Declared from the Kestrel Ridge Growth II secondary.",
        },
        {
            "id": "C-04",
            "kind": "capital_call",
            "number": 4,
            "notice_date": "2027-02-20",
            "date": "2027-03-06",
            "date_label": "Due",
            "amount": 975_000,
            "pct": 0.15,
            "status": "scheduled",
            "status_label": CALENDAR_STATUS_LABELS["scheduled"],
            "outstanding": 975_000,
            "meta": "Scheduled for the growth sleeve.",
        },
        {
            "id": "D-02",
            "kind": "distribution",
            "number": 2,
            "notice_date": None,
            "date": "2027-03-31",
            "date_label": "Pay",
            "amount": 180_000,
            "pct": None,
            "status": "projected",
            "status_label": CALENDAR_STATUS_LABELS["projected"],
            "outstanding": 0,
            "meta": "Projected. Not declared.",
        },
    ]
    calendar.sort(key=lambda event: (event["date"], event["id"]))
    for event in calendar:
        event["kind_label"] = CALENDAR_KIND_LABELS[event["kind"]]
        event["pct_label"] = f"{int(round(event['pct'] * 100))}% of commitments" if event["pct"] else "No capital called"
        event["is_past"] = event["date"] < AS_OF
    next_event = next(
        (event for event in calendar if event["kind"] == "capital_call" and event["status"] != "settled"),
        calendar[0],
    )
    for event in calendar:
        event["is_next"] = event["id"] == next_event["id"]

    task_lines = [
        {
            "key": "subscription",
            "name": "Subscription pack review",
            "formula": "1 packet x 4.0 hours",
            "hours": 4.0,
            "basis": "Review, signature chase, and file assembly",
        },
        {
            "key": "kyc",
            "name": "KYC and tax refresh",
            "formula": "1 refresh x 2.0 hours",
            "hours": 2.0,
            "basis": "Annual refresh and W-9 or EIN follow-up",
        },
        {
            "key": "calls",
            "name": "Capital call processing",
            "formula": "4 calls x 0.75 hours",
            "hours": 3.0,
            "basis": "Notice, reconciliation, and receipt",
        },
        {
            "key": "distributions",
            "name": "Distribution processing",
            "formula": "2 events x 0.5 hours",
            "hours": 1.0,
            "basis": "Allocation, statement, and cash reconciliation",
        },
        {
            "key": "statements",
            "name": "Quarterly statement reconciliation",
            "formula": "4 quarters x 1.25 hours",
            "hours": 5.0,
            "basis": "NAV tie-out and LP statement",
        },
    ]
    baseline_hours = round(sum(line["hours"] for line in task_lines), 2)
    reduction_rate = 0.35
    per_lp_hours = round(baseline_hours * reduction_rate, 2)
    current_lps = len(lps)
    target_lps = 60
    productive_hours = 1880
    metric = {
        "label": "Hours returned per LP per year",
        "per_lp_hours": per_lp_hours,
        "baseline_hours": baseline_hours,
        "reduction_rate": reduction_rate,
        "current_lps": current_lps,
        "current_hours": round(per_lp_hours * current_lps, 2),
        "target_lps": target_lps,
        "target_hours": round(per_lp_hours * target_lps, 2),
        "productive_hours": productive_hours,
        "target_fte": round(per_lp_hours * target_lps / productive_hours, 2),
        "task_lines": task_lines,
        "math": (
            f"{baseline_hours:.2f} baseline hours x {int(reduction_rate * 100)}% = "
            f"{per_lp_hours:.2f} hours per LP per year. "
            f"{current_lps} LPs = {per_lp_hours * current_lps:.1f} hours. "
            f"{target_lps} LPs = {per_lp_hours * target_lps:.0f} hours, or "
            f"{per_lp_hours * target_lps / productive_hours:.2f} FTE at {productive_hours:,} productive hours."
        ),
        "caveat": "Modelled from the desk task list. Not a time study. The 60-LP target is a program assumption.",
    }

    summary = {
        "lps": len(lps),
        "packets": len(packets),
        "commitments": total_commitment,
        "called": total_called,
        "funded": total_funded,
        "outstanding": total_outstanding,
        "unfunded": total_unfunded,
        "called_pct": round(total_called / total_commitment * 100, 1),
        "funded_pct_of_called": round(total_funded / total_called * 100, 1),
        "funded_pct_of_commitments": round(total_funded / total_commitment * 100, 1),
        "open_exceptions": open_exceptions,
        "packets_with_exceptions": packets_with_exceptions,
        "fund_target": FUND["target_usd"],
        "fund_target_pct": round(total_commitment / FUND["target_usd"] * 100, 1),
        "next_event_id": next_event["id"],
        "next_event_label": next_event["kind_label"] + " " + str(next_event.get("number") or ""),
        "next_event_date": next_event["date"],
        "next_event_amount": next_event["amount"],
        "next_event_outstanding": next_event["outstanding"],
        "current_hours": metric["current_hours"],
        "target_hours": metric["target_hours"],
    }

    stage_counts = {"all": len(lps)}
    for stage in STAGE_ORDER:
        stage_counts[stage] = sum(1 for lp in lps if lp["stage"] == stage)

    lp_records = {
        "program": PROGRAM,
        "fund": FUND,
        "summary": summary,
        "stage_counts": stage_counts,
        "stage_order": STAGE_ORDER,
        "stage_labels": STAGE_LABELS,
        "kyc_labels": KYC_LABELS,
        "document_labels": DOCUMENT_LABELS,
        "exception_labels": KIND_LABELS,
        "severity_labels": SEVERITY_LABELS,
        "calendar_status_labels": CALENDAR_STATUS_LABELS,
        "calendar_kind_labels": CALENDAR_KIND_LABELS,
        "field_definitions": FIELD_DEFINITIONS,
        "field_keys": FIELD_KEYS,
        "team": TEAM,
        "lps": lps,
        "calendar": calendar,
        "metric": metric,
        "assumptions": [
            {"label": "Program", "value": PROGRAM["name"], "source": "Synthetic program record"},
            {"label": "Platform", "value": PROGRAM["platform"], "source": "Synthetic program record"},
            {"label": "Eligibility", "value": PROGRAM["eligibility"], "source": "Synthetic program record"},
            {"label": "Fund target", "value": f"USD {FUND['target_usd']:,}", "source": "Synthetic fund term sheet"},
            {"label": "Calls per year", "value": "4", "source": "Modelled from the 2026 call schedule"},
            {"label": "Distributions per year", "value": "2", "source": "Modelled from the 2027 distribution calendar"},
            {"label": "Reduction rate", "value": "35%", "source": "Modelled assumption"},
            {"label": "Target LPs", "value": "60", "source": "Program assumption"},
            {"label": "Productive hours", "value": "1,880 per year", "source": "Modelled FTE basis"},
        ],
        "sources": {
            "pipeline": "Synthetic Allocate capital account export, as of 2026-11-19",
            "calendar": "Synthetic fund administrator call and distribution notices",
            "packets": "Synthetic subscription packets, received 2026-09-18 to 2026-11-10",
            "exceptions": "Synthetic administrator issue log",
            "eval": "data/modules/lpo/eval.json",
            "metric": "Modelled from the desk task list",
        },
        "build_note": {
            "pattern": (
                "One extraction call per packet with strict JSON and a closed exception vocabulary. "
                "The administrator record sits in the prompt so the model can compare commitment, KYC, "
                "and custodian. A second call turns the extraction into an internal checklist. A third "
                "drafts the LP status note for two packets. All three are parsed defensively."
            ),
            "eval": (
                "65 checks: 60 field cells across five packets, plus 5 exception sets. An exception "
                "set passes only when the kind and every severity match. Exact match after normalising "
                "money, dates, and yes/no values. In-sample, so it describes these five documents, not "
                "the next one."
            ),
            "limits": [
                "Text intake only. No PDF parsing, OCR, or document storage.",
                "No Allocate API, custodian API, e-signature, wire, or filing integration.",
                "No model output is sent to an LP. The status note waits for a named approval.",
                "The exception log is a synthetic administrator record, not a real NIGO feed.",
            ],
        },
    }

    packets_payload = {
        "fund": FUND,
        "as_of": AS_OF,
        "packets": packets,
    }

    eval_payload = {
        "name": "Subscription packet extraction",
        "version": 1,
        "module": "lpo",
        "as_of": AS_OF,
        "field_keys": FIELD_KEYS,
        "field_definitions": FIELD_DEFINITIONS,
        "exception_kinds": list(KIND_LABELS),
        "cases": eval_cases,
    }

    write_json(OUT_DIR / "lp_records.json", lp_records)
    write_json(OUT_DIR / "packets.json", packets_payload)
    write_json(OUT_DIR / "eval_cases.json", eval_payload)

    print(f"LPs: {len(lps)}")
    print(f"Packets: {len(packets)}")
    print(f"Eval cases: {len(eval_cases)} x {len(FIELD_KEYS)} fields")
    print(f"Commitments: USD {total_commitment:,}")
    print(f"Called: USD {total_called:,}")
    print(f"Funded: USD {total_funded:,}")
    print(f"Outstanding: USD {total_outstanding:,}")
    print(f"Open seeded exceptions: {open_exceptions}")
    print(f"Calendar events: {len(calendar)}")
    print(f"Baseline hours per LP: {baseline_hours}")
    print(f"Modelled hours returned per LP: {per_lp_hours}")
    print(f"Wrote: {OUT_DIR}")


if __name__ == "__main__":
    main()
