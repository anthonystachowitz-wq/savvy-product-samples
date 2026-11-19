# Five product samples for one operating RIA
### A work sample for the Senior Product Manager role at Savvy, November 2026
*Anthony Stachowitz · anthony.stachowitz@gmail.com · linkedin.com/in/anthonystachowitz*
*I don't work at Savvy. Everything here is built from public information and synthetic data. Happy to take it down on request.*

## The short version

Savvy is three businesses now: an adviser platform, a custodial platform built on Fidelity's rails, and a back-office AI line sold to other wealth firms. I built a working product suite in that shape. Five surfaces, one synthetic client record, one approval and audit layer, and one set of evaluations with the failures shown.

Each surface maps to one lever Savvy's own operating model pulls: recruit advisers, grow their books, serve their clients, keep marketing compliant, and scale operations without adding headcount per dollar. The point is not the five pages. It is that I can find a wedge from public signals, build a working slice with a real model in the loop, measure it honestly, and put the controls in before the demo.

## What the public record suggested

Savvy's job board reads like a roadmap. Six open roles across adviser onboarding, transition, and adviser experience, with published KPIs for time to assets transitioned, signed-not-started rate, onboarding CSAT, and enablement completion. A transition specialist owns root-cause reviews on NIGOs. That is a team pointing at manual work.

The new enterprise line, from the product marketing posting: "We've spent the last few years building AI to run these workflows inside our own firm. Now we're bringing that product to market for enterprise RIAs and wealth firms." It deploys into each customer's existing systems and is measured by "the operating hours we return and the improvement in EBITDA margin," with forward-deployed engineers and design partners.

And the custodial platform, launched 23 September 2026, opened Savvy's rails to RIAs that are not joining as advisers. Savvy is not the custodian. Savvy Wealth Management is the introducing broker-dealer and Fidelity's National Financial Services holds the accounts.

Read together: Savvy is moving from one revenue line to three, and it is already paying for the operational work the samples address.

## What I built

A working suite, not a deck. One synthetic book of 12 households and 18 accounts, generated deterministically. Every surface reads the same record and writes to the same audit trail. All model work runs on DeepSeek, mostly DeepSeek Flash, through a recorded-run cache: every output shown was produced by a real call, saved with its model, latency, and token usage, and replayed by default so a reviewer never sees a spinner fail.

| Sample | Lever | What it does | AI pattern |
|---|---|---|---|
| Transition Copilot | Recruit | A signed book becomes a structured record, a signature and NIGO plan, and an approval file | Document extraction and workflow orchestration |
| Client Concierge | Serve | Answers a client's own questions from their record, cites every number, routes advice to the adviser | Retrieval and grounded generation |
| Book IQ | Grow | Ranks the next dollar in every book with evidence and drafts the adviser outreach | Reasoning and ranking over the book |
| Supervision in the loop | Comply | Drafts adviser marketing inside a pre-approval workflow with an evidence file per piece | Generation plus claim classification |
| LP Ops | Scale | Takes private markets paperwork from intake to capital call, with exceptions flagged | Document extraction and exception detection |

The suite is 54 recorded model calls, about 99,000 tokens, with a mean latency around two seconds. The model is not the product. The workflow, the data underneath it, and the controls are.

The build method is part of the sample. It was built over two days. AI coding agents wrote most of the code. I made the product decisions, wrote or directed every prompt, defined the guardrails and the evaluations, reviewed every module, and ran the QA. That division of labour is what I would bring to the role: high leverage, with a person accountable for what ships.

## The evaluations, including what failed

Small evaluations, in-sample, and shown with their failures rather than a perfect score.

| Area | What it measures | Result |
|---|---|---|
| Transition | Field extraction against 51 labelled fields | 51 of 51 in-sample, a build check, not a claim about real statements |
| Concierge | Advice boundary, citations, and routing across 10 labelled questions | 90 percent |
| Book IQ | Top play and required signal across 12 labelled households | 12 of 12 in-sample |
| Supervision | Claim-type classification across 12 labelled claims | 75 percent exact, 100 percent precision, 77.8 percent recall |
| LP Ops | Field extraction and exception detection across 5 packets | 96.9 percent |

Three failures are worth reading because they are the real story. On an advice question, the client assistant refused correctly but mentioned an Nvidia holding the household did not own; the number check cannot see names, so a named-entity check is the next control. The supervision classifier typed two index-return sentences as factual rather than performance, so the performance flag missed them; the identical sentence was caught inside a draft, which says the label is context sensitive. The LP extractor called two medium exceptions high severity. Each is on its page, next to the fix I would make.

## The controls that matter in an RIA

Every client-facing output waits for a named human approval and produces an evidence record: approver, role, timestamp, version, audience, channel, disclosures, retention date, and a hash. Every number in a draft points at a seeded source field or the draft is flagged. Advice language is blocked, and anything that looks like a recommendation routes to the adviser. A client-facing draft for a household without Broker Protocol consent is blocked on the server, not only in the button. Nothing is sent, filed, traded, or delivered.

That is the part I would want a hiring manager to judge. The model is the easy part to demo.

## The business case, modelled

| Sample | Modelled impact | How it is calculated |
|---|---|---|
| Transition Copilot | 109 to 152 hours returned per 100-client transition | 500 accounts at 45 minutes plus 60 planning hours equals 435 hours, times 25 to 35 percent |
| Client Concierge | 3.3 adviser hours a month | 30 routine questions, 60 percent answerable, 11 adviser minutes each |
| Book IQ | USD 4.48 million of net new assets across the book | Sum of 12 recommended plays at stated consolidation and cash assumptions |
| Supervision | Review cycle 3.31 to 1.87 days per piece | Pickup, first pass, rework rate, sign-off, before and after |
| LP Ops | 5.25 hours per LP per year | 15 hours of baseline work times a 35 percent reduction |

Every number is modelled arithmetic with the assumptions visible on the page. None is a measurement, and none is presented as one.

## What I would do in the first 30 days

1. Shadow three live transitions and time every step from signature to funded. Replace the model with the real distribution.
2. Pull the operational data: NIGO reasons, first-pass yield, stalled accounts, signed-not-started, and the hours behind each.
3. Sit with the transition and service teams, then with five recently moved advisers, and find the moment they nearly lost confidence.
4. Pick the one workflow with a measurable number and ship an internal improvement before proposing anything big.
5. For the enterprise line, test whether the same workflow sells to a design partner, and whether that conflicts with the affiliation pitch. That is the strategic question I would want to work through with the team.
6. Kill or reorder anything the data contradicts.

## How I would measure it

Instrument each account as a state machine with timestamps: signed, application submitted, custodian accepted, transfer initiated, validated, settled, funded, model invested, billing live. From those, compute time to first funded account, days to 50, 80, and 90 percent of transferable assets, first-pass yield, NIGO rate by custodian and reason, signatures outstanding, stalled accounts, and specialist capacity. For the enterprise line, hours returned per firm and the buyer's EBITDA margin. For the client surface, answers released without approval, which should stay at zero.

## Risks and what guards them

Custodian dependency. The custodian is the system of record and the plan can only orchestrate it. Phase one tracks and prepares, it does not pretend to integrate.

Model error. Confidence per field, source citations, labelled evaluations, and human review before anything leaves the queue. The failures above show why this matters.

The advice line. Client drafts explain; they do not recommend. Advice-seeking language routes to the adviser, and the check is visible rather than a footer disclaimer.

Protocol consent. No client-facing draft for a household without consent. A hard block in the workflow.

Records. Communications about a client's accounts are firm records. The vendor keeps nothing for itself, and the firm keeps and exports what it needs for five years.

Trust with the operators. Specialists are the users. A tool that feels like surveillance fails. The product should remove the chase, not add a manager's dashboard.

## The competitive reality

The space is crowded and worth being honest about. Farther is the closest model match and better capitalised, with USD 23 billion in recruited assets. Altruist, now being acquired by Vanguard, owns the account data and gives software away with custody. LPL has 32,000 advisers. On the enterprise side, Nevis has Schwab's backing, Avantos has Vanguard, Guardian, and SEI, and the systems of record are shipping AI at no extra cost.

Savvy's advantage is not features. It is that Savvy is the customer, can prove hours and margin from its own profit and loss, and offers advisers equity plus their own brand. The samples are built around that advantage: every one is an operating workflow with a measure attached, not a feature demo.

## What I deliberately did not build

No custodian or CRM integration, no delivery or filing, no advice, no real client data, and no pricing page. This is an outside-in argument built to start a conversation, not a plan. The two-page version of that argument is in the brief, and the code is in the repository.

Demo: https://savvy-suite.3-236-129-20.sslip.io. Code: https://github.com/anthonystachowitz-wq/savvy-product-samples.

This is a work sample, not a proposal for Savvy's roadmap. Savvy may already have some of this internally. If so, the useful part is the method: what I noticed, what I chose, how fast it came together, and how the controls are built.
