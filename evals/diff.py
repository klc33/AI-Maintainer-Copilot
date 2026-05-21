"""Compare a current eval report against the previous green build's report
and an absolute threshold floor. Exit non-zero if any gate trips.

The thresholds file (eval_thresholds.yaml) defines, per metric:
  min_absolute       — current must be >= this, no matter what previous was
  max_relative_drop  — current must be >= previous * (1 - max_relative_drop)

A "skipped" suite is never a regression — only suites with status='ok' are
compared. A suite with status='error' fails the build immediately.

Usage:
    python evals/diff.py \\
        --current eval_report.json \\
        --previous prev/eval_report.json \\
        --thresholds eval_thresholds.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


def _load_report(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _metrics(report: dict, suite: str) -> dict:
    """Return the metrics dict for a suite if it ran ok; else {}."""
    s = (report.get("suites") or {}).get(suite) or {}
    if s.get("status") != "ok":
        return {}
    return s.get("metrics") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--current", required=True)
    ap.add_argument("--previous", default=None,
                    help="Previous green build report. Optional; relative checks skipped if missing.")
    ap.add_argument("--thresholds", required=True)
    args = ap.parse_args()

    current = _load_report(Path(args.current))
    previous = _load_report(Path(args.previous)) if args.previous else {}
    thresholds = yaml.safe_load(Path(args.thresholds).read_text()) or {}

    failures: list[str] = []
    notes: list[str] = []
    suites = current.get("suites") or {}

    # Any suite that errored is an immediate fail.
    for suite_name, suite in suites.items():
        if suite.get("status") == "error":
            failures.append(f"{suite_name}: suite errored — {suite.get('reason', 'unknown')}")
        elif suite.get("status") == "skipped":
            notes.append(f"{suite_name}: skipped — {suite.get('reason', 'no reason given')}")

    # Per-metric gates.
    for suite_name, suite_thresholds in (thresholds or {}).items():
        cur_metrics = _metrics(current, suite_name)
        prev_metrics = _metrics(previous, suite_name)

        if not cur_metrics:
            # Suite didn't run ok this build — already flagged above as error/skip.
            continue

        for metric, gates in (suite_thresholds or {}).items():
            cur = cur_metrics.get(metric)
            if cur is None:
                notes.append(f"{suite_name}.{metric}: not in current report, skipping")
                continue

            min_abs = gates.get("min_absolute")
            if min_abs is not None and cur < min_abs:
                failures.append(
                    f"{suite_name}.{metric}: {cur:.4f} < min_absolute {min_abs:.4f}"
                )

            max_drop = gates.get("max_relative_drop")
            if max_drop is not None and prev_metrics.get(metric) is not None:
                prev = prev_metrics[metric]
                if prev > 0:
                    drop = (prev - cur) / prev
                    if drop > max_drop:
                        failures.append(
                            f"{suite_name}.{metric}: dropped {drop:.1%} "
                            f"(prev={prev:.4f} current={cur:.4f}, allowed {max_drop:.0%})"
                        )

    print("=" * 60)
    print("EVAL REGRESSION CHECK")
    print("=" * 60)
    print(f"Current  git_sha: {current.get('git_sha', 'unknown')}")
    print(f"Previous git_sha: {previous.get('git_sha', 'n/a')}")
    if notes:
        print("\nNotes:")
        for n in notes:
            print(f"  · {n}")
    if failures:
        print("\nREGRESSIONS:")
        for f in failures:
            print(f"  ✗ {f}")
        print(f"\n{len(failures)} regression(s) detected. Failing the build.")
        return 1
    print("\n✓ All metrics within thresholds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
