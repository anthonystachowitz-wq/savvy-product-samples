# Scenario

Synthetic data for the Transition Copilot work sample. Every name, custodian, account number, and address is invented. Account numbers are masked and no tax identifiers are present.

Dana Whitfield leaves Meridian Private Wealth for the receiving RIA. The book signs on 2026-11-03 with a target live date of 2026-12-18. Priya Raman is the transition lead. Total AUM is USD 42,300,000 across 12 households and 18 accounts.

Accounts sit at two fictional custodians: Alder Clearing and Morrow Trust. Twelve accounts are in wave 1 and six are in wave 2.

Hard cases, one account each:
- Irrevocable trust, A-2203
- Inherited IRA, A-2204
- Proprietary mutual fund share class, A-2205
- Variable annuity with a 1035 question, A-2206
- Held-away 401k, A-2209
- UTMA, A-2210
- Margin account with options, A-2212
- LP alternative interest, A-2213

Documents: three statements (two Alder Clearing, one Morrow Trust), one CRM contact export, one trust document summary, one annuity contract summary. Six hard findings are seeded: beneficiary missing, address stale, registration mismatch, non-transferable share class, annuity surrender charge, trust EIN absent.

Protocol and consent: the Broker Protocol packet arrived on 2026-11-03 with five permitted data elements (client name, address, phone, email, and account title) and three restrictions: no client contact before resignation, protocol data used only for transition preparation until client consent, and no solicitation. Consent status is received for 8 households, pending for H-1043, H-1044, and H-1045, and not required for H-1053, the household opening a new account with new money in wave 2. Client-facing drafts for a pending household stay blocked until consent is received. Each account also carries a signature_count estimate between 2 and 6, assigned by account type.

Value model: about 45 minutes of manual ops time per account. A reference book of 100 clients at 5 accounts each is 500 accounts, so 375 hours, plus 60 hours of planning and reconciliation, equals 435 hours per transition. A 25 to 35 percent reduction is 109 to 152 hours, or 14 to 19 working days, and 40 transitions a year is 4,360 to 6,080 hours. The target is 45 days from signature to a funded account. These are modelled estimates, not measurements.

Regenerate the dataset with python3 scripts/generate_book.py. Output is deterministic.
