from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.evaluation.autonomous_cycle import (
    _assert_no_link_ancestors,
    _is_link_or_reparse_point,
    _reject_json_constant,
    _strict_json_object,
    _validate_cycle_id,
    _validate_evaluation_root,
    _validate_state,
)
from tests.evaluation.issue_registry import ISSUE_STATUSES, IssueRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage an evaluation issue.")
    parser.add_argument("issue_id")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--status", choices=tuple(sorted(ISSUE_STATUSES)))
    actions.add_argument("--export-regression", action="store_true")
    parser.add_argument("--cycle-id")
    return parser


def _strict_cycle_state(path: Path, cycle_id: str) -> dict[str, Any]:
    _assert_no_link_ancestors(path)
    if _is_link_or_reparse_point(path):
        raise ValueError("cycle state must not be a link or reparse point")
    try:
        metadata = path.stat()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"verification cycle does not exist: {cycle_id}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("cycle state must be a regular file")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("cycle state is not valid strict JSON") from exc
    if type(value) is not dict:
        raise ValueError("cycle state must be a JSON object")
    return _validate_state(
        value,
        cycle_id=cycle_id,
        mode=value.get("mode"),
        rounds=value.get("target_rounds"),
        base_seed=value.get("base_seed"),
    )


def _load_verification_cycle(
    repository_root: str | os.PathLike[str],
    cycle_id: Any,
) -> dict[str, Any]:
    validated_id = _validate_cycle_id(cycle_id)
    evaluation_root = _validate_evaluation_root(
        Path(repository_root) / "artifacts" / "evaluation",
        repository_root,
    )
    cycles_root = evaluation_root / "cycles"
    cycle_directory = cycles_root / validated_id
    state_path = cycle_directory / "cycle.json"
    _assert_no_link_ancestors(state_path)
    resolved_evaluation = evaluation_root.resolve(strict=False)
    resolved_cycles = cycles_root.resolve(strict=False)
    resolved_cycle = cycle_directory.resolve(strict=False)
    if resolved_cycles.parent != resolved_evaluation:
        raise ValueError("cycles path escapes evaluation_root")
    if resolved_cycle.parent != resolved_cycles:
        raise ValueError("cycle path escapes cycles root")
    if state_path.resolve(strict=False).parent != resolved_cycle:
        raise ValueError("cycle state path escapes cycle directory")
    return _strict_cycle_state(state_path, validated_id)


def main(
    argv: Sequence[str] | None = None,
    *,
    repository_root: str | os.PathLike[str] = REPO_ROOT,
    registry_factory: Callable[[Path], IssueRegistry] = IssueRegistry,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        evaluation_root = _validate_evaluation_root(
            Path(repository_root) / "artifacts" / "evaluation",
            repository_root,
        )
        registry = registry_factory(evaluation_root)
        if args.export_regression:
            path = registry.export_regression_candidate(args.issue_id)
        else:
            verification_cycle = None
            if args.status == "resolved":
                if args.cycle_id is None:
                    raise ValueError("resolved status requires --cycle-id")
                verification_cycle = _load_verification_cycle(
                    repository_root,
                    args.cycle_id,
                )
            issue = registry.set_status(
                args.issue_id,
                args.status,
                verification_cycle=verification_cycle,
            )
            path = (
                evaluation_root
                / "issues"
                / issue["status"]
                / f"{issue['issue_id']}.json"
            )
    except Exception as error:
        print(f"issue error: {error}", file=sys.stderr)
        return 2

    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
