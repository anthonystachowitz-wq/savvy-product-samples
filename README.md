# Transition Copilot

A working product sample for a wealth-management platform: an AI-assisted adviser
transition workflow that takes a signed adviser's book from contract to fully
transferred assets.

**This is an unofficial concept.** I do not work at Savvy and this is not
affiliated with Savvy. All client, adviser, custodian, and account data in the
demo is synthetic. Nothing here is investment advice. Built from public
information only. Happy to take it down on request.

- Anthony Stachowitz
- anthony.stachowitz@gmail.com
- linkedin.com/in/anthonystachowitz

## Why this exists

Savvy's public job board shows six open roles across adviser onboarding,
transition, and adviser experience, plus a new enterprise line that sells
back-office AI by the operating hours it returns. Transition is the workflow
between those two. This sample is the first slice I would productize: a messy
book becomes a structured record, the record becomes a signature and
form-packet plan with NIGO risks, and every client communication sits behind a
human approval and an evidence file.

The argument is in the [product brief](docs/product-brief.md). This repository is
the proof of work.

## The demo

Six screens, one seeded book of 12 households and 18 accounts across two
fictional custodians:

| Screen | What it shows |
|---|---|
| Overview | The thesis, the three claims, and a guided tour |
| Intake | Synthetic statements and a CRM export become structured fields with confidence, flags, and the Broker Protocol permission card |
| Plan | Per-account form packets and signature counts, the critical path, and NIGO risks with reasons and remedies |
| Review | Draft client and custodian communications with source-cited claims, policy checks, an approval gate, an evidence file, and an audit trail |
| Value | The hours-returned model with every formula visible, plus the extraction eval |
| About | What is real, what is simulated, the compliance map, and limitations |

## What is real and what is simulated

| Real | Simulated |
|---|---|
| The pipeline code: extraction, planning, drafting, policy checks, value model, eval harness | The book, statements, CRM export, trust and annuity summaries, custodians, and people |
| The seeded questions and the rules asserted in tests | Any custodian connection or filing. None exists |
| The in-sample extraction eval and its failures/scores | The extracted values at demo time. The demo runs a deterministic parser and template writer so a click never fails |

The extraction eval scores 100 percent on 51 labelled fields **in-sample**, on
synthetic statements the parser was built against. It is a build check, not a
generalization claim, and the report says so.

The pipeline carries a provider interface for a real model. Cached outputs are
labelled `deterministic extractor` or `deterministic template` and never
`live model, recorded` unless a real model call produced them.

## Compliance notes

- Client-facing drafts explain status, next steps, and required documents. They
  do not recommend transactions, and a policy check blocks advice language.
- A household without Broker Protocol consent cannot receive a client-facing
  draft. That is a hard block in the workflow.
- Communications about a client's accounts are firm records. The demo keeps the
  evidence and the audit trail on the firm side and states the retention period.
- All data is synthetic. No real client data, no vendor credentials, no
  production screenshots.

## Quick start

Requires Python 3.10 or newer.

```bash
python3 -m pip install -r requirements.txt
make run            # or: python3 -m uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000. The app seeds a local `app.db` on first request
from the files in `data/`. `make reset` clears it; `make test` runs the suite;
`make eval` regenerates the extraction report. `/healthz` reports the engine
path and the seeded book.

### Rebuild the synthetic dataset

```bash
python3 scripts/generate_book.py
python3 scripts/warm_cache.py
```

The generator is deterministic. Two runs from any working directory produce
byte-identical files.

### Run the eval and the tests

```bash
python3 scripts/run_eval.py
python3 -m pytest -q
```

The eval writes `data/eval/report.json`, which the Value screen reads.

## Deploy

See [deploy/README.md](deploy/README.md). Two containers: the app and a Caddy
proxy that terminates TLS and sets `noindex`.

## Repository layout

```
app/            FastAPI app, templates, static assets, and the pipeline
data/           Deterministic synthetic book, documents, labels, and cache
scripts/        Dataset generator, cache warmer, eval runner
tests/          Guardrail, eval, value, and consent tests
deploy/         Dockerfile, compose file, Caddy config
docs/           Product brief and architecture notes
```

## Limitations

- No custodian integration. The product orchestrates a process; the custodian
  remains the system of record.
- The seeded book is small and synthetic. Real books are messier.
- The value model is arithmetic with labelled assumptions, not a measurement.
- The Broker Protocol mechanics are simplified to the five permitted data fields
  and the no-contact rule.
