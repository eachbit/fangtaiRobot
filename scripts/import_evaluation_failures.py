from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.evaluation.issue_registry import (
    IssueRegistry,
    _assert_no_link_ancestors,
    _directory_identity,
    _is_link_or_reparse_point,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import persisted public evaluation failure JSON files."
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="recursively scan directory inputs for JSON files",
    )
    parser.add_argument("paths", nargs="+", type=Path)
    return parser


def _repository_path(repository_root: str | os.PathLike[str]) -> Path:
    if not isinstance(repository_root, (str, os.PathLike)):
        raise ValueError("repository_root must be path-like")
    lexical = Path(repository_root)
    if ".." in lexical.parts:
        raise ValueError("repository_root must not contain parent traversal parts")
    if not lexical.is_absolute():
        lexical = Path.cwd() / lexical
    repository = Path(os.path.abspath(lexical))
    _assert_no_link_ancestors(repository)
    if not repository.is_dir():
        raise ValueError("repository_root must be an existing directory")
    return repository


def _input_path(value: Path, repository: Path, evaluation_root: Path) -> Path:
    if ".." in value.parts:
        raise ValueError("input path must not contain parent traversal parts")
    candidate = value if value.is_absolute() else repository / value
    candidate = Path(os.path.abspath(candidate))
    try:
        candidate.relative_to(evaluation_root)
    except ValueError as exc:
        raise ValueError("input path must be within artifacts/evaluation") from exc
    _assert_no_link_ancestors(candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"input path does not exist: {candidate}") from exc
    try:
        resolved.relative_to(evaluation_root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("input path escapes artifacts/evaluation") from exc
    if resolved != candidate:
        raise ValueError("input path must not contain links or reparse points")
    return candidate


def _scan_directory(
    directory: Path,
    *,
    recursive: bool,
    direct_failure_root: bool = False,
) -> list[Path]:
    _directory_identity(directory)
    files: list[Path] = []
    collect_direct_json = direct_failure_root or (
        directory.name == "failures" and directory.parent.parent.name == "rounds"
    )
    for child in sorted(directory.iterdir(), key=lambda item: item.name):
        if _is_link_or_reparse_point(child):
            raise ValueError("directory input must not contain links or reparse points")
        metadata = child.lstat()
        if stat.S_ISREG(metadata.st_mode):
            if child.suffix.lower() == ".json" and collect_direct_json:
                files.append(child)
        elif stat.S_ISDIR(metadata.st_mode) and recursive and not collect_direct_json:
            files.extend(_scan_directory(child, recursive=True))
    return files


def _collect_files(
    values: Sequence[Path],
    repository: Path,
    evaluation_root: Path,
    *,
    recursive: bool,
) -> tuple[Path, ...]:
    files: set[Path] = set()
    for value in values:
        path = _input_path(value, repository, evaluation_root)
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode):
            if path.suffix.lower() != ".json":
                raise ValueError("failure file inputs must use the .json extension")
            files.add(path)
        elif stat.S_ISDIR(metadata.st_mode):
            direct_failure_root = path.name == "failures"
            files.update(
                _scan_directory(
                    path,
                    recursive=recursive and not direct_failure_root,
                    direct_failure_root=direct_failure_root,
                )
            )
        else:
            raise ValueError("input path must be a regular file or directory")
    return tuple(sorted(files, key=lambda item: item.as_posix()))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(
    argv: Sequence[str] | None = None,
    *,
    repository_root: str | os.PathLike[str] = REPO_ROOT,
    registry: Any | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        repository = _repository_path(repository_root)
        evaluation_root = repository / "artifacts" / "evaluation"
        active_registry = registry or IssueRegistry(evaluation_root)
        _assert_no_link_ancestors(evaluation_root)
        files = _collect_files(
            args.paths,
            repository,
            evaluation_root,
            recursive=args.recursive,
        )
        if not files:
            raise ValueError("no failure JSON files found")
        observed_at = _utc_now()
        touched: set[str] = set()
        for path in files:
            touched.update(
                active_registry.ingest_failure_file(
                    path,
                    observed_at=observed_at,
                )
            )
    except Exception as error:
        print(f"import error: {error}", file=sys.stderr)
        return 2

    for issue_id in sorted(touched):
        print(issue_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
