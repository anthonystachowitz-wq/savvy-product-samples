# Five product samples for an AI-native RIA

A working product suite built as a work sample for the Senior Product Manager role at Savvy Wealth. Five surfaces, one synthetic client record, one approval and audit layer, and one honest evaluation story.

**Unofficial concept.** I do not work at Savvy and this is not affiliated with Savvy. All client, adviser, custodian, LP, and account data is synthetic. Nothing here is investment advice. Built from public information only. Happy to take it down on request.

- Anthony Stachowitz
- anthony.stachowitz@gmail.com
- linkedin.com/in/anthonystachowitz

## Why these five

Savvy's own public hiring and product signals point at five levers: recruit advisers, grow their books, serve their clients, keep marketing compliant, and scale operations without adding headcount per dollar. Each sample maps to one lever, and each one uses a different AI pattern on the same client record.

| Sample | Lever | AI pattern | Route |
|---|---|---|---|
| Transition Copilot | Recruit | Document extraction and workflow orchestration | /transition |
| Client Concierge | Serve | Retrieval over the household record and grounded generation | /concierge |
| Book IQ | Grow | Reasoning and ranking over the book, plus outreach drafting | /bookiq |
| Supervision in the loop | Comply | Generation plus claim classification and policy checks | /supervision |
| LP Ops | Scale | Document extraction and exception detection | /lpo |

The hub at / is the starting point. /method is generated from the modules: every recorded run, every evaluation, and every failure is read from the data files.

## Real model work

All model calls run on DeepSeek through its API, mostly DeepSeek Flash. Every output shown in the suite came from a real call. The recordings live in `data/cached/llm/` with the model, latency, token usage, and prompt hash. The app replays them by default so a reviewer never sees a spinner fail, and a deployment can make fresh calls inside a small daily budget.

The evaluations are small and in-sample. They are shown with their failures on the module pages and on /method. Examples: the client assistant invented a holding in one refusal, the supervision classifier missed two performance claims that were framed as factual returns, and the LP extractor was too severe on two exceptions. Those are the interesting parts.

## What is real and what is simulated

| Real | Simulated |
|---|---|
| The application code, the prompts, the recorded model calls, the evaluations, the approval and audit logic | The synthetic book, the client records, the LP packets, the marketing briefs, the fact sheet, and the people |
| The deterministic data generators and the guardrail rules | Any custodian, CRM, private markets administrator, e-signature, wire, or delivery integration. None exists |
| The modelled business arithmetic, with its assumptions on the page | The market context and the company's internal data. Nothing internal is used |

## Quick start

Requires Python 3.10 or newer.

```bash
python3 -m pip install -r requirements.txt
make run            # or: python3 -m uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000. The app seeds a local `app.db` on first request and reads the recorded model outputs from `data/cached`. No API key is needed to run the demo.

### Optional live model mode

```bash
export DEEPSEEK_API_KEY=...
export SAVVY_LLM_MODE=live
export SAVVY_LLM_DAILY_LIMIT=200
```

Live mode makes a real call only when a recording is missing or a page asks for a refresh, and every call counts against the daily budget.

### Rebuild the data and recordings

```bash
python3 scripts/generate_book.py            # the shared synthetic book
python3 scripts/generate_concierge.py       # each module's deterministic data
python3 scripts/record_concierge.py         # live calls into data/cached, once
python3 scripts/eval_concierge.py           # writes data/modules/concierge/eval.json
python3 -m pytest -q
```

Each module has its own generate, record, and eval scripts. See `docs/architecture.md`.

## Deploy

See [deploy/README.md](deploy/README.md). Two containers: the app and a Caddy proxy that terminates TLS and sets `noindex`. The DeepSeek key is passed at runtime through the environment and never baked into the image.

## Repository layout

```
app/
  main.py                 hub, method page, and the Transition sample
  platform/               DeepSeek client with recorded runs, audit, state, nav
  modules/                one package per sample, each with routes and logic
  templates/              suite layout, hub, method, Transition, and module templates
  static/                 CSS and a small progressive-enhancement script
data/
  book.json               the shared synthetic book
  documents/              synthetic statements and summaries for Transition
  modules/                per-module synthetic data, labels, and eval results
  cached/llm/             recorded live model calls, with model and usage metadata
scripts/                  deterministic generators, recorders, eval runners
tests/                    unit tests for the pipeline, recordings, and guards
deploy/                   Dockerfile, compose file, Caddy config
docs/                     the product brief and architecture notes
```

## Limitations

- No custodian, CRM, or private markets connection. Every step that would touch the outside world is a draft.
- The evaluations are small, in-sample, and written by the same person who wrote the prompts.
- The business metrics are modelled arithmetic with labelled assumptions, not measurements.
- The synthetic book is small and tidy. Real books are messier.
- There is no authentication. The demo is unlisted and sets `noindex`, and it must not hold real data.
