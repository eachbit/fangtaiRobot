from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.evaluation.runner import EvaluationRunner, MODE_COUNTS


def default_output_dir(now: datetime | None = None) -> Path:
    current = now or datetime.now(timezone.utc)
    return Path("artifacts") / "evaluation" / current.astimezone(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic nutrition evaluations.")
    parser.add_argument("--mode", choices=tuple(MODE_COUNTS), default="quick")
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--count", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-holdout", action="store_true")
    parser.add_argument("--holdout-dir", type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner_factory: Callable[..., EvaluationRunner] = EvaluationRunner,
) -> int:
    args = build_parser().parse_args(argv)
    output = args.output or default_output_dir()
    try:
        runner = runner_factory(
            output,
            seed=args.seed,
            mode=args.mode,
            include_holdout=args.include_holdout,
            holdout_dir=args.holdout_dir,
        )
        report = runner.run_count(args.count) if args.count is not None else runner.run_mode()
    except (OSError, ValueError) as error:
        print(f"evaluation error: {error}", file=sys.stderr)
        return 2

    print(
        f"evaluation total={report.total} passed={report.passed} "
        f"blocking={len(report.failures)} output={output}"
    )
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
