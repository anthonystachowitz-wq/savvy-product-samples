#!/usr/bin/env python3
"""Run the supervision claim-flag evaluation and write data/modules/supervision/eval.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.modules.supervision import data, eval as sup_eval  # noqa: E402

OUT_PATH = REPO_ROOT / "data" / "modules" / "supervision" / "eval.json"


def main() -> int:
    report = sup_eval.run_eval()
    OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("Supervision in the loop - claim flag accuracy")
    print("Source: " + str(report["source"]))
    print("Model: " + (report["model"] or "n/a") + " at " + (report["recorded_at"] or "n/a"))
    print("Cases: " + str(report["cases"]))
    print("Exact match: " + str(report["exact"]) + " of " + str(report["cases"]))
    print("Accuracy: {:.1%}".format(report["accuracy"]))
    print("Flag precision: {:.1%}".format(report["precision"]))
    print("Flag recall: {:.1%}".format(report["recall"]))
    print("Flag F1: {:.1%}".format(report["f1"]))
    flags = report["flags"]
    print(
        "Flags: expected {expected}, actual {actual}, true positive {tp}, false positive {fp}, false negative {fn}".format(
            expected=flags["expected"],
            actual=flags["actual"],
            tp=flags["true_positive"],
            fp=flags["false_positive"],
            fn=flags["false_negative"],
        )
    )
    print("Report: " + str(OUT_PATH))
    if report["failures"]:
        print()
        print("Failures (" + str(len(report["failures"])) + "):")
        for failure in report["failures"]:
            print("  - " + failure["case"])
            print("    expected " + failure["expected"] + ", got " + failure["actual"])
            print("    " + failure["note"])
    else:
        print()
        print("No failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
