#!/usr/bin/env python3
"""Generate the synthetic Transition Copilot dataset.

All names, custodians, addresses, and account numbers in the output are invented.
The script is deterministic and uses only the Python standard library.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCUMENTS_DIR = DATA_DIR / "documents"
EVAL_DIR = DATA_DIR / "eval"

NL = chr(10)
CENT = Decimal("0.01")

SPECIMEN_STATEMENT = "SPECIMEN - SYNTHETIC DATA - NOT A REAL STATEMENT"
SPECIMEN_CRM = "SPECIMEN - SYNTHETIC DATA - NOT A REAL CRM EXPORT"
SPECIMEN_TRUST = "SPECIMEN - SYNTHETIC DATA - NOT A REAL TRUST DOCUMENT"
SPECIMEN_ANNUITY = "SPECIMEN - SYNTHETIC DATA - NOT A REAL ANNUITY CONTRACT"

TRANSFER_PATHS = {
    "ACAT",
    "non-ACAT",
    "new account",
    "assignment",
    "rollover",
    "in-kind review",
    "no transfer",
}
ACCOUNT_STATUSES = {
    "not started",
    "in flight",
    "waiting on client",
    "waiting on custodian",
    "complete",
    "blocked",
}
TASK_PHASES = {
    "intake",
    "account opening",
    "document collection",
    "transfer initiation",
    "custodian review",
    "confirmation",
    "handoff",
}
REGISTRATIONS = {
    "JTWROS",
    "TOD",
    "Revocable trust",
    "Irrevocable trust",
    "IRA",
    "Roth IRA",
    "Inherited IRA",
    "UTMA",
    "Individual",
}
TASK_STATUSES = {"not started", "in progress", "blocked", "complete"}

HARD_CASES = {
    "A-2203": "Irrevocable trust",
    "A-2204": "Inherited IRA",
    "A-2205": "Proprietary mutual fund share class",
    "A-2206": "Variable annuity with a 1035 question",
    "A-2209": "Held-away 401k",
    "A-2210": "UTMA",
    "A-2212": "Margin account with options",
    "A-2213": "LP alternative interest",
}

# Broker Protocol permission object. The five data elements are the only client
# data that may move with the resignation letter, and each restriction is a
# condition on using them. Fictional firm, invented dates, synthetic book.
PROTOCOL = {
    "received_at": "2026-11-03",
    "permitted_fields": [
        "Client name",
        "Address",
        "Phone",
        "Email",
        "Account title",
    ],
    "restrictions": [
        "No client contact before resignation",
        "Protocol data may be used only for transition preparation until client consent",
        "No solicitation",
    ],
    "source_note": "Broker Protocol: five permitted data elements, submitted with the resignation letter",
}


def d(value) -> Decimal:
    return Decimal(str(value))


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money(value: Decimal) -> str:
    return f"{value:,.2f}"


def number(value: Decimal):
    if value == value.to_integral_value():
        return int(value)
    return float(value)


BOOK = {
    "id": "book-whitfield",
    "advisor_name": "Dana Whitfield",
    "firm_from": "Meridian Private Wealth",
    "transition_lead": "Priya Raman",
    "signed_at": "2026-11-03",
    "target_live_at": "2026-12-18",
    "aum_total": 42300000,
    "household_count": 12,
    "custodians": ["Alder Clearing", "Morrow Trust"],
    "protocol": PROTOCOL,
}

HOUSEHOLDS = [
    ("H-1042", "Rowan Household", 6180000, "Client since 2016", "Two generations of accounts"),
    ("H-1043", "Calloway Household", 4250000, "Client since 2009", "Brother is trustee; EIN pending"),
    ("H-1044", "Barrett Household", 1875000, "Client since 2021", "Inherited from a parent in 2021"),
    ("H-1045", "Voss Household", 3940000, "Client since 2014", "TOD brokerage plus a non-qualified annuity"),
    ("H-1046", "Sato Household", 1260000, "Client since 2019", "Single Roth IRA"),
    ("H-1047", "Delgado Household", 2890000, "Client since 2012", "Former employer plan rollover in progress"),
    ("H-1048", "Okafor Household", 1430000, "Client since 2018", "Parent is custodian for one minor"),
    ("H-1049", "Lindqvist Household", 5120000, "Client since 2010", "Margin account with listed options"),
    ("H-1050", "Ferraro Household", 7460000, "Client since 2007", "LP interest plus a brokerage account"),
    ("H-1051", "Nakamura Household", 1980000, "Client since 2020", "TOD registration; beneficiary on file"),
    ("H-1052", "Petrov Household", 3310000, "Client since 2015", "Retirement assets only"),
    ("H-1053", "Achterberg Household", 2605000, "Client since 2024", "New account opening in wave 2"),
]

# Consent status by household. Pending means a consent is still outstanding, so a
# client-facing draft for that household stays blocked. "not required" is the
# household whose wave 2 relationship opens a new account with new money.
CONSENT_STATUS = {
    "H-1042": "received",
    "H-1043": "pending",  # irrevocable trust; trustee certification and EIN outstanding
    "H-1044": "pending",  # inherited IRA; beneficiary elections outstanding
    "H-1045": "pending",  # variable annuity; 1035 election outstanding
    "H-1046": "received",
    "H-1047": "received",
    "H-1048": "received",
    "H-1049": "received",
    "H-1050": "received",
    "H-1051": "received",
    "H-1052": "received",
    "H-1053": "not required",  # new account funded with new money in wave 2
}

ACCOUNTS = [
    {
        "id": "A-2201",
        "household_id": "H-1042",
        "custodian": "Alder Clearing",
        "account_type": "Joint brokerage",
        "registration": "JTWROS",
        "masked_number": "xxxx-4471",
        "transfer_path": "ACAT",
        "status": "in flight",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 1,
    },
    {
        "id": "A-2202",
        "household_id": "H-1042",
        "custodian": "Morrow Trust",
        "account_type": "Revocable living trust",
        "registration": "Revocable trust",
        "masked_number": "xxxx-3390",
        "transfer_path": "ACAT",
        "status": "waiting on client",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 1,
    },
    {
        "id": "A-2203",
        "household_id": "H-1043",
        "custodian": "Alder Clearing",
        "account_type": "Irrevocable trust",
        "registration": "Irrevocable trust",
        "masked_number": "xxxx-7715",
        "transfer_path": "non-ACAT",
        "status": "waiting on client",
        "hard_case": "Irrevocable trust",
        "hard_case_note": "Trust dated 2022-03-18; trustee certification and EIN pending",
        "wave": 1,
    },
    {
        "id": "A-2204",
        "household_id": "H-1044",
        "custodian": "Morrow Trust",
        "account_type": "Inherited IRA",
        "registration": "Inherited IRA",
        "masked_number": "xxxx-2084",
        "transfer_path": "ACAT",
        "status": "in flight",
        "hard_case": "Inherited IRA",
        "hard_case_note": "Beneficiary IRA opened in 2022 after the account owner died in 2021",
        "wave": 1,
    },
    {
        "id": "A-2205",
        "household_id": "H-1045",
        "custodian": "Alder Clearing",
        "account_type": "Individual brokerage (TOD)",
        "registration": "TOD",
        "masked_number": "xxxx-6128",
        "transfer_path": "ACAT",
        "status": "in flight",
        "hard_case": "Proprietary mutual fund share class",
        "hard_case_note": "Meridian Select Class C shares are proprietary and cannot transfer in kind",
        "wave": 1,
    },
    {
        "id": "A-2206",
        "household_id": "H-1045",
        "custodian": "Morrow Trust",
        "account_type": "Variable annuity",
        "registration": "Individual",
        "masked_number": "xxxx-8820",
        "transfer_path": "in-kind review",
        "status": "waiting on client",
        "hard_case": "Variable annuity with a 1035 question",
        "hard_case_note": "Non-qualified contract issued by Halden Life; 1035 exchange under review",
        "wave": 1,
    },
    {
        "id": "A-2207",
        "household_id": "H-1046",
        "custodian": "Alder Clearing",
        "account_type": "Roth IRA",
        "registration": "Roth IRA",
        "masked_number": "xxxx-5540",
        "transfer_path": "ACAT",
        "status": "not started",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 2,
    },
    {
        "id": "A-2208",
        "household_id": "H-1047",
        "custodian": "Morrow Trust",
        "account_type": "Traditional IRA",
        "registration": "IRA",
        "masked_number": "xxxx-9187",
        "transfer_path": "ACAT",
        "status": "in flight",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 1,
    },
    {
        "id": "A-2209",
        "household_id": "H-1047",
        "custodian": "Morrow Trust",
        "account_type": "Rollover IRA (held-away 401k)",
        "registration": "IRA",
        "masked_number": "xxxx-9246",
        "transfer_path": "rollover",
        "status": "waiting on client",
        "hard_case": "Held-away 401k",
        "hard_case_note": "Plan recordkept by Northline Retirement Services; rollover approval pending",
        "wave": 1,
    },
    {
        "id": "A-2210",
        "household_id": "H-1048",
        "custodian": "Alder Clearing",
        "account_type": "UTMA custodial",
        "registration": "UTMA",
        "masked_number": "xxxx-3372",
        "transfer_path": "ACAT",
        "status": "waiting on custodian",
        "hard_case": "UTMA",
        "hard_case_note": "Parent is custodian for the minor; UTMA certification required",
        "wave": 1,
    },
    {
        "id": "A-2211",
        "household_id": "H-1048",
        "custodian": "Alder Clearing",
        "account_type": "Individual brokerage",
        "registration": "Individual",
        "masked_number": "xxxx-3418",
        "transfer_path": "ACAT",
        "status": "not started",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 2,
    },
    {
        "id": "A-2212",
        "household_id": "H-1049",
        "custodian": "Alder Clearing",
        "account_type": "Joint brokerage (margin)",
        "registration": "JTWROS",
        "masked_number": "xxxx-7780",
        "transfer_path": "in-kind review",
        "status": "blocked",
        "hard_case": "Margin account with options",
        "hard_case_note": "Margin agreement and listed options; in-kind review required",
        "wave": 1,
    },
    {
        "id": "A-2213",
        "household_id": "H-1050",
        "custodian": "Morrow Trust",
        "account_type": "Limited partnership interest",
        "registration": "Individual",
        "masked_number": "xxxx-4450",
        "transfer_path": "assignment",
        "status": "blocked",
        "hard_case": "LP alternative interest",
        "hard_case_note": "Kestrel Ridge Fund II units require general partner consent to assign",
        "wave": 1,
    },
    {
        "id": "A-2214",
        "household_id": "H-1050",
        "custodian": "Morrow Trust",
        "account_type": "Individual brokerage",
        "registration": "Individual",
        "masked_number": "xxxx-4519",
        "transfer_path": "ACAT",
        "status": "not started",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 2,
    },
    {
        "id": "A-2215",
        "household_id": "H-1051",
        "custodian": "Alder Clearing",
        "account_type": "Individual brokerage (TOD)",
        "registration": "TOD",
        "masked_number": "xxxx-6635",
        "transfer_path": "ACAT",
        "status": "not started",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 2,
    },
    {
        "id": "A-2216",
        "household_id": "H-1052",
        "custodian": "Alder Clearing",
        "account_type": "Traditional IRA",
        "registration": "IRA",
        "masked_number": "xxxx-2814",
        "transfer_path": "ACAT",
        "status": "waiting on custodian",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 1,
    },
    {
        "id": "A-2217",
        "household_id": "H-1053",
        "custodian": "Alder Clearing",
        "account_type": "Individual brokerage",
        "registration": "Individual",
        "masked_number": "xxxx-5906",
        "transfer_path": "ACAT",
        "status": "not started",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 2,
    },
    {
        "id": "A-2218",
        "household_id": "H-1053",
        "custodian": "Morrow Trust",
        "account_type": "Individual brokerage",
        "registration": "Individual",
        "masked_number": "xxxx-6401",
        "transfer_path": "new account",
        "status": "not started",
        "hard_case": "",
        "hard_case_note": "",
        "wave": 2,
    },
]

ACCOUNT_TARGETS = {
    "A-2201": "2146318.44",
    "A-2202": "4033681.56",
    "A-2203": "4250000.00",
    "A-2204": "1875000.00",
    "A-2205": "2640000.00",
    "A-2206": "1300000.00",
    "A-2207": "1260000.00",
    "A-2208": "1640000.00",
    "A-2209": "1250000.00",
    "A-2210": "780000.00",
    "A-2211": "650000.00",
    "A-2212": "5120000.00",
    "A-2213": "3600000.00",
    "A-2214": "3860000.00",
    "A-2215": "1980000.00",
    "A-2216": "3310000.00",
    "A-2217": "2605000.00",
    "A-2218": "0.00",
}

# Signature count estimates, assigned by account type. The build spec ranges are:
# joint or individual brokerage 2 to 3, IRA 3, trust 5 to 6, inherited IRA 5,
# annuity 4, UTMA 3, margin with options 4, LP assignment 6, held-away 401k 3,
# unfunded new account 2. Explicit values keep the output stable across runs.
SIGNATURE_COUNTS = {
    "A-2201": 2,  # joint brokerage
    "A-2202": 5,  # revocable trust
    "A-2203": 6,  # irrevocable trust
    "A-2204": 5,  # inherited IRA
    "A-2205": 3,  # individual brokerage (TOD)
    "A-2206": 4,  # variable annuity
    "A-2207": 3,  # Roth IRA
    "A-2208": 3,  # traditional IRA
    "A-2209": 3,  # held-away 401k rollover
    "A-2210": 3,  # UTMA
    "A-2211": 2,  # individual brokerage
    "A-2212": 4,  # margin account with options
    "A-2213": 6,  # LP assignment
    "A-2214": 2,  # individual brokerage
    "A-2215": 3,  # individual brokerage (TOD)
    "A-2216": 3,  # traditional IRA
    "A-2217": 2,  # individual brokerage
    "A-2218": 2,  # unfunded new account
}

POSITION_SPECS = {
    "A-2201": [
        ("VTI", "Total market ETF", 3120, "273.45", "642500.00", True, ""),
        ("VXUS", "Total international ETF", 2450, "71.20", "151300.00", True, ""),
        ("BND", "Total bond market ETF", 4800, "73.85", "371900.00", True, ""),
        ("VEA", "Developed markets ETF", 6250, "58.40", "318750.00", True, ""),
        ("NBMI", "Northbridge Materials", 1200, "84.15", "78400.00", True, ""),
        ("CHSY", "Calder Health Systems", 900, "132.60", "96100.00", True, ""),
    ],
    "A-2202": [
        ("VTI", "Total market ETF", 6400, "273.45", "1386000.00", True, ""),
        ("VXUS", "Total international ETF", 4200, "71.20", "268500.00", True, ""),
        ("BND", "Total bond market ETF", 7200, "73.85", "548200.00", True, ""),
        ("VWO", "Emerging markets ETF", 5400, "49.15", "232900.00", True, ""),
        ("HBFX", "Halcyon Balanced Fund, Class A", 26000, "38.42", "921400.00", True, ""),
    ],
    "A-2203": [
        ("VTI", "Total market ETF", 7100, "273.45", "1530000.00", True, ""),
        ("BND", "Total bond market ETF", 8500, "73.85", "641000.00", True, ""),
        ("VXUS", "Total international ETF", 5200, "71.20", "330000.00", True, ""),
        ("CHSY", "Calder Health Systems", 3400, "132.60", "301000.00", True, ""),
        ("HBFX", "Halcyon Balanced Fund, Class A", 19000, "38.42", "680000.00", True, ""),
    ],
    "A-2204": [
        ("VTI", "Total market ETF", 3600, "273.45", "812000.00", True, ""),
        ("VXUS", "Total international ETF", 2600, "71.20", "162000.00", True, ""),
        ("BND", "Total bond market ETF", 4200, "73.85", "289000.00", True, ""),
        ("VEA", "Developed markets ETF", 3400, "58.40", "171000.00", True, ""),
        ("NBMI", "Northbridge Materials", 800, "84.15", "54000.00", True, ""),
    ],
    "A-2205": [
        ("VTI", "Total market ETF", 4100, "273.45", "900000.00", True, ""),
        ("VXUS", "Total international ETF", 2900, "71.20", "178000.00", True, ""),
        ("BND", "Total bond market ETF", 5200, "73.85", "396000.00", True, ""),
        (
            "MSGF",
            "Meridian Select Growth Fund, Class C",
            24000,
            "26.84",
            "590000.00",
            False,
            "Class C shares are proprietary to Meridian Private Wealth",
        ),
        ("CHSY", "Calder Health Systems", 1200, "132.60", "128000.00", True, ""),
    ],
    "A-2206": [
        (
            "HAL-EQ",
            "Halden Equity Index Subaccount",
            12500,
            "42.80",
            "420000.00",
            False,
            "Contract exchange only; 1035 review required",
        ),
        (
            "HAL-BAL",
            "Halden Balanced Subaccount",
            9600,
            "31.45",
            "260000.00",
            False,
            "Contract exchange only; 1035 review required",
        ),
        (
            "HAL-BOND",
            "Halden Core Bond Subaccount",
            4800,
            "27.15",
            "115000.00",
            False,
            "Contract exchange only; 1035 review required",
        ),
        (
            "HAL-INTL",
            "Halden International Subaccount",
            7400,
            "25.10",
            "160000.00",
            False,
            "Contract exchange only; 1035 review required",
        ),
        (
            "HAL-FIX",
            "Halden Fixed Account",
            1,
            "147020.00",
            "150000.00",
            False,
            "Contract exchange only; 1035 review required",
        ),
    ],
    "A-2207": [
        ("VTI", "Total market ETF", 2200, "273.45", "480000.00", True, ""),
        ("VXUS", "Total international ETF", 1600, "71.20", "96000.00", True, ""),
        ("BND", "Total bond market ETF", 2400, "73.85", "162000.00", True, ""),
        ("VWO", "Emerging markets ETF", 1900, "49.15", "74000.00", True, ""),
        ("CHSY", "Calder Health Systems", 800, "132.60", "82000.00", True, ""),
        ("NBMI", "Northbridge Materials", 600, "84.15", "41000.00", True, ""),
    ],
    "A-2208": [
        ("VTI", "Total market ETF", 2800, "273.45", "610000.00", True, ""),
        ("VXUS", "Total international ETF", 2100, "71.20", "130000.00", True, ""),
        ("BND", "Total bond market ETF", 3200, "73.85", "220000.00", True, ""),
        ("VEA", "Developed markets ETF", 2600, "58.40", "129000.00", True, ""),
        ("HBFX", "Halcyon Balanced Fund, Class A", 5000, "38.42", "178000.00", True, ""),
    ],
    "A-2209": [
        ("VTI", "Total market ETF", 2400, "273.45", "520000.00", True, ""),
        ("BND", "Total bond market ETF", 2800, "73.85", "185000.00", True, ""),
        ("HBFX", "Halcyon Balanced Fund, Class A", 4000, "38.42", "140000.00", True, ""),
    ],
    "A-2210": [
        ("VTI", "Total market ETF", 1500, "273.45", "330000.00", True, ""),
        ("VXUS", "Total international ETF", 1000, "71.20", "60000.00", True, ""),
        ("BND", "Total bond market ETF", 1600, "73.85", "110000.00", True, ""),
        ("VEA", "Developed markets ETF", 1400, "58.40", "69000.00", True, ""),
        ("CHSY", "Calder Health Systems", 500, "132.60", "52000.00", True, ""),
    ],
    "A-2211": [
        ("VTI", "Total market ETF", 1200, "273.45", "260000.00", True, ""),
        ("VXUS", "Total international ETF", 800, "71.20", "48000.00", True, ""),
        ("BND", "Total bond market ETF", 1100, "73.85", "74000.00", True, ""),
        ("NBMI", "Northbridge Materials", 900, "84.15", "61000.00", True, ""),
        ("VEA", "Developed markets ETF", 900, "58.40", "44000.00", True, ""),
    ],
    "A-2212": [
        ("VTI", "Total market ETF", 12000, "273.45", "2450000.00", True, ""),
        ("VXUS", "Total international ETF", 5500, "71.20", "340000.00", True, ""),
        ("BND", "Total bond market ETF", 6000, "73.85", "410000.00", True, ""),
        ("CHSY", "Calder Health Systems", 3000, "132.60", "310000.00", True, ""),
        ("HBFX", "Halcyon Balanced Fund, Class A", 12000, "38.42", "420000.00", True, ""),
        (
            "VTI2701C280",
            "VTI Jan 2027 280 call",
            25,
            "1460.00",
            "18000.00",
            False,
            "Listed option; expires 2027-01-15",
        ),
    ],
    "A-2213": [
        (
            "KRVF2",
            "Kestrel Ridge Value Fund II, LP",
            3000,
            "1050.00",
            "3000000.00",
            False,
            "Assignment requires general partner consent; restriction through 2027-06-30",
        ),
        ("VTI", "Total market ETF", 500, "273.45", "96000.00", True, ""),
        ("BND", "Total bond market ETF", 1200, "73.85", "80000.00", True, ""),
    ],
    "A-2214": [
        ("VTI", "Total market ETF", 6200, "273.45", "1380000.00", True, ""),
        ("VXUS", "Total international ETF", 4000, "71.20", "250000.00", True, ""),
        ("BND", "Total bond market ETF", 5500, "73.85", "385000.00", True, ""),
        ("VEA", "Developed markets ETF", 4800, "58.40", "240000.00", True, ""),
        ("HBFX", "Halcyon Balanced Fund, Class A", 20000, "38.42", "700000.00", True, ""),
        ("CHSY", "Calder Health Systems", 1500, "132.60", "152000.00", True, ""),
    ],
    "A-2215": [
        ("VTI", "Total market ETF", 4000, "273.45", "890000.00", True, ""),
        ("VXUS", "Total international ETF", 2300, "71.20", "140000.00", True, ""),
        ("BND", "Total bond market ETF", 3000, "73.85", "200000.00", True, ""),
        ("VEA", "Developed markets ETF", 2600, "58.40", "128000.00", True, ""),
        ("CHSY", "Calder Health Systems", 1100, "132.60", "120000.00", True, ""),
    ],
    "A-2216": [
        ("VTI", "Total market ETF", 5400, "273.45", "1250000.00", True, ""),
        ("VXUS", "Total international ETF", 3600, "71.20", "210000.00", True, ""),
        ("BND", "Total bond market ETF", 4800, "73.85", "330000.00", True, ""),
        ("VEA", "Developed markets ETF", 4000, "58.40", "200000.00", True, ""),
        ("HBFX", "Halcyon Balanced Fund, Class A", 15000, "38.42", "540000.00", True, ""),
        ("NBMI", "Northbridge Materials", 2500, "84.15", "180000.00", True, ""),
    ],
    "A-2217": [
        ("VTI", "Total market ETF", 4500, "273.45", "1020000.00", True, ""),
        ("VXUS", "Total international ETF", 3000, "71.20", "185000.00", True, ""),
        ("BND", "Total bond market ETF", 4000, "73.85", "280000.00", True, ""),
        ("VWO", "Emerging markets ETF", 2500, "49.15", "105000.00", True, ""),
        ("HBFX", "Halcyon Balanced Fund, Class A", 10000, "38.42", "350000.00", True, ""),
        ("CHSY", "Calder Health Systems", 1000, "132.60", "110000.00", True, ""),
    ],
    "A-2218": [],
}

CONTACTS = {
    "H-1042": (
        "Jordan Rowan",
        "Primary",
        "jordan.rowan@example.com",
        "860-555-0142",
        "27 Fenwick Street, Guilford, CT 06437",
        "2016",
        "Address updated 2026-06-14",
    ),
    "H-1043": (
        "Margaret Calloway",
        "Primary",
        "margaret.calloway@example.com",
        "860-555-0168",
        "9 Hollow Brook Road, Cornwall, CT 06753",
        "2009",
        "Trustee contact on file",
    ),
    "H-1044": (
        "Ellis Barrett",
        "Primary",
        "ellis.barrett@example.com",
        "860-555-0173",
        "44 Sable Court, Mystic, CT 06355",
        "2021",
        "Beneficiary election pending",
    ),
    "H-1045": (
        "Elena Voss",
        "Primary",
        "elena.voss@example.com",
        "860-555-0119",
        "48 Ashcroft Lane, Madison, CT 06443",
        "2014",
        "Annuity contract on file",
    ),
    "H-1046": (
        "Nora Sato",
        "Primary",
        "nora.sato@example.com",
        "860-555-0134",
        "312 Willow Street, Apt 5, New Haven, CT 06511",
        "2019",
        "Email only",
    ),
    "H-1047": (
        "Marco Delgado",
        "Primary",
        "marco.delgado@example.com",
        "860-555-0156",
        "76 Orchard Hill Drive, Wallingford, CT 06492",
        "2012",
        "Plan statement requested",
    ),
    "H-1048": (
        "Adaeze Okafor",
        "Primary",
        "adaeze.okafor@example.com",
        "860-555-0181",
        "20 Brook Meadow Lane, Hamden, CT 06517",
        "2018",
        "Custodian for minor",
    ),
    "H-1049": (
        "Anders Lindqvist",
        "Primary",
        "anders.lindqvist@example.com",
        "860-555-0125",
        "158 Shore Road, Old Lyme, CT 06371",
        "2010",
        "Options approval on file",
    ),
    "H-1050": (
        "Thomas Ferraro",
        "Primary",
        "thomas.ferraro@example.com",
        "860-555-0192",
        "7 Pemberton Close, Greenwich, CT 06831",
        "2007",
        "LP consent requested",
    ),
    "H-1051": (
        "Grace Nakamura",
        "Primary",
        "grace.nakamura@example.com",
        "860-555-0107",
        "905 Chapel Street, New Haven, CT 06510",
        "2020",
        "Beneficiary form on file",
    ),
    "H-1052": (
        "Irina Petrov",
        "Primary",
        "irina.petrov@example.com",
        "860-555-0163",
        "63 Birchwood Drive, Cheshire, CT 06410",
        "2015",
        "IRA beneficiary on file",
    ),
    "H-1053": (
        "Willem Achterberg",
        "Primary",
        "willem.achterberg@example.com",
        "860-555-0188",
        "11 Stonegate Row, Litchfield, CT 06759",
        "2024",
        "New account in wave 2",
    ),
}

DOCUMENTS = [
    {
        "id": "D-1",
        "kind": "statement",
        "custodian": "Alder Clearing",
        "filename": "alder-statement-2026-10-rowan.txt",
        "received_at": "2026-11-05",
    },
    {
        "id": "D-2",
        "kind": "statement",
        "custodian": "Alder Clearing",
        "filename": "alder-statement-2026-09-voss.txt",
        "received_at": "2026-11-06",
    },
    {
        "id": "D-3",
        "kind": "statement",
        "custodian": "Morrow Trust",
        "filename": "morrow-statement-2026-10-rowan-trust.txt",
        "received_at": "2026-11-07",
    },
    {
        "id": "D-4",
        "kind": "crm_export",
        "custodian": "Meridian Private Wealth",
        "filename": "meridian-crm-contacts-2026-11-08.csv",
        "received_at": "2026-11-08",
    },
    {
        "id": "D-5",
        "kind": "trust_summary",
        "custodian": "Alder Clearing",
        "filename": "calloway-trust-summary.txt",
        "received_at": "2026-11-09",
    },
    {
        "id": "D-6",
        "kind": "annuity_summary",
        "custodian": "Morrow Trust",
        "filename": "voss-annuity-summary.txt",
        "received_at": "2026-11-10",
    },
]

COLUMN_HEADER = (
    f"{'Symbol':<7} {'Description':<36} {'Quantity':>10} "
    f"{'Price':>12} {'Value':>13} {'Cost basis':>12}"
)


def position_line(row) -> str:
    return (
        f"{row['symbol']:<7} {row['name']:<36} "
        f"{row['quantity']:>10,.2f} {row['price']:>12,.2f} "
        f"{row['value']:>13,.2f} {row['cost']:>12,.2f}"
    )


def build_statement(
    doc_id,
    custodian,
    account,
    registration,
    period,
    prepared,
    address_line,
    address_value,
    beneficiary,
    notes,
    rows,
    extra_fields,
    address_extra="",
    title_line="",
    tax_line="",
):
    total = sum((row["value"] for row in rows), d("0.00"))
    cost = sum((row["cost"] for row in rows), d("0.00"))
    gain = total - cost
    phone = "1-800-555-0142" if custodian == "Alder Clearing" else "1-800-555-0177"

    lines = [
        SPECIMEN_STATEMENT,
        custodian.upper(),
        "Monthly account statement",
        f"Statement period: {period}",
        f"Prepared: {prepared}",
        "",
        f"Account registration: {registration}",
    ]
    if title_line:
        lines.append(f"Account title: {title_line}")
    lines.extend(
        [
            f"Account number: {account['masked_number']}",
            f"Custodian: {custodian}",
            f"Address on file: {address_line}",
        ]
    )
    if address_extra:
        lines.append(address_extra)
    if tax_line:
        lines.append(tax_line)
    lines.extend(
        [
            f"Beneficiary: {beneficiary}",
            "",
            "HOLDINGS",
            COLUMN_HEADER,
        ]
    )
    for row in rows:
        lines.append(position_line(row))
    lines.extend(
        [
            "",
            "ACCOUNT SUMMARY",
            f"Total account value: USD {money(total)}",
            f"Total cost basis: USD {money(cost)}",
            f"Unrealized gain/loss: USD {money(gain)}",
            f"Positions held: {len(rows)}",
            "",
            "Notes",
        ]
    )
    for note in notes:
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            f"Questions: {custodian} Client Service, {phone}",
            "End of statement",
        ]
    )

    assert 25 <= len(lines) <= 45, f"{doc_id} statement has {len(lines)} lines"

    fields = [
        ("Registration", registration, 0.99, f"Account registration: {registration}"),
        ("Account number", account["masked_number"], 0.99, f"Account number: {account['masked_number']}"),
        ("Statement period", period, 0.98, f"Statement period: {period}"),
        ("Address on file", address_value, 0.94, f"Address on file: {address_value}"),
        ("Beneficiary", beneficiary, 0.93, f"Beneficiary: {beneficiary}"),
        ("Account value", money(total), 0.99, f"Total account value: USD {money(total)}"),
        ("Cost basis", money(cost), 0.97, f"Total cost basis: USD {money(cost)}"),
    ]
    fields.extend(extra_fields)
    return NL.join(lines), fields


def build_crm(accounts):
    lines = [
        SPECIMEN_CRM,
        "MERIDIAN PRIVATE WEALTH - CRM CONTACT EXPORT",
        "Export date: 2026-11-08",
        "Records: 12 households, 18 linked accounts",
        "Source: advisor book transferred at signature",
        "",
        "CONTACTS AND HOUSEHOLD LINKS",
        "household_id,household_name,contact_name,role,email,phone,mailing_address,client_since,linked_accounts,data_quality_note",
    ]

    linked = {}
    for account in accounts:
        linked.setdefault(account["household_id"], []).append(account["id"])

    rows = {}
    for household in HOUSEHOLDS:
        hid = household[0]
        contact, role, email, phone, address, since, note = CONTACTS[hid]
        accounts_text = "; ".join(linked[hid])
        row = (
            f'{hid},{household[1]},{contact},{role},{email},{phone},'
            f'"{address}",{since},"{accounts_text}",{note}'
        )
        rows[hid] = row
        lines.append(row)

    lines.extend(
        [
            "",
            "ACCOUNT LINKS",
            "household_id,account_id,custodian,registration",
        ]
    )
    for account in accounts:
        lines.append(
            f"{account['household_id']},{account['id']},{account['custodian']},{account['registration']}"
        )

    lines.extend(
        [
            "",
            "Notes",
            "- Email addresses use the example.com domain reserved for documentation.",
            "- Phone numbers use the 555-01xx range reserved for fictional use.",
            "- This export is synthetic and was prepared for a product demonstration.",
        ]
    )

    body = NL.join(lines)
    row = rows["H-1042"]
    address = CONTACTS["H-1042"][4]
    accounts_text = "; ".join(linked["H-1042"])

    fields = [
        ("Household ID", "H-1042", 0.99, "H-1042,Rowan Household"),
        ("Household name", "Rowan Household", 0.99, "H-1042,Rowan Household"),
        ("Primary contact", "Jordan Rowan", 0.98, "Jordan Rowan,Primary"),
        ("Current address", address, 0.97, f'"{address}"'),
        ("Client since", "2016", 0.96, ",2016,"),
        ("Accounts linked", accounts_text, 0.98, f'"{accounts_text}"'),
        ("Data quality note", "Address updated 2026-06-14", 0.92, "Address updated 2026-06-14"),
    ]
    assert row in body
    return body, fields


def build_trust_summary():
    lines = [
        SPECIMEN_TRUST,
        "TRUST DOCUMENT SUMMARY",
        "Trust name: Calloway Family Irrevocable Trust",
        "Trust type: Irrevocable",
        "Trust date: 2022-03-18",
        "Grantor: Margaret Calloway",
        "Trustee: Devon Calloway",
        "Successor trustee: Harbor Fiduciary Services",
        "Governing state: Connecticut",
        "Trust EIN: pending",
        "Beneficiaries: Two children of the grantor, equal shares",
        "Distribution standard: Income and principal for health, education, maintenance, and support",
        "Trustee certification: Not signed",
        "Account to be opened: Irrevocable trust account at Alder Clearing",
        "Counsel: Whitcomb & Reed LLC",
        "",
        "Notes",
        "- Trustee certification must be signed before the account can be titled.",
        "- The trust EIN has not been issued.",
        "- No account number is assigned until the trust account is opened.",
        "- This summary is synthetic and was prepared for a product demonstration.",
    ]
    body = NL.join(lines)
    fields = [
        ("Trust name", "Calloway Family Irrevocable Trust", 0.99, "Trust name: Calloway Family Irrevocable Trust"),
        ("Trust type", "Irrevocable", 0.99, "Trust type: Irrevocable"),
        ("Trust date", "2022-03-18", 0.98, "Trust date: 2022-03-18"),
        ("Trustee", "Devon Calloway", 0.97, "Trustee: Devon Calloway"),
        ("Successor trustee", "Harbor Fiduciary Services", 0.96, "Successor trustee: Harbor Fiduciary Services"),
        ("Governing state", "Connecticut", 0.99, "Governing state: Connecticut"),
        ("Trust EIN", "pending", 0.98, "Trust EIN: pending"),
        (
            "Beneficiaries",
            "Two children of the grantor, equal shares",
            0.95,
            "Beneficiaries: Two children of the grantor, equal shares",
        ),
        (
            "Distribution standard",
            "Income and principal for health, education, maintenance, and support",
            0.94,
            "Distribution standard: Income and principal for health, education, maintenance, and support",
        ),
        (
            "Account to open",
            "Irrevocable trust account at Alder Clearing",
            0.97,
            "Account to be opened: Irrevocable trust account at Alder Clearing",
        ),
        (
            "FLAG: trust EIN absent",
            "FLAG: trust EIN pending; account cannot be titled",
            0.98,
            "Trust EIN: pending",
        ),
    ]
    return body, fields


def build_annuity_summary():
    lines = [
        SPECIMEN_ANNUITY,
        "ANNUITY CONTRACT SUMMARY",
        "Issuer: Halden Life Insurance Company",
        "Contract number: xxxx-8820",
        "Contract owner: Elena Voss",
        "Annuitant: Elena Voss",
        "Contract type: Variable annuity, non-qualified",
        "Issue date: 2018-06-30",
        "Contract value: USD 1,300,000.00",
        "Surrender charge: 3.0% of contract value through 2028-06-30",
        "Free withdrawal: 10% of contract value per year",
        "Death benefit: Return of contract value",
        "1035 exchange: Eligible; receiving carrier acceptance required",
        "Qualified plan: No",
        "",
        "SUBACCOUNTS",
        "HAL-EQ Halden Equity Index Subaccount",
        "HAL-BAL Halden Balanced Subaccount",
        "HAL-BOND Halden Core Bond Subaccount",
        "HAL-INTL Halden International Subaccount",
        "HAL-FIX Halden Fixed Account",
        "",
        "Notes",
        "- Surrender charge applies to amounts withdrawn above the free withdrawal allowance.",
        "- A 1035 exchange preserves tax deferral if the receiving carrier accepts the contract.",
        "- This summary is synthetic and was prepared for a product demonstration.",
    ]
    body = NL.join(lines)
    fields = [
        ("Contract owner", "Elena Voss", 0.99, "Contract owner: Elena Voss"),
        ("Annuitant", "Elena Voss", 0.99, "Annuitant: Elena Voss"),
        ("Issuer", "Halden Life Insurance Company", 0.99, "Issuer: Halden Life Insurance Company"),
        ("Contract number", "xxxx-8820", 0.99, "Contract number: xxxx-8820"),
        ("Contract type", "Variable annuity, non-qualified", 0.98, "Contract type: Variable annuity, non-qualified"),
        ("Issue date", "2018-06-30", 0.98, "Issue date: 2018-06-30"),
        ("Contract value", "1,300,000.00", 0.99, "Contract value: USD 1,300,000.00"),
        (
            "Surrender charge",
            "3.0% through 2028-06-30",
            0.96,
            "Surrender charge: 3.0% of contract value through 2028-06-30",
        ),
        (
            "1035 exchange",
            "Eligible; receiving carrier acceptance required",
            0.95,
            "1035 exchange: Eligible; receiving carrier acceptance required",
        ),
        (
            "FLAG: annuity surrender charge",
            "FLAG: 3.0% surrender charge applies through 2028-06-30",
            0.96,
            "Surrender charge: 3.0% of contract value through 2028-06-30",
        ),
    ]
    return body, fields


TASKS = [
    ("A-2201", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-05", "complete", ""),
    ("A-2201", "document collection", "Collect October statement and photo ID", "transition specialist", "2026-11-09", "complete", ""),
    ("A-2201", "transfer initiation", "Submit ACAT request to Alder Clearing", "transition specialist", "2026-11-12", "complete", ""),
    ("A-2201", "custodian review", "Resolve address discrepancy with client", "transition specialist", "2026-11-19", "blocked", "Client confirmation of current address pending"),
    ("A-2201", "confirmation", "Confirm cost basis and positions received", "operations analyst", "2026-11-27", "not started", ""),
    ("A-2201", "handoff", "Notify advisor that account is funded", "transition specialist", "2026-12-02", "not started", ""),

    ("A-2202", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-05", "complete", ""),
    ("A-2202", "document collection", "Collect trustee certification", "transition specialist", "2026-11-20", "blocked", "Trustee signature pending"),
    ("A-2202", "transfer initiation", "Submit ACAT request after trust retitling", "transition specialist", "2026-11-25", "not started", ""),
    ("A-2202", "confirmation", "Confirm trust titling and positions received", "operations analyst", "2026-12-02", "not started", ""),
    ("A-2202", "handoff", "Notify advisor that account is funded", "transition specialist", "2026-12-07", "not started", ""),

    ("A-2203", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2203", "document collection", "Obtain trust EIN and signed trustee certification", "transition specialist", "2026-11-21", "blocked", "EIN pending from trustee counsel"),
    ("A-2203", "account opening", "Open irrevocable trust account at Alder Clearing", "operations analyst", "2026-11-21", "blocked", "EIN pending from trustee counsel"),
    ("A-2203", "transfer initiation", "Prepare non-ACAT transfer packet", "transition specialist", "2026-12-01", "not started", ""),
    ("A-2203", "custodian review", "Submit packet to Alder Clearing trust desk", "transition specialist", "2026-12-04", "not started", ""),
    ("A-2203", "handoff", "Confirm trust account funded", "advisor", "2026-12-10", "not started", ""),

    ("A-2204", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2204", "document collection", "Collect death certificate and beneficiary election", "transition specialist", "2026-11-18", "in progress", ""),
    ("A-2204", "transfer initiation", "Submit ACAT request with inherited IRA title", "transition specialist", "2026-11-13", "complete", ""),
    ("A-2204", "custodian review", "Confirm inherited IRA titling with Morrow Trust", "operations analyst", "2026-11-24", "in progress", ""),
    ("A-2204", "confirmation", "Confirm beneficiary designation and cost basis", "operations analyst", "2026-12-01", "not started", ""),
    ("A-2204", "handoff", "Notify advisor that account is funded", "transition specialist", "2026-12-07", "not started", ""),

    ("A-2205", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2205", "document collection", "Collect beneficiary designation form", "transition specialist", "2026-11-19", "blocked", "No beneficiary on the September statement"),
    ("A-2205", "transfer initiation", "Submit ACAT request to Alder Clearing", "transition specialist", "2026-11-13", "complete", ""),
    ("A-2205", "custodian review", "Confirm Class C redemption instructions", "transition specialist", "2026-11-24", "blocked", "Client election on redemption pending"),
    ("A-2205", "confirmation", "Confirm cash proceeds and cost basis", "operations analyst", "2026-12-01", "not started", ""),
    ("A-2205", "handoff", "Notify advisor that account is funded", "transition specialist", "2026-12-07", "not started", ""),

    ("A-2206", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2206", "document collection", "Obtain annuity contract and surrender schedule", "transition specialist", "2026-11-10", "complete", ""),
    ("A-2206", "transfer initiation", "Prepare 1035 exchange analysis", "compliance", "2026-11-24", "in progress", ""),
    ("A-2206", "custodian review", "Submit 1035 exchange request to Halden Life", "transition specialist", "2026-12-02", "not started", ""),
    ("A-2206", "handoff", "Confirm contract values with advisor", "advisor", "2026-12-09", "not started", ""),

    ("A-2207", "intake", "Confirm household record and account authority", "transition specialist", "2026-12-01", "not started", ""),
    ("A-2207", "document collection", "Collect Roth IRA statement and photo ID", "transition specialist", "2026-12-04", "not started", ""),
    ("A-2207", "transfer initiation", "Submit ACAT request to Alder Clearing", "operations analyst", "2026-12-10", "not started", ""),

    ("A-2208", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2208", "document collection", "Collect August and September statements", "transition specialist", "2026-11-10", "complete", ""),
    ("A-2208", "transfer initiation", "Submit ACAT request to Morrow Trust", "transition specialist", "2026-11-13", "complete", ""),
    ("A-2208", "custodian review", "Monitor Morrow Trust review", "operations analyst", "2026-11-24", "in progress", ""),
    ("A-2208", "confirmation", "Confirm cost basis and positions received", "operations analyst", "2026-12-02", "not started", ""),

    ("A-2209", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2209", "document collection", "Collect plan statement and rollover election", "transition specialist", "2026-11-20", "in progress", ""),
    ("A-2209", "transfer initiation", "Submit rollover request to Northline Retirement Services", "compliance", "2026-11-25", "blocked", "Plan approval and spousal consent pending"),
    ("A-2209", "confirmation", "Confirm rollover deposit", "operations analyst", "2026-12-08", "not started", ""),

    ("A-2210", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2210", "document collection", "Collect UTMA custodianship certification", "transition specialist", "2026-11-10", "complete", ""),
    ("A-2210", "transfer initiation", "Submit ACAT request for UTMA", "transition specialist", "2026-11-13", "complete", ""),
    ("A-2210", "custodian review", "Confirm UTMA titling with Alder Clearing", "operations analyst", "2026-11-24", "in progress", ""),
    ("A-2210", "confirmation", "Confirm positions and cost basis", "transition specialist", "2026-12-02", "not started", ""),

    ("A-2211", "intake", "Confirm household record and account authority", "transition specialist", "2026-12-02", "not started", ""),
    ("A-2211", "document collection", "Collect September statement and photo ID", "transition specialist", "2026-12-07", "not started", ""),
    ("A-2211", "transfer initiation", "Submit ACAT request", "operations analyst", "2026-12-11", "not started", ""),

    ("A-2212", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2212", "document collection", "Collect margin agreement and options approval record", "transition specialist", "2026-11-10", "complete", ""),
    ("A-2212", "transfer initiation", "Review option positions for transfer eligibility", "compliance", "2026-11-20", "blocked", "Two option positions expire after target live date"),
    ("A-2212", "custodian review", "Submit in-kind review packet", "transition specialist", "2026-11-30", "not started", ""),
    ("A-2212", "confirmation", "Confirm option settlement and cash transfer", "operations analyst", "2026-12-08", "not started", ""),

    ("A-2213", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2213", "document collection", "Obtain general partner consent for LP assignment", "compliance", "2026-11-25", "blocked", "General partner consent pending"),
    ("A-2213", "transfer initiation", "Submit assignment packet to Morrow Trust", "compliance", "2026-11-30", "blocked", "General partner consent pending"),
    ("A-2213", "custodian review", "Confirm assignment acceptance", "transition specialist", "2026-12-08", "not started", ""),
    ("A-2213", "handoff", "Notify advisor of alternative asset status", "advisor", "2026-12-15", "not started", ""),

    ("A-2214", "intake", "Confirm household record and account authority", "transition specialist", "2026-12-01", "not started", ""),
    ("A-2214", "document collection", "Collect September statement and photo ID", "transition specialist", "2026-12-04", "not started", ""),
    ("A-2214", "transfer initiation", "Submit ACAT request", "operations analyst", "2026-12-10", "not started", ""),

    ("A-2215", "intake", "Confirm household record and account authority", "transition specialist", "2026-12-01", "in progress", ""),
    ("A-2215", "document collection", "Collect TOD beneficiary form", "transition specialist", "2026-12-08", "not started", ""),
    ("A-2215", "transfer initiation", "Submit ACAT request", "operations analyst", "2026-12-11", "not started", ""),

    ("A-2216", "intake", "Confirm household record and account authority", "transition specialist", "2026-11-06", "complete", ""),
    ("A-2216", "document collection", "Collect July and August statements", "transition specialist", "2026-11-10", "complete", ""),
    ("A-2216", "transfer initiation", "Submit ACAT request to Alder Clearing", "transition specialist", "2026-11-13", "complete", ""),
    ("A-2216", "custodian review", "Respond to Alder Clearing cost basis request", "operations analyst", "2026-11-24", "in progress", ""),
    ("A-2216", "confirmation", "Confirm positions received", "operations analyst", "2026-12-02", "not started", ""),

    ("A-2217", "intake", "Confirm household record and account authority", "transition specialist", "2026-12-01", "not started", ""),
    ("A-2217", "document collection", "Collect September statement and photo ID", "transition specialist", "2026-12-04", "not started", ""),
    ("A-2217", "transfer initiation", "Submit ACAT request", "operations analyst", "2026-12-10", "not started", ""),

    ("A-2218", "account opening", "Open individual brokerage account at Morrow Trust", "operations analyst", "2026-12-04", "in progress", ""),
    ("A-2218", "document collection", "Collect signed new account application and tax form", "transition specialist", "2026-12-08", "not started", ""),
    ("A-2218", "handoff", "Confirm account number and notify advisor", "transition specialist", "2026-12-15", "not started", ""),
]

RISKS = [
    {
        "id": "R-1",
        "account_id": "A-2201",
        "kind": "NIGO",
        "severity": "medium",
        "reason": "Alder statement address was last updated 2019-04-12; CRM address was updated 2026-06-14.",
        "rule_ref": "Alder Clearing transfer requirements 4.2",
        "clears_with": "Signed address change form and client confirmation",
        "status": "open",
    },
    {
        "id": "R-2",
        "account_id": "A-2205",
        "kind": "NIGO",
        "severity": "high",
        "reason": "TOD registration has no beneficiary designation on the September statement.",
        "rule_ref": "Alder Clearing transfer requirements 5.1",
        "clears_with": "Signed beneficiary designation form",
        "status": "open",
    },
    {
        "id": "R-3",
        "account_id": "A-2205",
        "kind": "NIGO",
        "severity": "high",
        "reason": "Meridian Select Growth Fund Class C shares are proprietary and cannot transfer in kind.",
        "rule_ref": "Alder Clearing proprietary fund policy 3.4",
        "clears_with": "Client election to redeem and repurchase at the receiving custodian",
        "status": "open",
    },
    {
        "id": "R-4",
        "account_id": "A-2202",
        "kind": "NIGO",
        "severity": "high",
        "reason": "Morrow Trust statement titles the account as Individual; the account record requires Revocable trust.",
        "rule_ref": "Morrow Trust account titling requirements 2.1",
        "clears_with": "Signed account title change form and trustee certification",
        "status": "open",
    },
    {
        "id": "R-5",
        "account_id": "A-2203",
        "kind": "NIGO",
        "severity": "high",
        "reason": "Trust EIN is pending, so the irrevocable trust account cannot be titled.",
        "rule_ref": "Alder Clearing trust account requirements 3.2",
        "clears_with": "EIN confirmation from trustee counsel",
        "status": "open",
    },
    {
        "id": "R-6",
        "account_id": "A-2206",
        "kind": "NIGO",
        "severity": "medium",
        "reason": "A 3.0% surrender charge applies to withdrawals above the free allowance through 2028-06-30.",
        "rule_ref": "Morrow Trust annuity servicing guide 4.3",
        "clears_with": "1035 exchange analysis and client acknowledgment",
        "status": "open",
    },
    {
        "id": "R-7",
        "account_id": "A-2212",
        "kind": "NIGO",
        "severity": "high",
        "reason": "Two listed option positions expire after the target live date and cannot move in kind.",
        "rule_ref": "Alder Clearing options transfer policy 2.4",
        "clears_with": "Option liquidation or expiration before transfer",
        "status": "open",
    },
    {
        "id": "R-8",
        "account_id": "A-2213",
        "kind": "NIGO",
        "severity": "high",
        "reason": "Kestrel Ridge Fund II units require general partner consent before assignment.",
        "rule_ref": "Morrow Trust alternative assets policy 5.2",
        "clears_with": "Signed general partner consent",
        "status": "open",
    },
    {
        "id": "R-9",
        "account_id": "A-2209",
        "kind": "NIGO",
        "severity": "medium",
        "reason": "Northline plan requires a signed rollover election and spousal consent before release.",
        "rule_ref": "Morrow Trust rollover requirements 6.1",
        "clears_with": "Signed rollover election and spousal consent",
        "status": "mitigating",
    },
]

FLAG_TO_RISK = {
    ("D-1", "FLAG: address stale"): "R-1",
    ("D-2", "FLAG: beneficiary missing"): "R-2",
    ("D-2", "FLAG: non-transferable share class"): "R-3",
    ("D-3", "FLAG: registration mismatch"): "R-4",
    ("D-5", "FLAG: trust EIN absent"): "R-5",
    ("D-6", "FLAG: annuity surrender charge"): "R-6",
}

DOC_ACCOUNT = {
    "D-1": "A-2201",
    "D-2": "A-2205",
    "D-3": "A-2202",
    "D-5": "A-2203",
    "D-6": "A-2206",
}


def build_positions():
    ledger = {}
    positions = []
    position_id = 0
    for account in ACCOUNTS:
        aid = account["id"]
        target = d(ACCOUNT_TARGETS[aid])
        rows = []
        running = d("0.00")
        for spec in POSITION_SPECS[aid]:
            symbol, name, quantity, price_text, cost_text, transferable, note = spec
            price = d(price_text)
            value = cents(d(quantity) * price)
            cost = d(cost_text)
            running += value
            rows.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "quantity": quantity,
                    "price": price,
                    "value": value,
                    "cost": cost,
                    "transferable": transferable,
                    "note": note,
                }
            )
        cash = target - running
        if cash < 0:
            raise AssertionError(f"{aid} positions exceed the account target by {money(-cash)}")
        if cash > 0:
            rows.append(
                {
                    "symbol": "CASH",
                    "name": "Cash and cash equivalents",
                    "quantity": 1,
                    "price": cash,
                    "value": cash,
                    "cost": cash,
                    "transferable": True,
                    "note": "",
                }
            )
        total = sum((row["value"] for row in rows), d("0.00"))
        assert total == target, f"{aid} positions total {total}, expected {target}"
        for row in rows:
            position_id += 1
            positions.append(
                {
                    "id": f"P-{position_id}",
                    "account_id": aid,
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "quantity": row["quantity"],
                    "value": number(row["value"]),
                    "cost_basis": number(row["cost"]),
                    "transferable": row["transferable"],
                    "note": row["note"],
                }
            )
        ledger[aid] = rows
    return ledger, positions


def build_documents(ledger):
    d1_rows = ledger["A-2201"]
    d2_rows = ledger["A-2205"]
    d3_rows = ledger["A-2202"]

    transfer_note = (
        "Class C shares are proprietary to Meridian Private Wealth and are not eligible for transfer in kind."
    )
    d1_address_line = "1184 Larkspur Lane, Unit 3, Branford, CT 06405 (last updated 2019-04-12)"
    d1_address_value = "1184 Larkspur Lane, Unit 3, Branford, CT 06405"
    d3_address = "27 Fenwick Street, Guilford, CT 06437"
    d2_address = "48 Ashcroft Lane, Madison, CT 06443"

    d1_extra = [
        (
            "FLAG: address stale",
            "FLAG: address last updated 2019-04-12; CRM shows 2026-06-14",
            0.96,
            f"Address on file: {d1_address_line}",
        )
    ]
    d2_extra = [
        ("Fund holding", "Meridian Select Growth Fund, Class C", 0.94, "Meridian Select Growth Fund, Class C"),
        ("Share class", "Class C", 0.95, transfer_note),
        (
            "FLAG: beneficiary missing",
            "FLAG: TOD registration has no designated beneficiary",
            0.97,
            "Beneficiary: none designated",
        ),
        (
            "FLAG: non-transferable share class",
            "FLAG: Class C shares are proprietary and cannot transfer in kind",
            0.96,
            transfer_note,
        ),
    ]
    d3_extra = [
        ("Custodian", "Morrow Trust", 0.99, "Custodian: Morrow Trust"),
        (
            "FLAG: registration mismatch",
            "FLAG: statement shows Individual; account record requires Revocable trust",
            0.95,
            "Account registration: Individual",
        ),
    ]

    d1_body, d1_fields = build_statement(
        "D-1",
        "Alder Clearing",
        next(a for a in ACCOUNTS if a["id"] == "A-2201"),
        "JTWROS",
        "2026-10-01 to 2026-10-31",
        "2026-11-02",
        d1_address_line,
        d1_address_value,
        "not applicable, joint registration",
        [
            "The address on file was last confirmed on 2019-04-12.",
            "Prices are month-end closing prices.",
            "Cost basis is provided for information only and may not reflect all adjustments.",
            "This statement is synthetic and was generated for a product demonstration.",
        ],
        d1_rows,
        d1_extra,
    )

    d2_body, d2_fields = build_statement(
        "D-2",
        "Alder Clearing",
        next(a for a in ACCOUNTS if a["id"] == "A-2205"),
        "TOD",
        "2026-09-01 to 2026-09-30",
        "2026-10-02",
        d2_address,
        d2_address,
        "none designated",
        [
            transfer_note,
            "Prices are month-end closing prices.",
            "Cost basis is provided for information only and may not reflect all adjustments.",
            "This statement is synthetic and was generated for a product demonstration.",
        ],
        d2_rows,
        d2_extra,
    )

    d3_body, d3_fields = build_statement(
        "D-3",
        "Morrow Trust",
        next(a for a in ACCOUNTS if a["id"] == "A-2202"),
        "Individual",
        "2026-10-01 to 2026-10-31",
        "2026-11-03",
        d3_address,
        d3_address,
        "Rowan Family Trust dated 2021-05-14",
        [
            "Trust certification and trustee signatures are required before this account is retitled.",
            "Prices are month-end closing prices.",
            "Cost basis is provided for information only and may not reflect all adjustments.",
            "This statement is synthetic and was generated for a product demonstration.",
        ],
        d3_rows,
        d3_extra,
    )

    d4_body, d4_fields = build_crm(ACCOUNTS)
    d5_body, d5_fields = build_trust_summary()
    d6_body, d6_fields = build_annuity_summary()

    return {
        "D-1": (d1_body, d1_fields),
        "D-2": (d2_body, d2_fields),
        "D-3": (d3_body, d3_fields),
        "D-4": (d4_body, d4_fields),
        "D-5": (d5_body, d5_fields),
        "D-6": (d6_body, d6_fields),
    }


def signature_range(account):
    """Allowed signature_count range for one account, from the build spec."""

    if account["hard_case"] == "LP alternative interest":
        return (6, 6)
    if account["hard_case"] == "Margin account with options":
        return (4, 4)
    if account["hard_case"] == "Held-away 401k":
        return (3, 3)
    if account["transfer_path"] == "new account":
        return (2, 2)
    registration = account["registration"]
    if registration in {"Revocable trust", "Irrevocable trust"}:
        return (5, 6)
    if registration == "Inherited IRA":
        return (5, 5)
    if account["account_type"] == "Variable annuity":
        return (4, 4)
    if registration == "UTMA":
        return (3, 3)
    if registration in {"IRA", "Roth IRA"}:
        return (3, 3)
    if registration in {"JTWROS", "TOD", "Individual"}:
        return (2, 3)
    raise AssertionError(f"{account['id']} has no signature_count range")


def build_accounts():
    """Accounts with the additive signature_count estimate appended."""

    account_ids = {account["id"] for account in ACCOUNTS}
    if set(SIGNATURE_COUNTS) != account_ids:
        raise AssertionError("SIGNATURE_COUNTS does not cover exactly the account ids")
    accounts = []
    for account in ACCOUNTS:
        count = SIGNATURE_COUNTS[account["id"]]
        low, high = signature_range(account)
        if not low <= count <= high:
            raise AssertionError(
                f"{account['id']} signature_count {count} is outside {low} to {high}"
            )
        row = dict(account)
        row["signature_count"] = count
        accounts.append(row)
    return accounts


def build_households(ledger):
    households = []
    for hid, display_name, expected_aum, relationship, notes in HOUSEHOLDS:
        account_ids = [a["id"] for a in ACCOUNTS if a["household_id"] == hid]
        computed = sum(
            (row["value"] for aid in account_ids for row in ledger[aid]),
            d("0.00"),
        )
        assert computed == d(expected_aum), f"{hid} AUM {computed}, expected {expected_aum}"
        households.append(
            {
                "id": hid,
                "display_name": display_name,
                "aum": number(computed),
                "relationship": relationship,
                "notes": notes,
                "consent_status": CONSENT_STATUS[hid],
            }
        )
    return households


def build_tasks():
    tasks = []
    for index, (account_id, phase, title, owner_role, due_at, status, blocker) in enumerate(TASKS, start=1):
        tasks.append(
            {
                "id": f"T-{index}",
                "account_id": account_id,
                "phase": phase,
                "title": title,
                "owner_role": owner_role,
                "due_at": due_at,
                "status": status,
                "blocker": blocker,
            }
        )
    return tasks


def build_scenario(book, households, accounts):
    hard_case_lines = []
    for account in accounts:
        if account["hard_case"]:
            hard_case_lines.append(f"- {account['hard_case']}, {account['id']}")

    pending = [h["id"] for h in households if h["consent_status"] == "pending"]
    not_required = [h["id"] for h in households if h["consent_status"] == "not required"]
    received_count = sum(1 for h in households if h["consent_status"] == "received")
    assert len(pending) == 3, f"expected three pending households, found {pending}"
    assert len(not_required) == 1, f"expected one not-required household, found {not_required}"
    pending_text = ", ".join(pending[:-1]) + ", and " + pending[-1]

    lines = [
        "# Scenario",
        "",
        "Synthetic data for the Transition Copilot work sample. Every name, custodian, account number, and address is invented. Account numbers are masked and no tax identifiers are present.",
        "",
        f"Dana Whitfield leaves Meridian Private Wealth for the receiving RIA. The book signs on 2026-11-03 with a target live date of 2026-12-18. Priya Raman is the transition lead. Total AUM is USD {book['book']['aum_total']:,} across {len(households)} households and {len(accounts)} accounts.",
        "",
        "Accounts sit at two fictional custodians: Alder Clearing and Morrow Trust. Twelve accounts are in wave 1 and six are in wave 2.",
        "",
        "Hard cases, one account each:",
    ]
    lines.extend(hard_case_lines)
    lines.extend(
        [
            "",
            "Documents: three statements (two Alder Clearing, one Morrow Trust), one CRM contact export, one trust document summary, one annuity contract summary. Six hard findings are seeded: beneficiary missing, address stale, registration mismatch, non-transferable share class, annuity surrender charge, trust EIN absent.",
            "",
            "Protocol and consent: the Broker Protocol packet arrived on 2026-11-03 with five permitted data elements (client name, address, phone, email, and account title) and three restrictions: no client contact before resignation, protocol data used only for transition preparation until client consent, and no solicitation. Consent status is received for "
            f"{received_count} households, pending for {pending_text}, and not required for {not_required[0]}, the household opening a new account with new money in wave 2. Client-facing drafts for a pending household stay blocked until consent is received. Each account also carries a signature_count estimate between 2 and 6, assigned by account type.",
            "",
            "Value model: the estimate is account-based. Each account is modelled at 45 minutes of manual operations time. A book of 100 clients at 5 accounts each is about 500 accounts, which is 375 hours, plus 60 hours of planning and reconciliation, so 435 hours per transition. A 25 to 35 percent reduction is 109 to 152 hours, or 14 to 19 working days. At 40 transitions a year that is 4,360 to 6,080 hours. These are modelled estimates, not measurements.",
            "",
            "Regenerate the dataset with python3 scripts/generate_book.py. Output is deterministic.",
        ]
    )
    assert 20 <= len(lines) <= 32, f"SCENARIO.md has {len(lines)} lines"
    return NL.join(lines) + NL


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + NL, encoding="utf-8")


def validate(book, document_payloads, labeled):
    problems = []
    document_by_id = {doc["id"]: doc for doc in document_payloads}
    account_by_id = {account["id"]: account for account in ACCOUNTS}
    household_by_id = {household["id"]: household for household in book["households"]}

    expected_keys = {
        "book",
        "households",
        "accounts",
        "positions",
        "documents",
        "extractions",
        "tasks",
        "risks",
    }
    if set(book) != expected_keys:
        problems.append(f"top level keys are {sorted(book)}")

    protocol = book["book"].get("protocol")
    expected_protocol_fields = ["Client name", "Address", "Phone", "Email", "Account title"]
    if not isinstance(protocol, dict):
        problems.append("book.protocol is missing")
    else:
        if set(protocol) != {"received_at", "permitted_fields", "restrictions", "source_note"}:
            problems.append(f"book.protocol keys are {sorted(protocol)}")
        if protocol.get("received_at") != "2026-11-03":
            problems.append(f"book.protocol received_at is {protocol.get('received_at')!r}")
        if protocol.get("permitted_fields") != expected_protocol_fields:
            problems.append(
                f"book.protocol permitted_fields are {protocol.get('permitted_fields')!r}"
            )
        restrictions = protocol.get("restrictions")
        if not isinstance(restrictions, list) or len(restrictions) != 3:
            problems.append(f"book.protocol restrictions are {restrictions!r}")
        if not isinstance(protocol.get("source_note"), str) or not protocol["source_note"]:
            problems.append("book.protocol source_note is missing")

    if len(book["households"]) != 12:
        problems.append(f"household count is {len(book['households'])}")
    if len(book["accounts"]) != 18:
        problems.append(f"account count is {len(book['accounts'])}")
    if len(book["positions"]) != 103:
        problems.append(f"position count is {len(book['positions'])}")
    if len(book["documents"]) != 6:
        problems.append(f"document count is {len(book['documents'])}")

    if [h["id"] for h in book["households"]] != [h[0] for h in HOUSEHOLDS]:
        problems.append("household ids or ordering changed")
    if [a["id"] for a in book["accounts"]] != [a["id"] for a in ACCOUNTS]:
        problems.append("account ids or ordering changed")

    kind_counts = {}
    for document in book["documents"]:
        kind_counts[document["kind"]] = kind_counts.get(document["kind"], 0) + 1
    expected_kinds = {
        "statement": 3,
        "crm_export": 1,
        "trust_summary": 1,
        "annuity_summary": 1,
    }
    if kind_counts != expected_kinds:
        problems.append(f"document kinds are {kind_counts}")

    wave_one = [account for account in ACCOUNTS if account["wave"] == 1]
    wave_two = [account for account in ACCOUNTS if account["wave"] == 2]
    if len(wave_one) != 12 or len(wave_two) != 6:
        problems.append(f"wave counts are {len(wave_one)} and {len(wave_two)}")

    found_hard_cases = {account["hard_case"] for account in ACCOUNTS if account["hard_case"]}
    if found_hard_cases != set(HARD_CASES.values()):
        problems.append(f"hard case set is {sorted(found_hard_cases)}")

    for account in ACCOUNTS:
        if account["household_id"] not in household_by_id:
            problems.append(f"{account['id']} references missing household {account['household_id']}")
        if account["custodian"] not in BOOK["custodians"]:
            problems.append(f"{account['id']} custodian {account['custodian']}")
        if account["transfer_path"] not in TRANSFER_PATHS:
            problems.append(f"{account['id']} transfer path {account['transfer_path']}")
        if account["status"] not in ACCOUNT_STATUSES:
            problems.append(f"{account['id']} status {account['status']}")
        if account["registration"] not in REGISTRATIONS:
            problems.append(f"{account['id']} registration {account['registration']}")
        if not re.fullmatch("xxxx-[0-9]{4}", account["masked_number"]):
            problems.append(f"{account['id']} masked number {account['masked_number']}")
        if account["hard_case"] != HARD_CASES.get(account["id"], ""):
            problems.append(f"{account['id']} hard case label drift")

    for account in book["accounts"]:
        count = account.get("signature_count")
        if isinstance(count, bool) or not isinstance(count, int):
            problems.append(f"{account['id']} signature_count is {count!r}")
            continue
        low, high = signature_range(account)
        if not low <= count <= high:
            problems.append(f"{account['id']} signature_count {count} outside {low} to {high}")

    positions_by_account = {}
    for position in book["positions"]:
        if position["account_id"] not in account_by_id:
            problems.append(f"{position['id']} references missing account")
        positions_by_account.setdefault(position["account_id"], []).append(position)
        if position["value"] < 0 or position["cost_basis"] < 0:
            problems.append(f"{position['id']} has a negative value or cost basis")

    for account in ACCOUNTS:
        count = len(positions_by_account.get(account["id"], []))
        if ACCOUNT_TARGETS[account["id"]] != "0.00":
            if not 3 <= count <= 7:
                problems.append(f"{account['id']} has {count} positions")
        elif count != 0:
            problems.append(f"{account['id']} is unfunded but has {count} positions")

    household_total = 0
    for household in book["households"]:
        account_ids = [a["id"] for a in ACCOUNTS if a["household_id"] == household["id"]]
        computed = sum(
            (Decimal(str(p["value"])) for aid in account_ids for p in positions_by_account.get(aid, [])),
            Decimal("0.00"),
        )
        if Decimal(str(household["aum"])) != computed:
            problems.append(f"{household['id']} AUM does not equal its positions")
        household_total += household["aum"]

    consent_values = {"received", "pending", "not required"}
    consent_status = {}
    for household in book["households"]:
        status = household.get("consent_status")
        if status not in consent_values:
            problems.append(f"{household['id']} consent_status is {status!r}")
            continue
        consent_status[household["id"]] = status

    pending_ids = [hid for hid, status in consent_status.items() if status == "pending"]
    not_required_ids = [
        hid for hid, status in consent_status.items() if status == "not required"
    ]
    annuity_households = {
        account["household_id"]
        for account in ACCOUNTS
        if account["account_type"] == "Variable annuity"
    }
    if len(pending_ids) != 3:
        problems.append(f"pending consent households are {pending_ids}")
    if "H-1043" not in pending_ids:
        problems.append("H-1043 consent_status is not pending")
    if not annuity_households <= set(pending_ids):
        problems.append(f"annuity households {sorted(annuity_households)} are not pending")
    if len(not_required_ids) != 1:
        problems.append(f"not-required consent households are {not_required_ids}")

    if household_total != BOOK["aum_total"]:
        problems.append(f"household AUM total is {household_total}")
    if book["book"]["aum_total"] != 42300000:
        problems.append("book AUM total is not 42,300,000")

    for document in book["documents"]:
        if document["id"] not in document_by_id:
            problems.append(f"{document['id']} has no document payload")
        if document_by_id[document["id"]]["kind"] != document["kind"]:
            problems.append(f"{document['id']} kind mismatch")

    for document in document_payloads:
        body = document["body_text"]
        if "$" in body:
            problems.append(f"{document['id']} contains a dollar sign")
        if re.search("[0-9]{3}-[0-9]{2}-[0-9]{4}", body):
            problems.append(f"{document['id']} contains an SSN-shaped string")
        if re.search("[0-9]{2}-[0-9]{7}", body):
            problems.append(f"{document['id']} contains an EIN-shaped string")
        for email in re.findall("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", body):
            if not email.endswith("@example.com"):
                problems.append(f"{document['id']} has a non-example email {email}")

    flags = 0
    for extraction in book["extractions"]:
        doc = document_by_id.get(extraction["document_id"])
        if doc is None:
            problems.append(f"{extraction['id']} references missing document")
            continue
        if extraction["source_span"] not in doc["body_text"]:
            problems.append(
                f"{extraction['id']} span not in {extraction['document_id']}: {extraction['source_span']}"
            )
        if not 0 < extraction["confidence"] <= 1:
            problems.append(f"{extraction['id']} confidence out of range")
        if extraction["field_name"].startswith("FLAG:"):
            flags += 1

    if len(book["extractions"]) != 59:
        problems.append(f"extraction count is {len(book['extractions'])}")
    if flags != 6:
        problems.append(f"flag count is {flags}")

    for task in book["tasks"]:
        if task["account_id"] not in account_by_id:
            problems.append(f"{task['id']} references missing account")
        if task["phase"] not in TASK_PHASES:
            problems.append(f"{task['id']} phase {task['phase']}")
        if task["status"] not in TASK_STATUSES:
            problems.append(f"{task['id']} status {task['status']}")

    covered_accounts = {task["account_id"] for task in book["tasks"]}
    if covered_accounts != set(account_by_id):
        problems.append("not every account has a task")
    if len(book["tasks"]) != 81:
        problems.append(f"task count is {len(book['tasks'])}")

    for risk in book["risks"]:
        if risk["account_id"] not in account_by_id:
            problems.append(f"{risk['id']} references missing account")
    if len(book["risks"]) != 9:
        problems.append(f"risk count is {len(book['risks'])}")

    if len(labeled) != 52:
        problems.append(f"labeled fields {len(labeled)}")

    labeled_by_key = {}
    for row in labeled:
        key = (row["document_id"], row["field_name"])
        if key in labeled_by_key:
            problems.append(f"duplicate labeled field {key}")
        labeled_by_key[key] = row
        if row["document_id"] not in {"D-1", "D-2", "D-3", "D-5", "D-6"}:
            problems.append(f"labeled row in unexpected document {row['document_id']}")
        doc = document_by_id[row["document_id"]]
        if row["source_span"] not in doc["body_text"]:
            problems.append(f"labeled span not in {row['document_id']}: {row['source_span']}")

    extraction_index = {
        (e["document_id"], e["field_name"]): e for e in book["extractions"]
    }
    for key, row in labeled_by_key.items():
        extraction = extraction_index.get(key)
        if extraction is None:
            problems.append(f"labeled field {key} has no extraction record")
            continue
        if extraction["value"] != row["expected"]:
            problems.append(f"labeled value drift for {key}")
        if extraction["source_span"] != row["source_span"]:
            problems.append(f"labeled span drift for {key}")

    risk_by_id = {risk["id"]: risk for risk in book["risks"]}
    for (doc_id, field_name), risk_id in FLAG_TO_RISK.items():
        if (doc_id, field_name) not in extraction_index:
            problems.append(f"missing flag extraction {doc_id} {field_name}")
        risk = risk_by_id.get(risk_id)
        if risk is None:
            problems.append(f"missing flag risk {risk_id}")
            continue
        if risk["account_id"] != DOC_ACCOUNT[doc_id]:
            problems.append(f"{risk_id} does not point at {DOC_ACCOUNT[doc_id]}")

    if problems:
        raise AssertionError("VALIDATION FAILED" + NL + NL.join(problems))


def main() -> None:
    ledger, positions = build_positions()
    households = build_households(ledger)
    accounts = build_accounts()
    document_map = build_documents(ledger)

    documents = []
    document_payloads = []
    extractions = []
    for meta in DOCUMENTS:
        body, fields = document_map[meta["id"]]
        documents.append(meta)
        document_payloads.append(
            {
                "id": meta["id"],
                "title": {
                    "D-1": "Alder Clearing statement, October 2026",
                    "D-2": "Alder Clearing statement, September 2026",
                    "D-3": "Morrow Trust statement, October 2026",
                    "D-4": "CRM contact export, 2026-11-08",
                    "D-5": "Calloway Family Irrevocable Trust summary",
                    "D-6": "Halden Life variable annuity contract summary",
                }[meta["id"]],
                "kind": meta["kind"],
                "body_text": body,
            }
        )
        for field_name, value, confidence, source_span in fields:
            extractions.append(
                {
                    "id": f"E-{len(extractions) + 1}",
                    "document_id": meta["id"],
                    "field_name": field_name,
                    "value": value,
                    "confidence": confidence,
                    "source_span": source_span,
                }
            )

    tasks = build_tasks()
    risks = [dict(risk) for risk in RISKS]

    labeled = [
        {
            "document_id": extraction["document_id"],
            "field_name": extraction["field_name"],
            "expected": extraction["value"],
            "source_span": extraction["source_span"],
        }
        for extraction in extractions
        if extraction["document_id"] != "D-4"
    ]

    book = {
        "book": dict(BOOK),
        "households": households,
        "accounts": accounts,
        "positions": positions,
        "documents": documents,
        "extractions": extractions,
        "tasks": tasks,
        "risks": risks,
    }

    validate(book, document_payloads, labeled)

    write_json(DATA_DIR / "book.json", book)
    for document in document_payloads:
        write_json(DOCUMENTS_DIR / f"{document['id']}.json", document)
    write_json(EVAL_DIR / "labeled.json", labeled)

    scenario = build_scenario(book, households, accounts)
    (DATA_DIR / "SCENARIO.md").write_text(scenario, encoding="utf-8")

    print(f"book.json: {len(households)} households, {len(ACCOUNTS)} accounts, {len(positions)} positions")
    print(f"documents: {len(documents)} files, {len(extractions)} extraction records")
    print(f"tasks: {len(tasks)}, risks: {len(risks)}, labeled fields: {len(labeled)}")
    print(f"hard cases: {len(HARD_CASES)}, flags: {sum(1 for e in extractions if e['field_name'].startswith('FLAG:'))}")
    print(
        "protocol: "
        f"{len(PROTOCOL['permitted_fields'])} permitted fields, "
        f"{sum(1 for h in households if h['consent_status'] == 'pending')} households pending consent"
    )
    print(f"signature counts: {sum(a['signature_count'] for a in accounts)} total")
    print("validation: all checks passed")


if __name__ == "__main__":
    main()
