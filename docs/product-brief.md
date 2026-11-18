# Transition Copilot
### A product sample for Savvy, November 2026
*Anthony Stachowitz · anthony.stachowitz@gmail.com · linkedin.com/in/anthonystachowitz*
*I don't work at Savvy. Everything here is built from public information and synthetic data. Happy to take it down on request.*

## The short version

Savvy has six open roles across advisor onboarding, transition, and advisor experience. That hiring plan is the clearest public statement of where the constraint is. When a signed advisor's book takes months to get live, every week is revenue not yet on platform and a specialist's hours not yet spent on the next transition.

I built a working slice of the workflow that fixes that, with the policy checks and audit trail a registered investment adviser would need. This page explains why I picked this bet, what the demo actually does, how I would measure it, and what I would need to learn in the first 30 days to prove myself wrong.

## What I read before building

Your job board, read as a roadmap: six roles in onboarding, transition, and advisor experience. The postings publish the KPIs for time to assets transitioned, signed-not-started rate, onboarding CSAT, and enablement completion, and the transition specialist owns root-cause reviews on NIGOs ([job board](https://jobs.ashbyhq.com/savvy)).

A new enterprise line, from the product marketing posting: "We've spent the last few years building AI to run these workflows inside our own firm. Now we're bringing that product to market for enterprise RIAs and wealth firms." It is sold as an outcome, in operating hours returned and EBITDA margin.

The operational reality from public sources: a clean ACATS transfer runs three to four business days, and the carrying firm has three days to validate or take exception ([DTCC](https://www.dtcc.com/insights/2025/acats-transformation-is-underway), [FINRA](https://www.finra.org/rules-guidance/key-topics/customer-account-transfers)). The bottleneck is everything before the transfer: a seven-account household can need 20 signatures, and a 100-client book can approach 1,500 ([WealthManagement](https://www.wealthmanagement.com/business-planning/for-transitioning-advisors-repapering-is-a-daunting-task)). Under the Broker Protocol an advisor may take exactly five data elements, and no client contact is allowed before resignation, Protocol or not ([Comply](https://www.comply.com/resource/understanding-the-broker-protocol-in-2025-what-individual-advisers-and-rias-need-to-know/)).

The gap everybody shares, per trade press: there is no cross-custodian data model, so every transition reconciles the same household again ([RIABiz, November 2026](https://riabiz.com/a/2026/11/2/schwab-rias-have-until-monday-to-convert-exclusively-to-docusign-ending-paper-era-as-it-deputizes-nevis-to-help-rias-conquer-their-onboarding-quagmire-and-prove-ai-can-fix-the-most-painful-workflow-at-any-ria)).

## Why this bet

Transition sits at the intersection of Savvy's growth engine and its new product line. Internally, faster transitions mean revenue starts sooner, specialists carry more books, and the signed-not-started rate goes down. Externally, every growing RIA has this workflow, and the buyers already spend on it. The same product can be used inside the firm first and then sold, which is the cheapest way to find out if it works.

It also produces the asset Savvy already treats as the moat. The complete household record has to start somewhere. Today it starts in a spreadsheet during transition. Making transition the first place the record gets built means every new book lands structured, reconciled, and auditable instead of being cleaned up months later.

The competitive picture is real. Docupace runs transitions with in-house specialists, Skience and Advisor360 sell onboarding and account opening, Dispatch and Jump are shipping AI-assisted transitions now, Nevis is part-owned by Schwab and goes live for Schwab RIAs in Q1 2027, and Avantos raised a Series A with Vanguard, Guardian, and SEI behind it. Savvy's edge is not features. It is that Savvy is the customer, can price against its own cost baseline, and can prove hours returned inside its own RIA before asking another firm to believe it.

## What I built

A working prototype, not a deck. One seeded book: 12 households, 18 accounts, eight hard cases including an irrevocable trust, an inherited IRA, a variable annuity, a held-away 401(k), an LP interest, and a margin account with options. Fictional custodians. The workflow runs in four steps.

**Intake.** Synthetic statements and a CRM export become a structured household and account record, with confidence per field, missing-data flags, and a hard-case list. It also shows what the workflow is allowed to know: the five Broker Protocol fields, and which households have not consented yet.

**Plan.** Per-account form packets, signature counts, owners, and due dates, sequenced against the real clocks. ACATS is a short step at the end. Most of the plan is chasing signatures. NIGO risks carry the reason and what clears them.

**Review.** Client updates, custodian cover letters, and internal digests sit in a queue. Each draft carries its claims with sources, a policy check against advice language, promised timing, and protocol consent, and an evidence file with approver, timestamp, audience, and retention. Nothing sends without a human. Every action writes to an audit trail.

**Value.** The hours model, with the arithmetic visible. A 100-client book at five accounts per household is about 500 accounts. At 45 minutes of ops time each, that is 375 hours, plus 60 hours of planning and reconciliation, so 435 hours per transition. A 25 to 35 percent reduction is 109 to 152 hours, or 14 to 19 working days. Forty transitions a year is 4,360 to 6,080 hours. These are modelled estimates with the assumptions shown, not measurements.

What is real: the pipeline code, the policy checks, the evidence and audit model, the eval harness, and the screens. What is simulated: the statements, the extracted values (the demo runs a deterministic parser and template writer so a click never fails; the code carries a model-provider interface for live runs), and any custodian integration. The demo states this on every page. The eval report scores extraction against a labelled set: 100 percent on 51 fields, measured in-sample on synthetic statements, which is a build check rather than a claim about real documents.

Demo: [DEMO_URL]. Code: [REPO_URL].

## How I would measure it

Instrument each account as a state machine with timestamps: signed, application submitted, custodian accepted, transfer initiated, validated, settled, funded, model invested, billing live. Everything else follows from those timestamps.

Leading: time from signature to first submitted application, first-pass yield, NIGO rate by custodian and reason, signatures outstanding per household, stalled accounts over 14 days, touches per account. Lagging: days to first funded account, days to 50, 80, and 90 percent of transferable assets, signed-not-started rate, onboarding CSAT at 30 and 60 days, specialist books in flight.

The business case is hours returned per transition. The published benchmarks are thin and I would not claim otherwise. Docupace's 68 percent of accounts in 30 days and 93 percent in 60 is vendor data ([source](https://markets.businessinsider.com/news/stocks/docupace-advisor-transitions-program-celebrates-500-000th-client-account-served-1031554591)). No public NIGO rate or days-to-first-funded series exists. Your transition specialist posting has that role running root-cause reviews on NIGOs, which means the data has to exist before it can be improved. I would rather build the instrumentation than borrow a number.

## Risks and what guards them

Custodian dependency. The receiving custodian is the system of record and the plan can only orchestrate it. Phase one tracks and prepares, it does not pretend to integrate.

Extraction accuracy. Confidence per field, a labelled eval set, and human review before anything leaves the queue. A wrong registration or beneficiary is worse than a slow one.

The advice line. Client drafts explain status, next steps, and documents required. They do not recommend transactions. Advice-seeking language routes to the adviser, and the check is visible in the UI rather than a footer.

Protocol consent. No client-facing draft for a household without consent. That is a hard block in the workflow, not a warning.

Records. Communications about a client's accounts are firm records. The vendor keeps nothing for itself. The firm keeps and exports everything it needs for five years, and the demo shows the retention field.

Trust with the transition team. Specialists are the users, and a tool that feels like surveillance fails. The product should remove the signature chase, not add a manager's dashboard.

## What I would do in the first 30 days

1. Shadow three live transitions, stopwatch in hand, and time every step from signature to funded. Replace my model with the real distribution.
2. Sit with the transition leads and pull NIGO reasons, first-pass yield, and stalled-account data for the last two quarters.
3. Interview five recently transitioned advisors and five in flight. Find the moment they nearly lost confidence.
4. Ask Growth for signed-not-started data and the reasons behind it. That number is the cost of a slow transition.
5. Run one design partner through the prototype if the enterprise motion is live. If not, run it internally against a completed transition as a backtest.
6. Kill or reorder anything the data contradicts. One module that lands beats five that look good on a page.

## What I deliberately did not build

No custodian integrations, no direct client access, no advice, no pricing page, no fabricated benchmarks, and no claims about Savvy's internals. This is an outside-in argument, built to start a conversation, not a plan.

## Where this goes if it works

Internal first: fewer hours per transition, more books per specialist, faster time to revenue. Then the enterprise module: sell the same workflow into firms that do not have Savvy's operations team, price against operating hours returned, and use Savvy's own RIA as the reference deployment. The proof is the same either way, and Savvy is one of the few companies that can generate it from its own P&L.

If any of this is wrong, I would like to hear which part.
