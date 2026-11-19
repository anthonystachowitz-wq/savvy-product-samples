"""Run the Concierge evaluation against the recorded answers.

    python3 scripts/eval_concierge.py

Forces cached mode so the eval never makes a live call, then writes
data/modules/concierge/eval.json and prints the score and the failures.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["SAVVY_LLM_MODE"] = "cached"

from app.modules.concierge import eval as concierge_eval  # noqa: E402


def main() -> int:
    payload = concierge_eval.evaluate()
    passed = sum(1 for item in payload.get("results", []) if item.get("passed"))
    print(payload.get("name"))
    print("accuracy:", payload["accuracy"], "(" + str(passed) + " of " + str(payload["cases"]) + " cases)")
    print("failures:", len(payload.get("failures", [])))
    for failure in payload.get("failures", []):
        print(" -", failure["case"], failure["household"], "| expected:", failure["expected"], "| actual:", failure["actual"])
        for reason in failure["reasons"]:
            print("     reason:", reason)
    print("wrote:", str(concierge_eval.OUT_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
