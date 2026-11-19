#!/usr/bin/env python3
"""Run the Book IQ eval against the recorded ranking and write eval.json.

    SAVVY_LLM_MODE=cached python3 scripts/eval_bookiq.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.bookiq import eval as bookiq_eval


def main() -> int:
    report = bookiq_eval.run()
    path, report = bookiq_eval.write(report)
    if report.get("status") == "recording_missing":
        print("No ranking recording. Run SAVVY_LLM_MODE=live python3 scripts/record_bookiq.py first.")
        return 2
    print(f"{report['name']}: {report['accuracy'] * 100:.1f}%")
    print(f"cases: {report['cases']}")
    print(f"model: {report['model']} ({report['mode']}), recorded {report['recorded_at']}")
    print(f"coverage: {report['coverage']} of {report['cases']} households")
    print(f"rule agreement: {report['rule_agreement']}")
    print("")
    for row in report.get("results") or []:
        mark = "PASS" if row["status"] == "pass" else "FAIL"
        print(f"  {mark}  {row['household_id']}  expected {row['expected']}  actual {row['actual']}")
    if report["failures"]:
        print("")
        print("failures:")
        for failure in report["failures"]:
            print(" -", json.dumps(failure, ensure_ascii=False))
    else:
        print("")
        print("no failures in this run.")
    print("")
    print(f"wrote: {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
