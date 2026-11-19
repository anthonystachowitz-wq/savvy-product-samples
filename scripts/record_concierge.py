"""Record every Concierge model call, live, once.

    SAVVY_LLM_MODE=live python3 scripts/record_concierge.py
    SAVVY_LLM_MODE=live python3 scripts/record_concierge.py --refresh

Each question and household pair makes one call. The recording lands in
data/cached/llm/concierge/<key>.json with the model, latency, token usage, and
timestamp. Re-running without --refresh replays the existing recordings.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SAVVY_LLM_MODE", "live")

from app.platform import config, llm  # noqa: E402
from app.modules.concierge import data, logic  # noqa: E402


def main() -> int:
    refresh = "--refresh" in sys.argv
    records = data.records()
    questions = data.questions()
    households = {item["id"]: item for item in records.get("households", [])}
    qindex = data.question_index(questions)
    keys = data.key_index(questions)
    if not keys:
        print("No questions found. Run scripts/generate_concierge.py first.")
        return 1

    settings = config.deepseek()
    print("mode:", config.llm_mode(), "| model:", settings["model"], "| keys:", len(keys), "| refresh:", refresh)
    started = time.time()
    failures: list[str] = []
    latencies: list[int] = []
    tokens = 0
    models: set[str] = set()

    for key in sorted(keys, key=lambda item: (keys[item]["question_id"], keys[item]["household_id"])):
        entry = keys[key]
        household = households.get(entry["household_id"])
        question = qindex.get(entry["question_id"], {}).get("text", "")
        if household is None:
            failures.append(key + " (household missing)")
            continue
        try:
            bundle = logic.ask(household, question, key=key, refresh=refresh, audit_live=False)
        except llm.LLMUnavailable as exc:
            failures.append(key + " (" + str(exc) + ")")
            print("FAIL", key, exc)
            continue
        provenance = bundle["provenance"]
        latencies.append(provenance["latency_ms"])
        tokens += provenance["total_tokens"]
        if provenance["model"]:
            models.add(provenance["model"])
        print(
            key.ljust(12),
            provenance["mode"].ljust(20),
            provenance["model"].ljust(15),
            str(provenance["latency_ms"]).rjust(5) + " ms",
            str(provenance["total_tokens"]).rjust(5) + " tokens",
            "intent=" + bundle["intent"].ljust(11),
            "check=" + ("pass" if bundle["guardrail"]["ok"] else "fail"),
            entry["household_id"],
        )

    elapsed = int(time.time() - started)
    print()
    print("recorded keys:", len(keys) - len(failures), "of", len(keys), "| wall:", elapsed, "s")
    print("models:", ", ".join(sorted(models)) if models else "none")
    print("mean latency:", int(round(sum(latencies) / len(latencies))) if latencies else 0, "ms")
    print("total tokens:", tokens)
    print("cache:", str(config.CACHE_DIR / logic.MODULE))
    if failures:
        print("failures:")
        for item in failures:
            print(" -", item)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
