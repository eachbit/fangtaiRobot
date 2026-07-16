from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.evaluation.autonomous_cycle import _REPO_ROOT, run_cycle
from tests.evaluation.runner import MODE_COUNTS


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a resumable autonomous evaluation cycle.")
    parser.add_argument("--mode", choices=tuple(MODE_COUNTS), default="quick")
    parser.add_argument("--rounds", type=_positive_int, default=10)
    parser.add_argument("--seed", type=int, default=20260716)
    parser.add_argument("--cycle-id")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    cycle_runner: Callable[..., dict[str, Any]] = run_cycle,
) -> int:
    args = build_parser().parse_args(argv)
    cycle_id = args.cycle_id or f"{args.mode}-{args.seed}-{args.rounds}"
    evaluation_root = _REPO_ROOT / "artifacts" / "evaluation"
    try:
        state = cycle_runner(
            evaluation_root,
            cycle_id,
            args.mode,
            args.rounds,
            args.seed,
            continue_on_error=args.continue_on_error,
            repository_root=_REPO_ROOT,
        )
    except (OSError, ValueError) as error:
        print(f"cycle error: {error}", file=sys.stderr)
        return 2

    print(
        f"cycle={state['cycle_id']} status={state['status']} "
        f"rounds={state['completed_rounds']}/{state['target_rounds']} "
        f"issues={len(state['issue_ids'])} root={evaluation_root}"
    )
    if state["status"] != "completed":
        return 2
    return 1 if state["issue_ids"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
