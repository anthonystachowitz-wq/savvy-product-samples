#!/usr/bin/env python3
"""Run the LP Ops field eval and write data/modules/lpo/eval.json.

    python3 scripts/eval_lpo.py

The eval reads recorded extractions only and never makes a model call.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.modules.lpo import eval as lpo_eval


def main() -> int:
    report = lpo_eval.run()
    path = lpo_eval.write_report(report)
    print(f"Eval: {report['name']}")
    print(f"Cases: {report['cases']} packets")
    print(f"Checks: {report['checks']} ({report['field_checks']} field cells, {report['exception_checks']} exception sets)")
    print(f"Accuracy: {report['accuracy']:.4f} ({report['accuracy'] * 100:.1f}%)")
    print(f"Field accuracy: {report['field_accuracy']:.4f}")
    print(f"Exception accuracy: {report['exception_accuracy']:.4f}")
    print(f"Failures: {len(report['failures'])}")
    for failure in report["failures"][:12]:
        print(f"  {failure['case']} {failure['field_label']}: expected {failure['expected']}, actual {failure['actual']}")
    print("Notes:")
    for note in report["failure_notes"]:
        print(f"  {note}")
    print(f"Wrote: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
