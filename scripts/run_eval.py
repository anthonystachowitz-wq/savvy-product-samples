#!/usr/bin/env python3
"""CLI for the extraction evaluation.

Usage:
    python3 scripts/run_eval.py
    python3 scripts/run_eval.py --provider cached
    python3 scripts/run_eval.py --data-dir /path/to/data --json

Defaults to the deterministic extractor, writes data/eval/report.json, and prints a
readable summary with the first ten mismatches.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.llm import ConfigurationError, get_provider  # noqa: E402
from app.pipeline.eval import run_eval  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate extraction against labeled data.")
    parser.add_argument(
        "--provider",
        choices=("cached", "bedrock"),
        default=None,
        help="Optional provider. Default is the deterministic extractor.",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Data directory. Defaults to <repo>/data.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report JSON instead of the readable summary.",
    )
    args = parser.parse_args(argv)

    provider = None
    if args.provider:
        try:
            provider = get_provider(args.provider)
        except ConfigurationError as exc:
            print(f"Provider error: {exc}", file=sys.stderr)
            return 2

    report = run_eval(provider, data_dir_path=args.data_dir)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print("Transition Copilot - extraction evaluation")
    print(f"Run at: {report['run_at']}")
    print(f"Method: {report['method']}")
    print(f"Documents evaluated: {report.get('documents_evaluated', 0)}")
    print(f"Fields compared: {report['field_count']}")
    print(f"Correct: {report['correct_count']}")
    print(f"Accuracy: {report['accuracy'] * 100:.2f}%")
    print(f"Report: {Path(report['labels_path']).with_name('report.json')}")

    for note in report.get("notes", []):
        print(f"Note: {note}")

    failures = report.get("failures", [])
    if failures:
        print()
        print(f"Mismatches ({len(failures)}):")
        for failure in failures[:10]:
            print(
                "  - "
                f"{failure['document_id']} / {failure['field']}: "
                f"expected {failure['expected']!r}, got {failure['actual']!r}"
            )
        remaining = len(failures) - 10
        if remaining > 0:
            print(f"  ... and {remaining} more in the report.")
    else:
        print()
        print("No mismatches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
