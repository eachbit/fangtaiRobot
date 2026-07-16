from __future__ import annotations

import errno
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.evaluation.issue_registry import IssueRegistry
from tests.evaluation.runner import EvaluationRunner, MODE_COUNTS
from tests.evaluation.schemas import EvaluationReport


_REPO_ROOT = Path(__file__).resolve().parents[2]
CYCLE_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,80}\Z")
_WINDOWS_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_SCHEMA_VERSION = 1
_CYCLE_STATUSES = frozenset({"running", "completed", "failed", "stopped"})
_ROUND_STATUSES = frozenset({"running", "completed", "failed"})
_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "cycle_id",
        "mode",
        "base_seed",
        "target_rounds",
        "status",
        "commit_sha",
        "created_at",
        "updated_at",
        "completed_rounds",
        "issue_ids",
        "rounds",
    }
)
_ROUND_FIELDS = frozenset(
    {
        "index",
        "seed",
        "status",
        "total",
        "passed",
        "failures",
        "elapsed_ms",
        "output_path",
        "issue_ids",
        "started_at",
        "finished_at",
        "error_type",
        "error",
    }
)
_LOCK_RETRIES = 100
_LOCK_RETRY_DELAY_SECONDS = 0.01


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _assert_no_link_ancestors(path: Path) -> None:
    current = path
    while True:
        if _is_link_or_reparse_point(current):
            raise ValueError("cycle path must not contain links or reparse points")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _validate_cycle_id(cycle_id: Any) -> str:
    if type(cycle_id) is not str or CYCLE_ID_PATTERN.fullmatch(cycle_id) is None:
        raise ValueError("cycle_id must match [A-Za-z0-9._-]{1,80}")
    if cycle_id in {".", ".."}:
        raise ValueError("cycle_id must name a child directory")
    if cycle_id.endswith("."):
        raise ValueError("cycle_id must not end with a period")
    if cycle_id.split(".", 1)[0].upper() in _WINDOWS_RESERVED_STEMS:
        raise ValueError("cycle_id uses a reserved Windows stem")
    return cycle_id


def _validate_evaluation_root(
    evaluation_root: str | os.PathLike[str],
    repository_root: str | os.PathLike[str],
) -> Path:
    if not isinstance(repository_root, (str, os.PathLike)):
        raise ValueError("repository_root must be path-like")
    lexical_repository = Path(repository_root)
    if ".." in lexical_repository.parts:
        raise ValueError("repository_root must not contain parent traversal parts")
    if not lexical_repository.is_absolute():
        lexical_repository = Path.cwd() / lexical_repository
    resolved_repository = Path(os.path.abspath(lexical_repository))
    _assert_no_link_ancestors(resolved_repository)
    if not resolved_repository.is_dir():
        raise ValueError("repository_root must be an existing directory")

    if not isinstance(evaluation_root, (str, os.PathLike)):
        raise ValueError("evaluation_root must be path-like")
    lexical_root = Path(evaluation_root)
    if lexical_root == Path("."):
        raise ValueError("evaluation_root must not be the current directory")
    if ".." in lexical_root.parts:
        raise ValueError("evaluation_root must not contain parent traversal parts")
    candidate = (
        lexical_root
        if lexical_root.is_absolute()
        else resolved_repository / lexical_root
    )
    candidate = Path(os.path.abspath(candidate))
    allowed_root = resolved_repository / "artifacts" / "evaluation"
    _assert_no_link_ancestors(allowed_root)
    _assert_no_link_ancestors(candidate)
    resolved_allowed = allowed_root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_allowed)
    except ValueError as exc:
        raise ValueError(
            "evaluation_root must be within repository artifacts/evaluation"
        ) from exc
    return resolved_candidate


def _validate_parameters(
    evaluation_root: str | os.PathLike[str],
    cycle_id: Any,
    mode: Any,
    rounds: Any,
    base_seed: Any,
    repository_root: str | os.PathLike[str],
) -> tuple[Path, str, str, int, int]:
    root = _validate_evaluation_root(evaluation_root, repository_root)
    validated_id = _validate_cycle_id(cycle_id)
    if type(mode) is not str or mode not in MODE_COUNTS:
        raise ValueError(f"mode must be one of {', '.join(MODE_COUNTS)}")
    if type(rounds) is not int or rounds <= 0:
        raise ValueError("rounds must be a positive integer")
    if type(base_seed) is not int:
        raise ValueError("base_seed must be an integer")
    return root, validated_id, mode, rounds, base_seed


def _create_directory(path: Path) -> None:
    if _is_link_or_reparse_point(path):
        raise ValueError("cycle path must not contain links or reparse points")
    path.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse_point(path) or not path.is_dir():
        raise ValueError("cycle path must be a real directory")


def _prepare_cycle_directory(root: Path, cycle_id: str) -> Path:
    _create_directory(root)
    _assert_no_link_ancestors(root)
    cycles_root = root / "cycles"
    _create_directory(cycles_root)
    cycle_dir = cycles_root / cycle_id
    _create_directory(cycle_dir)
    resolved_root = root.resolve()
    resolved_cycles = cycles_root.resolve()
    resolved_cycle = cycle_dir.resolve()
    if resolved_cycles.parent != resolved_root:
        raise ValueError("cycles path escapes evaluation_root")
    if resolved_cycle.parent != resolved_cycles:
        raise ValueError("cycle path escapes cycles root")
    return cycle_dir


def _json_value(value: Any, path: str = "$") -> Any:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path}: number must be finite")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path}: JSON object keys must be strings")
            result[key] = _json_value(item, f"{path}.{key}")
        return result
    if type(value) in (list, tuple):
        return [_json_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise ValueError(f"{path}: unsupported JSON value type {type(value).__name__}")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _atomic_write_text(path: Path, content: str) -> None:
    _assert_no_link_ancestors(path.parent)
    if _is_link_or_reparse_point(path):
        raise ValueError("atomic write destination must not be a link or reparse point")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    content = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    _atomic_write_text(path, f"{content}\n")


def _open_lock_descriptor(lock_path: Path) -> int:
    if _is_link_or_reparse_point(lock_path):
        raise ValueError("cycle lock must not be a link or reparse point")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("cycle lock must be a regular file")
        if metadata.st_size < 1:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _acquire_lock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_lock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _is_lock_contention(error: OSError) -> bool:
    return error.errno in {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EDEADLK", errno.EACCES),
    }


@contextmanager
def _cycle_lock(cycle_dir: Path) -> Iterator[None]:
    _assert_no_link_ancestors(cycle_dir)
    descriptor = _open_lock_descriptor(cycle_dir / ".cycle.lock")
    acquired = False
    try:
        for attempt in range(_LOCK_RETRIES):
            try:
                _acquire_lock(descriptor)
            except OSError as error:
                if not _is_lock_contention(error):
                    raise
                if attempt + 1 == _LOCK_RETRIES:
                    raise TimeoutError("timed out waiting for cycle lock") from error
                time.sleep(_LOCK_RETRY_DELAY_SECONDS)
            else:
                acquired = True
                break
        if not acquired:
            raise TimeoutError("timed out waiting for cycle lock")
        yield
    finally:
        if acquired:
            _release_lock(descriptor)
        os.close(descriptor)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _current_commit_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else "unknown"


def _validate_timestamp(value: Any, field: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if type(value) is not str or not value:
        raise ValueError(f"{field} must be a non-empty timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")


def _validate_issue_ids(value: Any, field: str) -> list[str]:
    if type(value) is not list:
        raise ValueError(f"{field} must be an array")
    if any(type(item) is not str or not item for item in value):
        raise ValueError(f"{field} contains an invalid issue id")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} must be deduplicated")
    return value


def _expected_output_path(index: int, seed: int) -> str:
    return f"rounds/{index + 1:04d}-{seed}"


def _validate_round(value: Any, target_rounds: int, base_seed: int) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _ROUND_FIELDS:
        raise ValueError("round record has invalid fields")
    index = value["index"]
    if type(index) is not int or not 0 <= index < target_rounds:
        raise ValueError("round index is out of range")
    seed = value["seed"]
    if type(seed) is not int or seed != base_seed + index:
        raise ValueError("round seed does not match its index")
    if value["output_path"] != _expected_output_path(index, seed):
        raise ValueError("round output_path does not match its index and seed")
    status = value["status"]
    if type(status) is not str or status not in _ROUND_STATUSES:
        raise ValueError("round status is invalid")
    _validate_timestamp(value["started_at"], "round.started_at")
    _validate_timestamp(value["finished_at"], "round.finished_at", nullable=status == "running")
    _validate_issue_ids(value["issue_ids"], "round.issue_ids")

    if status == "completed":
        for field in ("total", "passed", "failures"):
            if type(value[field]) is not int or value[field] < 0:
                raise ValueError(f"round.{field} must be a non-negative integer")
        if (
            value["passed"] > value["total"]
            or value["failures"] != value["total"] - value["passed"]
        ):
            raise ValueError("round totals are inconsistent")
        elapsed = value["elapsed_ms"]
        if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("round.elapsed_ms must be a finite non-negative number")
        if value["error_type"] is not None or value["error"] is not None:
            raise ValueError("completed round must not contain an error")
    else:
        if any(value[field] is not None for field in ("total", "passed", "failures")):
            raise ValueError("unfinished round must not contain totals")
        if value["issue_ids"]:
            raise ValueError("unfinished round must not contain issue ids")
        if status == "running":
            finished_fields = ("elapsed_ms", "finished_at", "error_type", "error")
            if any(value[field] is not None for field in finished_fields):
                raise ValueError("running round contains finished fields")
        else:
            elapsed = value["elapsed_ms"]
            if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("failed round elapsed_ms is invalid")
            if type(value["error_type"]) is not str or not value["error_type"]:
                raise ValueError("failed round error_type is invalid")
            if type(value["error"]) is not str:
                raise ValueError("failed round error is invalid")
    return value


def _aggregate_issue_ids(round_records: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for record in round_records:
        if record["status"] != "completed":
            continue
        for issue_id in record["issue_ids"]:
            if issue_id not in result:
                result.append(issue_id)
    return result


def _validate_state(
    value: Any,
    *,
    cycle_id: str,
    mode: str,
    rounds: int,
    base_seed: int,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _STATE_FIELDS:
        raise ValueError("cycle state has invalid fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("cycle schema_version must be 1")
    if type(value["cycle_id"]) is not str or value["cycle_id"] != cycle_id:
        raise ValueError("cycle_id does not match existing state")
    if type(value["mode"]) is not str or value["mode"] != mode:
        raise ValueError("mode does not match existing cycle")
    if type(value["target_rounds"]) is not int or value["target_rounds"] != rounds:
        raise ValueError("rounds does not match existing cycle")
    if type(value["base_seed"]) is not int or value["base_seed"] != base_seed:
        raise ValueError("base_seed does not match existing cycle")
    if type(value["status"]) is not str or value["status"] not in _CYCLE_STATUSES:
        raise ValueError("cycle status is invalid")
    if type(value["commit_sha"]) is not str or not value["commit_sha"]:
        raise ValueError("commit_sha must be a non-empty string")
    _validate_timestamp(value["created_at"], "created_at")
    _validate_timestamp(value["updated_at"], "updated_at")
    if type(value["rounds"]) is not list:
        raise ValueError("cycle rounds must be an array")
    round_records = [_validate_round(item, rounds, base_seed) for item in value["rounds"]]
    indices = [record["index"] for record in round_records]
    if indices != sorted(indices) or len(indices) != len(set(indices)):
        raise ValueError("cycle rounds must have unique ordered indices")
    completed = sum(record["status"] == "completed" for record in round_records)
    if type(value["completed_rounds"]) is not int or value["completed_rounds"] != completed:
        raise ValueError("completed_rounds aggregate is inconsistent")
    issue_ids = _validate_issue_ids(value["issue_ids"], "issue_ids")
    if issue_ids != _aggregate_issue_ids(round_records):
        raise ValueError("issue_ids aggregate is inconsistent")
    if value["status"] == "completed" and completed != rounds:
        raise ValueError("completed cycle does not contain every completed round")
    failed = sum(record["status"] == "failed" for record in round_records)
    if value["status"] in {"failed", "stopped"} and failed == 0:
        raise ValueError("failed or stopped cycle must contain a failed round")
    if value["status"] == "failed" and len(round_records) != rounds:
        raise ValueError("failed cycle must contain every target round")
    if value["status"] in {"completed", "failed", "stopped"} and any(
        record["status"] == "running" for record in round_records
    ):
        raise ValueError("finished cycle contains a running round")
    return value


def _load_state(
    path: Path,
    *,
    cycle_id: str,
    mode: str,
    rounds: int,
    base_seed: int,
) -> dict[str, Any]:
    if _is_link_or_reparse_point(path):
        raise ValueError("cycle state must not be a link or reparse point")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("cycle state is not valid strict JSON") from exc
    return _validate_state(
        value,
        cycle_id=cycle_id,
        mode=mode,
        rounds=rounds,
        base_seed=base_seed,
    )


def _new_round(index: int, seed: int, started_at: str) -> dict[str, Any]:
    return {
        "index": index,
        "seed": seed,
        "status": "running",
        "total": None,
        "passed": None,
        "failures": None,
        "elapsed_ms": None,
        "output_path": _expected_output_path(index, seed),
        "issue_ids": [],
        "started_at": started_at,
        "finished_at": None,
        "error_type": None,
        "error": None,
    }


def _replace_round(state: dict[str, Any], record: dict[str, Any]) -> None:
    existing = {item["index"]: item for item in state["rounds"]}
    existing[record["index"]] = record
    state["rounds"] = [existing[index] for index in sorted(existing)]
    state["completed_rounds"] = sum(
        item["status"] == "completed" for item in state["rounds"]
    )
    state["issue_ids"] = _aggregate_issue_ids(state["rounds"])


def _write_state(path: Path, state: dict[str, Any], utc_now: Callable[[], str]) -> None:
    state["updated_at"] = utc_now()
    _validate_timestamp(state["updated_at"], "updated_at")
    _atomic_write_json(path, state)


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    completed = [item for item in state["rounds"] if item["status"] == "completed"]
    elapsed = round(sum(float(item["elapsed_ms"]) for item in completed), 2)
    return {
        "schema_version": _SCHEMA_VERSION,
        "cycle_id": state["cycle_id"],
        "mode": state["mode"],
        "status": state["status"],
        "base_seed": state["base_seed"],
        "target_rounds": state["target_rounds"],
        "completed_rounds": state["completed_rounds"],
        "total_scenarios": sum(item["total"] for item in completed),
        "passed": sum(item["passed"] for item in completed),
        "failures": sum(item["failures"] for item in completed),
        "issue_ids": list(state["issue_ids"]),
        "elapsed_ms": elapsed,
        "average_elapsed_ms": round(elapsed / len(completed), 2) if completed else 0.0,
        "created_at": state["created_at"],
        "updated_at": state["updated_at"],
        "rounds": [dict(item) for item in state["rounds"]],
    }


def _summary_markdown(summary: Mapping[str, Any]) -> str:
    issue_ids = ", ".join(summary["issue_ids"]) or "none"
    lines = [
        f"# Autonomous Evaluation Cycle {summary['cycle_id']}",
        "",
        f"- Status: {summary['status']}",
        f"- Mode: {summary['mode']}",
        f"- Completed rounds: {summary['completed_rounds']}/{summary['target_rounds']}",
        f"- Total scenarios: {summary['total_scenarios']}",
        f"- Passed: {summary['passed']}",
        f"- Failures: {summary['failures']}",
        f"- Issues: {issue_ids}",
        f"- Elapsed ms: {summary['elapsed_ms']}",
        f"- Average elapsed ms: {summary['average_elapsed_ms']}",
        "",
        "| Round | Seed | Status | Total | Passed | Failures | Elapsed ms | Issues |",
        "| ---: | ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for record in summary["rounds"]:
        row_issues = ", ".join(record["issue_ids"]) or ""
        values = (
            record["index"] + 1,
            record["seed"],
            record["status"],
            "" if record["total"] is None else record["total"],
            "" if record["passed"] is None else record["passed"],
            "" if record["failures"] is None else record["failures"],
            "" if record["elapsed_ms"] is None else record["elapsed_ms"],
            row_issues,
        )
        lines.append("| " + " | ".join(str(value) for value in values) + " |")
    return "\n".join(lines) + "\n"


def _write_summaries(cycle_dir: Path, state: dict[str, Any]) -> None:
    summary = _summary(state)
    _atomic_write_json(cycle_dir / "summary.json", summary)
    _atomic_write_text(cycle_dir / "summary.md", _summary_markdown(summary))


def run_cycle(
    evaluation_root: str | os.PathLike[str],
    cycle_id: str,
    mode: str,
    rounds: int,
    base_seed: int,
    *,
    continue_on_error: bool = False,
    runner_factory: Callable[..., EvaluationRunner] = EvaluationRunner,
    registry: Any = None,
    clock: Callable[[], float] = time.perf_counter,
    utc_now: Callable[[], str] = _utc_now,
    commit_sha: str | Callable[[], str] = _current_commit_sha,
    repository_root: str | os.PathLike[str] = _REPO_ROOT,
) -> dict[str, Any]:
    root, cycle_id, mode, rounds, base_seed = _validate_parameters(
        evaluation_root,
        cycle_id,
        mode,
        rounds,
        base_seed,
        repository_root,
    )
    if type(continue_on_error) is not bool:
        raise ValueError("continue_on_error must be a boolean")
    cycle_dir = _prepare_cycle_directory(root, cycle_id)
    state_path = cycle_dir / "cycle.json"

    with _cycle_lock(cycle_dir):
        if state_path.exists():
            state = _load_state(
                state_path,
                cycle_id=cycle_id,
                mode=mode,
                rounds=rounds,
                base_seed=base_seed,
            )
            if state["status"] == "completed":
                _write_summaries(cycle_dir, state)
                return state
            state["status"] = "running"
            _write_state(state_path, state, utc_now)
        else:
            created_at = utc_now()
            _validate_timestamp(created_at, "created_at")
            resolved_sha = commit_sha() if callable(commit_sha) else commit_sha
            if type(resolved_sha) is not str or not resolved_sha:
                raise ValueError("commit_sha must be a non-empty string")
            state = {
                "schema_version": _SCHEMA_VERSION,
                "cycle_id": cycle_id,
                "mode": mode,
                "base_seed": base_seed,
                "target_rounds": rounds,
                "status": "running",
                "commit_sha": resolved_sha,
                "created_at": created_at,
                "updated_at": created_at,
                "completed_rounds": 0,
                "issue_ids": [],
                "rounds": [],
            }
            _atomic_write_json(state_path, state)

        issue_registry = registry
        completed_indices = {
            record["index"] for record in state["rounds"] if record["status"] == "completed"
        }
        for index in range(rounds):
            if index in completed_indices:
                continue
            seed = base_seed + index
            round_dir = cycle_dir / Path(_expected_output_path(index, seed))
            started_at = utc_now()
            _validate_timestamp(started_at, "round.started_at")
            record = _new_round(index, seed, started_at)
            _replace_round(state, record)
            _write_state(state_path, state, utc_now)
            started = clock()
            try:
                rounds_root = cycle_dir / "rounds"
                _create_directory(rounds_root)
                _assert_no_link_ancestors(rounds_root)
                if rounds_root.resolve().parent != cycle_dir.resolve():
                    raise ValueError("rounds path escapes cycle directory")
                _create_directory(round_dir)
                _assert_no_link_ancestors(round_dir)
                if round_dir.resolve().parent != rounds_root.resolve():
                    raise ValueError("round output path escapes cycle directory")
                if issue_registry is None:
                    issue_registry = IssueRegistry(root)
                runner = runner_factory(round_dir, seed=seed, mode=mode)
                report = runner.run_mode()
                if not isinstance(report, EvaluationReport):
                    raise ValueError("runner must return an EvaluationReport")
                if report.total < 0 or report.passed < 0 or report.passed > report.total:
                    raise ValueError("evaluation report totals are inconsistent")
                issue_ids = list(
                    issue_registry.ingest(
                        report,
                        runner.scenario_context,
                        observed_at=utc_now(),
                    )
                )
                _validate_issue_ids(issue_ids, "round.issue_ids")
                elapsed_ms = round(max(0.0, (clock() - started) * 1000.0), 2)
                record.update(
                    {
                        "status": "completed",
                        "total": report.total,
                        "passed": report.passed,
                        "failures": report.total - report.passed,
                        "elapsed_ms": elapsed_ms,
                        "issue_ids": issue_ids,
                        "finished_at": utc_now(),
                    }
                )
            except Exception as error:
                elapsed_ms = round(max(0.0, (clock() - started) * 1000.0), 2)
                record.update(
                    {
                        "status": "failed",
                        "elapsed_ms": elapsed_ms,
                        "finished_at": utc_now(),
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
                _replace_round(state, record)
                if not continue_on_error:
                    state["status"] = "stopped"
                    _write_state(state_path, state, utc_now)
                    _write_summaries(cycle_dir, state)
                    return state
            _replace_round(state, record)
            _write_state(state_path, state, utc_now)

        state["status"] = (
            "failed"
            if any(record["status"] == "failed" for record in state["rounds"])
            else "completed"
        )
        _write_state(state_path, state, utc_now)
        _write_summaries(cycle_dir, state)
        return state


__all__ = ["CYCLE_ID_PATTERN", "run_cycle"]
