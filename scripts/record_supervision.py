#!/usr/bin/env python3
"""Record every supervision model call with a live DeepSeek call.

Usage:
    SAVVY_LLM_MODE=live python3 scripts/record_supervision.py
    SAVVY_LLM_MODE=live python3 scripts/record_supervision.py --stage classify

Eleven calls in the full run: four drafts, four claim classifications, one
revision, one classification of the revision, and one classification of the
twelve eval claims. Each recording lands in
data/cached/llm/supervision/<key>.json with its model, latency, and token usage.
This script refuses to run in cached mode.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.platform import audit, config, llm  # noqa: E402
from app.modules.supervision import data, logic  # noqa: E402

MODULE = "supervision"
ROWS: list[dict[str, object]] = []


def _record(key: str, system: str, prompt: str, max_tokens: int, temperature: float) -> str:
    result = llm.run(
        MODULE,
        key,
        system,
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        refresh=True,
    )
    usage = result.usage or {}
    ROWS.append(
        {
            "key": key,
            "mode": result.mode,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "chars": len(result.text),
        }
    )
    print(
        "  {key:22s} {model:22s} {latency:>6d} ms  {p:>5d} + {c:>5d} tokens  {chars:>5d} chars".format(
            key=key,
            model=result.model,
            latency=result.latency_ms,
            p=int(usage.get("prompt_tokens") or 0),
            c=int(usage.get("completion_tokens") or 0),
            chars=len(result.text),
        )
    )
    return result.text


def _fail(message: str) -> int:
    print("FAILED: " + message, file=sys.stderr)
    return 1


def _classify_drafts(briefs: list[dict], fact_sheet: dict) -> int:
    for brief in briefs:
        draft = logic.load_draft(brief, 1)
        if not draft.get("ok"):
            return _fail(
                "draft {id} is not readable: {error}".format(id=brief["id"], error=draft.get("error"))
            )
        prompt = logic.build_classification_prompt(brief, draft, fact_sheet)
        _record(brief["claims_key"], logic.SYSTEM_CLASSIFY, prompt, 900, 0.0)
    return 0


def _classify_revision(brief: dict, fact_sheet: dict) -> int:
    revision_draft = logic.load_draft(brief, 2)
    if not revision_draft.get("ok"):
        return _fail(
            "revision for {id} is not readable: {error}".format(
                id=brief["id"], error=revision_draft.get("error")
            )
        )
    prompt = logic.build_classification_prompt(brief, revision_draft, fact_sheet)
    _record(brief["revision_claims_key"], logic.SYSTEM_CLASSIFY, prompt, 900, 0.0)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Record the supervision model calls.")
    parser.add_argument(
        "--stage",
        choices=("all", "classify"),
        default="all",
        help="all records every call; classify re-records the classifiers only.",
    )
    args = parser.parse_args()

    if config.llm_mode() != "live":
        print("SAVVY_LLM_MODE must be live to record. Nothing was written.", file=sys.stderr)
        return 2
    if not config.deepseek()["api_key"]:
        print("No DeepSeek key is configured. Nothing was written.", file=sys.stderr)
        return 2

    fact_sheet = data.fact_sheet()
    bundle = data.briefs_bundle()
    briefs = bundle["briefs"]
    revised = [brief for brief in briefs if brief.get("revision_key")]

    print("Recording supervision calls on " + str(config.deepseek()["model"]))
    print("Stage: " + args.stage)
    print()

    if args.stage == "all":
        print("Drafts")
        for brief in briefs:
            prompt = logic.build_generation_prompt(brief, fact_sheet)
            text = _record(brief["draft_key"], logic.SYSTEM_GENERATION, prompt, 700, 0.25)
            parsed, error = logic.parse_json(text)
            if not isinstance(parsed, dict) or not str(parsed.get("body") or "").strip():
                return _fail(
                    "draft {id} did not return usable JSON: {error}".format(id=brief["id"], error=error)
                )
        print()

        print("Claim classification, first pass")
        status = _classify_drafts(briefs, fact_sheet)
        if status:
            return status

        print()
        print("Revision for the blocked draft")
        for brief in revised:
            draft = logic.load_draft(brief, 1)
            claims = logic.load_claims(brief, fact_sheet, 1)
            prompt = logic.build_revision_prompt(brief, draft, claims["claims"], fact_sheet)
            text = _record(brief["revision_key"], logic.SYSTEM_REVISE, prompt, 700, 0.25)
            parsed, error = logic.parse_json(text)
            if not isinstance(parsed, dict) or not str(parsed.get("body") or "").strip():
                return _fail(
                    "revision for {id} did not return usable JSON: {error}".format(
                        id=brief["id"], error=error
                    )
                )
    else:
        print("Claim classification, first pass")
        status = _classify_drafts(briefs, fact_sheet)
        if status:
            return status

    print()
    print("Revision classification")
    for brief in revised:
        status = _classify_revision(brief, fact_sheet)
        if status:
            return status

    if args.stage == "all":
        print()
        print("Evaluation claims")
        cases = data.eval_cases()["cases"]
        prompt = logic.build_eval_prompt(cases, fact_sheet)
        _record(data.eval_cases().get("key", "eval-claims"), logic.SYSTEM_EVAL, prompt, 1400, 0.0)

    total_prompt = sum(int(row["prompt_tokens"]) for row in ROWS)
    total_completion = sum(int(row["completion_tokens"]) for row in ROWS)
    mean_latency = int(sum(int(row["latency_ms"]) for row in ROWS) / len(ROWS)) if ROWS else 0

    print()
    print("Recorded " + str(len(ROWS)) + " calls")
    print("Models: " + ", ".join(sorted({str(row["model"]) for row in ROWS})))
    print("Prompt tokens: " + "{:,}".format(total_prompt))
    print("Completion tokens: " + "{:,}".format(total_completion))
    print("Mean latency: " + "{:,}".format(mean_latency) + " ms")
    print()

    missing = []
    for row in ROWS:
        path = config.CACHE_DIR / MODULE / (str(row["key"]) + ".json")
        if not path.is_file():
            missing.append(str(row["key"]))
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("mode") != llm.MODE_LIVE:
            missing.append(str(row["key"]) + " (mode " + str(payload.get("mode")) + ")")
    if missing:
        return _fail("these recordings are not live: " + ", ".join(missing))

    audit.record(
        MODULE,
        "Recorder",
        "llm.recorded",
        "module",
        MODULE,
        "{n} live model calls recorded on {models}: {p:,} prompt and {c:,} completion tokens, mean latency {latency:,} ms.".format(
            n=len(ROWS),
            models=", ".join(sorted({str(row["model"]) for row in ROWS})),
            p=total_prompt,
            c=total_completion,
            latency=mean_latency,
        ),
    )
    print("Every recording says: " + llm.MODE_LIVE)
    print("Audit event written: llm.recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
