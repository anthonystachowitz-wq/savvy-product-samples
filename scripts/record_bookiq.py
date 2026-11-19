#!/usr/bin/env python3
"""Record every Book IQ model call against the live DeepSeek API.

    SAVVY_LLM_MODE=live python3 scripts/record_bookiq.py
    SAVVY_LLM_MODE=live python3 scripts/record_bookiq.py --refresh

Without --refresh an existing recording is reused. The app then replays those
files with SAVVY_LLM_MODE=cached. Recordings are the only model output the demo
serves.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.bookiq import data, logic
from app.platform import config, llm


def _usage_totals(results: list[Any]) -> dict[str, int]:
    prompt = 0
    completion = 0
    total = 0
    for result in results:
        usage = result.usage or {}
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        total += int(usage.get("total_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _print_result(label: str, result: Any, parse_note: str) -> None:
    usage = result.usage or {}
    print(
        f"{label:<18} {result.mode:<22} {result.model:<16} "
        f"{result.latency_ms:>5} ms  "
        f"in {int(usage.get('prompt_tokens') or 0):>5}  "
        f"out {int(usage.get('completion_tokens') or 0):>4}  "
        f"{len(result.text):>5} chars  {parse_note}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Record the Book IQ model calls.")
    parser.add_argument("--refresh", action="store_true", help="Replace existing recordings with fresh calls.")
    parser.add_argument(
        "--drafts-only",
        action="store_true",
        help="Reuse the recorded ranking and refresh only the outreach drafts.",
    )
    args = parser.parse_args()

    mode = config.llm_mode()
    if mode != "live":
        print(f"SAVVY_LLM_MODE is {mode!r}. Run with SAVVY_LLM_MODE=live.")
        return 2

    payload = data.households_payload()
    households = payload.get("households") or []
    if not households:
        print("No households. Run python3 scripts/generate_bookiq.py first.")
        return 2

    results: list[Any] = []
    failures: list[str] = []

    if args.drafts_only:
        rank = llm.recorded(logic.MODULE, logic.RANK_KEY)
        if rank is None:
            print("No ranking recording. Run without --drafts-only first.")
            return 2
        rows, meta = logic.parse_ranking(rank.text)
        print(f"ranking reused: {rank.model}, {rank.created_at}, {len(rows)} rows")
    else:
        system, prompt = logic.build_rank_prompt(payload)
        print(f"ranking prompt: {len(prompt)} chars, {len(households)} households")
        rank = llm.run(
            logic.MODULE,
            logic.RANK_KEY,
            system,
            prompt,
            max_tokens=2400,
            temperature=0.2,
            refresh=args.refresh,
        )
        results.append(rank)
        rows, meta = logic.parse_ranking(rank.text)
        note = f"rows={len(rows)}"
        if meta.get("error"):
            note += f" ERROR: {meta['error']}"
            failures.append("ranking: " + meta["error"])
        _print_result("rank", rank, note)

    by_id = {household["id"]: household for household in households}
    rank_by_id = {row["household_id"]: row for row in rows if row.get("play")}
    missing = [household_id for household_id in by_id if household_id not in rank_by_id]
    if missing:
        print(f"ranking omitted: {', '.join(missing)}")

    for household in households:
        household_id = household["id"]
        model_row = rank_by_id.get(household_id)
        play = model_row["play"] if model_row else logic.rule_play(household)["play"]
        signal = model_row["signal"] if model_row else (logic.rule_play(household).get("signal") or "")
        system, prompt = logic.build_draft_prompt(household, play, signal)
        result = llm.run(
            logic.MODULE,
            logic.draft_key(household_id),
            system,
            prompt,
            max_tokens=900,
            temperature=0.35,
            refresh=args.refresh,
        )
        results.append(result)
        draft, error = logic.parse_draft(result.text)
        if error:
            failures.append(f"{household_id}: {error}")
            note = "ERROR: " + error
        else:
            note = f"play={play} words={logic.word_count(draft['body'])} claims={len(draft['claims'])}"
        _print_result(household_id, result, note)

    totals = _usage_totals(results)
    print("")
    print(f"calls: {len(results)}")
    print(f"models: {', '.join(sorted({result.model for result in results}))}")
    print(f"latency mean: {int(sum(result.latency_ms for result in results) / len(results))} ms")
    print(f"tokens: {json.dumps(totals)}")
    if failures:
        print("")
        print("parse failures:")
        for failure in failures:
            print(" -", failure)
        return 1
    print("")
    print("all model outputs parsed. The app replays them with SAVVY_LLM_MODE=cached.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
