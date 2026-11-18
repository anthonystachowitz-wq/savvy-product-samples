# Architecture

A small FastAPI application around a deterministic pipeline and a seeded
synthetic book. The goal is to make the workflow legible, not to prototype a
production system.

## Runtime shape

```
browser
  └── FastAPI (app/main.py, Jinja templates)
        ├── app/store.py        SQLite state: draft status, consent, audit events
        ├── app/pipeline/       extract, plan, draft, guardrails, value, eval
        │     └── app/llm.py    provider interface: cached or Bedrock
        └── data/               seeded JSON: book, documents, labels, cache
```

No outbound network calls in the default mode. The app reads the seeded files,
writes a local SQLite file, and serves HTML.

## Data flow

1. **Seed.** `scripts/generate_book.py` writes `data/book.json`,
   `data/documents/*.json`, and `data/eval/labeled.json`. The generator asserts
   that every extraction and label span is an exact substring of its document,
   that household and book totals reconcile, and that no real identifiers appear.
2. **Extract.** `pipeline.extract.extract_document` parses a document into
   fields with confidence and source spans, then runs a rules pass for the known
   hard findings. A cached entry is used only when its recorded mode says a real
   model produced it.
3. **Plan.** `pipeline.plan.build_plan` turns the book into per-account tasks,
   a critical path, and NIGO risks with reasons and remedies. Signature counts
   drive signature-chasing work.
4. **Draft.** `pipeline.draft.draft` writes client and custodian communications
   with a claim for every number and a disclosure block. `pipeline.guardrails`
   checks advice language, promised timing, performance claims, testimonials,
   uncited numbers, disclosures, and Broker Protocol consent.
5. **Review.** The UI holds client-facing drafts until a human approves them.
   The approval writes an evidence record and an audit event to SQLite.
6. **Value.** `pipeline.value.value_model` computes the hours model from stated
   assumptions and returns every formula as a string so a reader can check it.
7. **Eval.** `pipeline.eval.run_eval` compares extraction output to the labelled
   set and writes `data/eval/report.json`. The Value screen reads that report.

## Guardrails

- **Claim sourcing.** Every numeric or status claim in a draft points at a seeded
  source. An uncited number fails a check.
- **Advice line.** Client drafts are explain-only. Recommendation, transaction,
  and guarantee language fails `advice_language`. Promised timing that a
  custodian controls fails `promised_timing`.
- **Protocol consent.** A client-facing draft for a household without consent is
  blocked, citing the five Broker Protocol data elements and the
  no-contact-before-resignation restriction.
- **Human approval.** Nothing client-facing is marked sent without an approver
  role and timestamp. The evidence file records who, when, version, audience,
  channel, disclosures, and retention.
- **Firm-held records.** The audit trail and evidence live with the firm. The
  zero-retention vendor pattern is called out as needing a firm export carve-out.

## What is deliberately simple

- State is local SQLite, seeded from JSON. A restart with `--reload` keeps the
  database; there is a reset action that clears approvals and audit events.
- The pipeline is deterministic so the demo cannot fail mid-click. The provider
  interface accepts a recorded model run, and the code path for a live model
  exists but is off by default.
- There is no authentication. The demo is unlisted and sets `noindex`; it is not
  private, and it must not hold real data.
- Custodian connections, filing, e-signature, and account opening are out of
  scope on purpose.

## Extension points

- A real extraction model behind `app/llm.py`, with the eval harness pointed at a
  held-out labelled set instead of the in-sample one.
- Custodian adapters for status and document retrieval, behind the plan's task
  status model.
- An evidence store with immutable versioning and export, replacing local
  SQLite.
