from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from tests.evaluation.schemas import EvaluationReport, FailureRecord, Scenario, Violation


ISSUE_STATUSES = frozenset({"open", "verifying", "resolved"})
ISSUE_ID_PATTERN = re.compile(r"issue-[0-9a-f]{24}\Z")
HISTORY_LIMIT = 256

_SCHEMA_VERSION = 1
_SCENARIO_HASH_PATTERN = re.compile(
    r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z"
)
_OBSERVATION_HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_OBSERVATION_TEMP_PATTERN = re.compile(
    r"\.([0-9a-f]{64})\.json\.[^.\\/]+\.tmp\Z"
)
_LOCK_RETRIES = 100
_LOCK_RETRY_DELAY_SECONDS = 0.01
_PUBLIC_FIELDS = frozenset(
    {
        "schema_version",
        "issue_id",
        "fingerprint",
        "status",
        "severity",
        "violation_code",
        "holdout",
        "scenario_ids",
        "health_buckets",
        "intents",
        "original_messages",
        "minimized_messages",
        "expected",
        "latest_evidence",
        "regression_source",
        "first_seen_at",
        "last_seen_at",
        "first_seen_commit",
        "last_seen_commit",
        "occurrences",
        "seeds",
    }
)
_HOLDOUT_FIELDS = frozenset(
    {
        "schema_version",
        "issue_id",
        "fingerprint",
        "status",
        "severity",
        "violation_code",
        "holdout",
        "scenario_hash",
        "first_seen_at",
        "last_seen_at",
        "occurrences",
    }
)


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(reparse_flag and file_attributes & reparse_flag)


def _assert_no_link_ancestors(path: Path) -> None:
    current = path
    while True:
        if _is_link_or_reparse_point(current):
            raise ValueError("issue registry path must not contain links or reparse points")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _json_value(value: Any, path: str = "$") -> Any:
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int:
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


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _strict_json_loads(value: str) -> Any:
    return json.loads(
        value,
        object_pairs_hook=_strict_json_object,
        parse_constant=_reject_json_constant,
    )


def _strict_json_object_from_path(path: Path) -> dict[str, Any]:
    value = _strict_json_loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _defensive_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    copied = _strict_json_loads(_canonical_json(value))
    if type(copied) is not dict:
        raise ValueError("expected a JSON object")
    return copied


def _parse_observed_at(value: Any) -> datetime:
    if type(value) is not str or not value:
        raise ValueError("observed_at must be a non-empty ISO string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("observed_at must be an ISO string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return parsed


def _validate_issue_id(issue_id: Any) -> str:
    if type(issue_id) is not str or ISSUE_ID_PATTERN.fullmatch(issue_id) is None:
        raise ValueError("invalid issue identifier")
    return issue_id


def _sorted_history(values: Any, *, integers: bool = False) -> list[Any]:
    if type(values) is not list:
        raise ValueError("issue history must be an array")
    expected_type = int if integers else str
    if any(type(value) is not expected_type for value in values):
        raise ValueError("issue history contains an invalid value")
    result = sorted(set(values))
    if result != values:
        raise ValueError("issue history must be sorted and deduplicated")
    return result


def _merged_history(
    existing: list[Any],
    value: Any,
    *,
    limit: int | None = None,
) -> list[Any]:
    result = sorted({*existing, value})
    return result[-limit:] if limit is not None else result


def _validated_scenario_hash(value: Any) -> str:
    if type(value) is not str or _SCENARIO_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "scenario_hash must be a 32, 40, or 64 character hexadecimal digest"
        )
    return value.lower()


def issue_fingerprint(
    failure: FailureRecord,
    violation: Violation,
    context: Mapping[str, Any],
) -> str:
    if not isinstance(failure, FailureRecord):
        raise ValueError("failure must be a FailureRecord")
    if not isinstance(violation, Violation) or violation.severity != "blocking":
        raise ValueError("violation must be a blocking Violation")
    if not isinstance(context, Mapping):
        raise ValueError("context must be a mapping")
    if context.get("holdout") is True:
        scenario_hash = _validated_scenario_hash(context.get("scenario_hash"))
        payload = {
            "violation_code": violation.code,
            "scenario_hash": scenario_hash,
        }
    else:
        payload = {
            "violation_code": violation.code,
            "minimized_messages": list(failure.minimized_messages),
            "health_bucket": context.get("health_bucket"),
            "intent": context.get("intent"),
            "expectation": context.get("expectation", {}),
            "scenario_hash": context.get("scenario_hash"),
        }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:24]


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
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
            json.dump(
                _json_value(value),
                temporary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _atomic_create_json(path: Path, value: Mapping[str, Any]) -> bool:
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
            json.dump(
                _json_value(value),
                temporary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            return False
        return True
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class IssueRegistry:
    def __init__(self, evaluation_root: str | os.PathLike[str]) -> None:
        lexical_root = Path(evaluation_root)
        if ".." in lexical_root.parts:
            raise ValueError("evaluation_root must not contain parent traversal parts")
        if not lexical_root.is_absolute():
            lexical_root = Path.cwd() / lexical_root
        self.evaluation_root = Path(os.path.abspath(lexical_root))
        _assert_no_link_ancestors(self.evaluation_root)
        self.issues_root = self.evaluation_root / "issues"
        self.status_directories = {
            status: self.issues_root / status for status in sorted(ISSUE_STATUSES)
        }
        self.observations_root = self.issues_root / "observations"
        self.index_path = self.issues_root / "index.json"
        self._lock_path = self.issues_root / ".registry.lock"
        self._journal_path = self.issues_root / ".transaction.json"
        self._create_layout()
        with self._locked():
            self._rebuild_index()

    def _create_layout(self) -> None:
        paths = [
            self.evaluation_root,
            self.issues_root,
            *(self.status_directories[status] for status in sorted(ISSUE_STATUSES)),
            self.observations_root,
        ]
        for path in paths:
            if _is_link_or_reparse_point(path):
                raise ValueError("issue registry path must not contain links or reparse points")
            path.mkdir(parents=True, exist_ok=True)
            if _is_link_or_reparse_point(path):
                raise ValueError("issue registry path must not contain links or reparse points")
        self._assert_layout()

    def _assert_layout(self) -> None:
        _assert_no_link_ancestors(self.evaluation_root)
        paths = [
            self.evaluation_root,
            self.issues_root,
            *(self.status_directories[status] for status in sorted(ISSUE_STATUSES)),
            self.observations_root,
        ]
        for path in paths:
            if _is_link_or_reparse_point(path):
                raise ValueError("issue registry path must not contain links or reparse points")
            if not path.is_dir():
                raise ValueError("issue registry path must be a directory")

        resolved_root = self.evaluation_root.resolve()
        resolved_issues = self.issues_root.resolve()
        try:
            resolved_issues.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("issue registry path escapes evaluation_root") from exc
        if resolved_issues.parent != resolved_root:
            raise ValueError("issues must be directly below evaluation_root")
        for directory in self.status_directories.values():
            resolved = directory.resolve()
            if resolved.parent != resolved_issues:
                raise ValueError("issue status directory escapes issues root")
        if self.observations_root.resolve().parent != resolved_issues:
            raise ValueError("observations directory escapes issues root")

        for path in (self.index_path, self._lock_path, self._journal_path):
            if _is_link_or_reparse_point(path):
                raise ValueError("issue registry files must not be links or reparse points")
            if path.resolve().parent != resolved_issues:
                raise ValueError("issue registry file escapes issues root")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._assert_layout()
        descriptor = self._open_lock_descriptor()
        acquired = False
        try:
            for attempt in range(_LOCK_RETRIES):
                try:
                    self._acquire_advisory_lock(descriptor)
                except OSError as error:
                    if not self._is_lock_contention(error):
                        raise
                    if attempt + 1 == _LOCK_RETRIES:
                        raise TimeoutError(
                            "timed out waiting for issue registry lock"
                        ) from error
                    time.sleep(_LOCK_RETRY_DELAY_SECONDS)
                else:
                    acquired = True
                    break
            if not acquired:
                raise TimeoutError("timed out waiting for issue registry lock")
            self._recover_transaction()
            yield
        finally:
            if acquired:
                try:
                    self._release_advisory_lock(descriptor)
                except OSError as error:
                    if not self._is_closed_descriptor(error):
                        raise
            try:
                os.close(descriptor)
            except OSError as error:
                if not self._is_closed_descriptor(error):
                    raise

    def _open_lock_descriptor(self) -> int:
        flags = os.O_CREAT | os.O_RDWR
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self._lock_path, flags, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("registry lock must be a regular file")
            if metadata.st_size < 1:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _acquire_advisory_lock(descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _release_advisory_lock(descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)

    @staticmethod
    def _is_lock_contention(error: OSError) -> bool:
        return error.errno in {
            errno.EACCES,
            errno.EAGAIN,
            getattr(errno, "EDEADLK", errno.EACCES),
        }

    @staticmethod
    def _is_closed_descriptor(error: OSError) -> bool:
        return error.errno == errno.EBADF or getattr(error, "winerror", None) == 6

    def _issue_path(self, issue_id: str, status: str) -> Path:
        _validate_issue_id(issue_id)
        if status not in ISSUE_STATUSES:
            raise ValueError("invalid issue status")
        directory = self.status_directories[status]
        path = directory / f"{issue_id}.json"
        if path.resolve().parent != directory.resolve():
            raise ValueError("issue path escapes status directory")
        return path

    @staticmethod
    def _observation_hash(observation_id: Any) -> str:
        if type(observation_id) is not str or not observation_id:
            raise ValueError("observation_id must be a non-empty string")
        return hashlib.sha256(observation_id.encode("utf-8")).hexdigest()

    def _observation_path_from_hash(self, observation_hash: str) -> Path:
        if (
            type(observation_hash) is not str
            or _OBSERVATION_HASH_PATTERN.fullmatch(observation_hash) is None
        ):
            raise ValueError("invalid observation hash")
        path = self.observations_root / f"{observation_hash}.json"
        if path.resolve().parent != self.observations_root.resolve():
            raise ValueError("observation path escapes observations directory")
        return path

    def _observation_path(self, observation_id: str) -> Path:
        return self._observation_path_from_hash(self._observation_hash(observation_id))

    def _validate_observation(
        self,
        value: Any,
        *,
        expected_hash: str,
    ) -> dict[str, Any]:
        if (
            type(value) is not dict
            or frozenset(value)
            != {"schema_version", "observation_hash", "issue_ids"}
        ):
            raise ValueError("observation marker fields do not match the registry schema")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("unsupported observation marker schema version")
        if value["observation_hash"] != expected_hash:
            raise ValueError("observation marker hash does not match its filename")
        issue_ids = value["issue_ids"]
        if type(issue_ids) is not list:
            raise ValueError("observation marker issue_ids must be an array")
        if any(
            type(issue_id) is not str
            or ISSUE_ID_PATTERN.fullmatch(issue_id) is None
            for issue_id in issue_ids
        ):
            raise ValueError("observation marker contains an invalid issue id")
        if issue_ids != sorted(set(issue_ids)):
            raise ValueError("observation marker issue_ids must be sorted and deduplicated")
        return value

    def _load_observation_path(
        self,
        path: Path,
        *,
        expected_hash: str,
    ) -> dict[str, Any]:
        if _is_link_or_reparse_point(path):
            raise ValueError("observation marker must not be a link or reparse point")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("observation marker must be a regular file")
        return self._validate_observation(
            _strict_json_object_from_path(path),
            expected_hash=expected_hash,
        )

    def _validate_observation_files(self, issue_ids: set[str]) -> None:
        for path in sorted(self.observations_root.iterdir()):
            if path.suffix != ".json" or _OBSERVATION_HASH_PATTERN.fullmatch(path.stem) is None:
                raise ValueError(f"invalid observation marker filename: {path.name}")
            marker = self._load_observation_path(path, expected_hash=path.stem)
            if not set(marker["issue_ids"]).issubset(issue_ids):
                raise ValueError("observation marker references an unknown issue")

    def _assert_issue_file(self, path: Path) -> None:
        if _is_link_or_reparse_point(path):
            raise ValueError("issue file must not be a link or reparse point")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("issue path must be a regular file")

    def _validate_issue(
        self,
        value: Any,
        *,
        expected_issue_id: str | None = None,
        expected_status: str | None = None,
    ) -> dict[str, Any]:
        if type(value) is not dict:
            raise ValueError("issue must be a JSON object")
        holdout = value.get("holdout")
        if type(holdout) is not bool:
            raise ValueError("issue holdout flag must be boolean")
        allowed = _HOLDOUT_FIELDS if holdout else _PUBLIC_FIELDS
        if frozenset(value) != allowed:
            raise ValueError("issue fields do not match the registry schema")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != _SCHEMA_VERSION
        ):
            raise ValueError("unsupported issue schema version")
        issue_id = _validate_issue_id(value["issue_id"])
        if expected_issue_id is not None and issue_id != expected_issue_id:
            raise ValueError("issue identifier does not match its file")
        fingerprint = value["fingerprint"]
        if type(fingerprint) is not str or issue_id != f"issue-{fingerprint}":
            raise ValueError("issue fingerprint does not match its identifier")
        status = value["status"]
        if status not in ISSUE_STATUSES:
            raise ValueError("invalid persisted issue status")
        if expected_status is not None and status != expected_status:
            raise ValueError("issue status does not match its directory")
        if value["severity"] != "blocking":
            raise ValueError("issue severity must be blocking")
        if type(value["violation_code"]) is not str or not value["violation_code"]:
            raise ValueError("issue violation code must be a non-empty string")
        _parse_observed_at(value["first_seen_at"])
        _parse_observed_at(value["last_seen_at"])
        if type(value["occurrences"]) is not int or value["occurrences"] < 1:
            raise ValueError("issue occurrences must be a positive integer")

        if holdout:
            _validated_scenario_hash(value["scenario_hash"])
            return value

        scenario_ids = _sorted_history(value["scenario_ids"])
        seeds = _sorted_history(value["seeds"], integers=True)
        if len(scenario_ids) > HISTORY_LIMIT or len(seeds) > HISTORY_LIMIT:
            raise ValueError("issue history exceeds the configured limit")
        _sorted_history(value["health_buckets"])
        _sorted_history(value["intents"])
        for field in ("original_messages", "minimized_messages"):
            messages = value[field]
            if type(messages) is not list or any(type(item) is not str for item in messages):
                raise ValueError(f"{field} must be an array of strings")
        if type(value["expected"]) is not dict:
            raise ValueError("expected must be a JSON object")
        _json_value(value["latest_evidence"])
        if type(value["regression_source"]) is not dict:
            raise ValueError("regression_source must be a JSON object")
        scenario = Scenario.from_dict(value["regression_source"])
        if scenario.expectation.to_dict() != value["expected"]:
            raise ValueError("regression source expectation does not match expected")
        for field in ("first_seen_commit", "last_seen_commit"):
            if type(value[field]) is not str:
                raise ValueError(f"{field} must be a string")
        return value

    def _load_path(
        self,
        path: Path,
        *,
        expected_issue_id: str,
        expected_status: str,
    ) -> dict[str, Any]:
        self._assert_issue_file(path)
        return self._validate_issue(
            _strict_json_object_from_path(path),
            expected_issue_id=expected_issue_id,
            expected_status=expected_status,
        )

    def _find_issue(self, issue_id: str) -> tuple[str, Path, dict[str, Any]] | None:
        issue_id = _validate_issue_id(issue_id)
        found: list[tuple[str, Path, dict[str, Any]]] = []
        for status in sorted(ISSUE_STATUSES):
            path = self._issue_path(issue_id, status)
            if path.exists() or path.is_symlink():
                found.append(
                    (
                        status,
                        path,
                        self._load_path(
                            path,
                            expected_issue_id=issue_id,
                            expected_status=status,
                        ),
                    )
                )
        if len(found) > 1:
            raise ValueError(f"duplicate issue files for {issue_id}")
        return found[0] if found else None

    def _index_entries(
        self,
        preferred_statuses: Mapping[str, str] | None = None,
    ) -> dict[str, dict[str, str]]:
        preferred = dict(preferred_statuses or {})
        candidates: dict[str, list[tuple[str, Path]]] = {}
        for status in sorted(ISSUE_STATUSES):
            directory = self.status_directories[status]
            for path in sorted(directory.glob("*.json")):
                if ISSUE_ID_PATTERN.fullmatch(path.stem) is None:
                    raise ValueError(f"invalid issue filename: {path.name}")
                issue_id = path.stem
                self._load_path(
                    path,
                    expected_issue_id=issue_id,
                    expected_status=status,
                )
                candidates.setdefault(issue_id, []).append((status, path))

        entries: dict[str, dict[str, str]] = {}
        for issue_id, locations in sorted(candidates.items()):
            selected: tuple[str, Path]
            if len(locations) == 1:
                selected = locations[0]
            else:
                preferred_status = preferred.get(issue_id)
                matching = [item for item in locations if item[0] == preferred_status]
                if len(matching) != 1:
                    raise ValueError(f"duplicate issue files for {issue_id}")
                selected = matching[0]
            status, path = selected
            if not path.is_file():
                raise ValueError("issue index cannot reference a missing file")
            entries[issue_id] = {
                "status": status,
                "path": f"{status}/{path.name}",
            }
        return entries

    def _rebuild_index(
        self,
        preferred_statuses: Mapping[str, str] | None = None,
    ) -> None:
        self._assert_layout()
        entries = self._index_entries(preferred_statuses)
        self._validate_observation_files(set(entries))
        index = {
            "schema_version": _SCHEMA_VERSION,
            "issues": entries,
        }
        _atomic_write_json(self.index_path, index)

    def _validate_index_payload(self, value: Any) -> dict[str, Any]:
        if (
            type(value) is not dict
            or frozenset(value) != {"schema_version", "issues"}
        ):
            raise ValueError("index fields do not match the registry schema")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("unsupported index schema version")
        issues = value["issues"]
        if type(issues) is not dict:
            raise ValueError("index issues must be a JSON object")
        for issue_id, entry in issues.items():
            _validate_issue_id(issue_id)
            if type(entry) is not dict or frozenset(entry) != {"status", "path"}:
                raise ValueError("index entry fields do not match the registry schema")
            status = entry["status"]
            if status not in ISSUE_STATUSES:
                raise ValueError("index entry contains an invalid status")
            expected_path = f"{status}/{issue_id}.json"
            if entry["path"] != expected_path:
                raise ValueError("index entry path does not match its issue status")
        return value

    def _validate_index_matches_files(self) -> None:
        self._assert_issue_file(self.index_path)
        index = self._validate_index_payload(_strict_json_object_from_path(self.index_path))
        if index["issues"] != self._index_entries():
            raise ValueError("issue index does not match persisted issue files")
        self._validate_observation_files(set(index["issues"]))

    def _transaction_relative_path(self, path: Path) -> str:
        candidate = Path(path).absolute()
        if candidate == self.index_path:
            return "index.json"
        if (
            candidate.parent == self.observations_root
            and candidate.suffix == ".json"
            and _OBSERVATION_HASH_PATTERN.fullmatch(candidate.stem) is not None
        ):
            return f"observations/{candidate.name}"
        for status, directory in self.status_directories.items():
            if candidate.parent == directory and ISSUE_ID_PATTERN.fullmatch(candidate.stem):
                if candidate.suffix != ".json":
                    break
                return f"{status}/{candidate.name}"
        raise ValueError("transaction path escapes the issue registry")

    def _transaction_path(self, relative_path: Any) -> Path:
        if relative_path == "index.json":
            return self.index_path
        if type(relative_path) is not str:
            raise ValueError("transaction path must be a string")
        observation_match = re.fullmatch(
            r"observations/([0-9a-f]{64})\.json",
            relative_path,
        )
        if observation_match is not None:
            return self._observation_path_from_hash(observation_match.group(1))
        match = re.fullmatch(
            r"(open|verifying|resolved)/(issue-[0-9a-f]{24})\.json",
            relative_path,
        )
        if match is None:
            raise ValueError("invalid transaction path")
        return self._issue_path(match.group(2), match.group(1))

    def _validate_transaction_snapshot(
        self,
        relative_path: str,
        value: Any,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        if type(value) is not dict:
            raise ValueError("transaction snapshot must be a JSON object or null")
        if relative_path == "index.json":
            return self._validate_index_payload(value)
        if relative_path.startswith("observations/"):
            return self._validate_observation(
                value,
                expected_hash=Path(relative_path).stem,
            )
        status, filename = relative_path.split("/", 1)
        return self._validate_issue(
            value,
            expected_issue_id=filename.removesuffix(".json"),
            expected_status=status,
        )

    def _load_transaction_journal(
        self,
    ) -> list[tuple[str, Path, dict[str, Any] | None]]:
        if _is_link_or_reparse_point(self._journal_path):
            raise ValueError("transaction journal must not be a link or reparse point")
        journal = _strict_json_object_from_path(self._journal_path)
        if (
            type(journal) is not dict
            or frozenset(journal) != {"schema_version", "files"}
        ):
            raise ValueError("transaction journal fields do not match the registry schema")
        if type(journal["schema_version"]) is not int or journal["schema_version"] != 1:
            raise ValueError("unsupported transaction journal schema version")
        files = journal["files"]
        if type(files) is not list or not files:
            raise ValueError("transaction journal files must be a non-empty array")
        result: list[tuple[str, Path, dict[str, Any] | None]] = []
        seen: set[str] = set()
        for entry in files:
            if type(entry) is not dict or frozenset(entry) != {"path", "before"}:
                raise ValueError("transaction journal entry fields are invalid")
            relative_path = entry["path"]
            path = self._transaction_path(relative_path)
            if relative_path in seen:
                raise ValueError("transaction journal contains duplicate paths")
            seen.add(relative_path)
            before = self._validate_transaction_snapshot(relative_path, entry["before"])
            result.append((relative_path, path, before))
        if "index.json" not in seen:
            raise ValueError("transaction journal must include index.json")
        self._validate_transaction_semantics(result)
        return result

    @staticmethod
    def _validate_transaction_semantics(
        entries: list[tuple[str, Path, dict[str, Any] | None]],
    ) -> None:
        index_before = next(
            before
            for relative_path, _, before in entries
            if relative_path == "index.json"
        )
        if index_before is None:
            raise ValueError(
                "transaction journal index.json before-image must not be null"
            )
        old_issues = index_before["issues"]
        grouped: dict[str, list[tuple[str, dict[str, Any] | None]]] = {}
        for relative_path, _, before in entries:
            if relative_path == "index.json":
                continue
            if relative_path.startswith("observations/"):
                if before is not None:
                    raise ValueError(
                        "transaction observation marker before-image must be null"
                    )
                continue
            _, filename = relative_path.split("/", 1)
            issue_id = filename.removesuffix(".json")
            grouped.setdefault(issue_id, []).append((relative_path, before))

        for issue_id, issue_entries in grouped.items():
            old_entry = old_issues.get(issue_id)
            if old_entry is None:
                if any(before is not None for _, before in issue_entries):
                    raise ValueError(
                        "transaction journal contains a non-null issue before-image "
                        "that is absent from the old index"
                    )
                continue

            expected_path = old_entry["path"]
            expected = [
                before
                for relative_path, before in issue_entries
                if relative_path == expected_path
            ]
            if len(expected) != 1 or expected[0] is None:
                raise ValueError(
                    "transaction journal must contain the old index issue path "
                    "with a non-null before-image"
                )
            if any(
                relative_path != expected_path and before is not None
                for relative_path, before in issue_entries
            ):
                raise ValueError(
                    "transaction journal issue path or status conflicts with the old index"
                )

    def _read_transaction_snapshot(
        self,
        relative_path: str,
        path: Path,
    ) -> dict[str, Any] | None:
        if not path.exists() and not path.is_symlink():
            return None
        if _is_link_or_reparse_point(path):
            raise ValueError("transaction file must not be a link or reparse point")
        value = _strict_json_object_from_path(path)
        return self._validate_transaction_snapshot(relative_path, value)

    def _begin_transaction(self, paths: Iterable[Path]) -> None:
        if self._journal_path.exists() or self._journal_path.is_symlink():
            raise ValueError("an issue registry transaction is already active")
        relative_paths = sorted({self._transaction_relative_path(path) for path in paths})
        if "index.json" not in relative_paths:
            raise ValueError("transaction must include index.json")
        files = []
        for relative_path in relative_paths:
            path = self._transaction_path(relative_path)
            before = self._read_transaction_snapshot(relative_path, path)
            if relative_path == "index.json" and before is None:
                raise ValueError("transaction requires an existing index")
            files.append({"path": relative_path, "before": before})
        _atomic_write_json(
            self._journal_path,
            {"schema_version": 1, "files": files},
        )

    def _restore_transaction_file(
        self,
        relative_path: str,
        path: Path,
        before: dict[str, Any] | None,
    ) -> None:
        exists = path.exists() or path.is_symlink()
        if exists and _is_link_or_reparse_point(path):
            raise ValueError("transaction file must not be a link or reparse point")
        if before is None:
            if exists:
                path.unlink()
            return
        if exists:
            current = self._read_transaction_snapshot(relative_path, path)
            if current == before:
                return
        _atomic_write_json(path, before)

    def _cleanup_observation_transaction_temps(
        self,
        entries: list[tuple[str, Path, dict[str, Any] | None]],
    ) -> None:
        allowed_hashes = {
            Path(relative_path).stem
            for relative_path, _, _ in entries
            if relative_path.startswith("observations/")
        }
        for path in sorted(self.observations_root.iterdir()):
            match = _OBSERVATION_TEMP_PATTERN.fullmatch(path.name)
            if match is None or match.group(1) not in allowed_hashes:
                continue
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or (
                reparse_flag and attributes & reparse_flag
            ):
                raise ValueError(
                    "observation temporary file must not be a link or reparse point"
                )
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("observation temporary path must be a regular file")
            path.unlink()

    def _recover_transaction(self) -> None:
        if not self._journal_path.exists() and not self._journal_path.is_symlink():
            return
        entries = self._load_transaction_journal()
        self._cleanup_observation_transaction_temps(entries)
        for relative_path, path, before in sorted(
            entries,
            key=lambda entry: entry[0] == "index.json",
        ):
            self._restore_transaction_file(relative_path, path, before)
        self._validate_index_matches_files()
        self._journal_path.unlink()

    @contextmanager
    def _transaction(self, paths: Iterable[Path]) -> Iterator[None]:
        self._begin_transaction(paths)
        try:
            yield
            self._validate_index_matches_files()
            self._journal_path.unlink()
        except BaseException as error:
            try:
                self._recover_transaction()
            except BaseException as recovery_error:
                raise recovery_error from error
            raise

    def _public_context(
        self,
        context: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
        if context.get("holdout") is not False:
            raise ValueError("public scenario context must set holdout to false")
        scenario_payload = context.get("scenario")
        if type(scenario_payload) is not dict:
            raise ValueError("public scenario context must contain a strict Scenario dict")
        scenario = Scenario.from_dict(scenario_payload)
        health_bucket = context.get("health_bucket")
        intent = context.get("intent")
        expectation = _json_value(context.get("expectation"))
        if health_bucket != scenario.persona.primary_bucket:
            raise ValueError("scenario context health bucket does not match Scenario")
        if intent != scenario.intent:
            raise ValueError("scenario context intent does not match Scenario")
        if expectation != scenario.expectation.to_dict():
            raise ValueError("scenario context expectation does not match Scenario")
        return scenario.to_dict(), health_bucket, intent, expectation

    @staticmethod
    def _holdout_hash(context: Mapping[str, Any]) -> str:
        if context.get("holdout") is not True:
            raise ValueError("holdout scenario context must set holdout to true")
        return _validated_scenario_hash(context.get("scenario_hash"))

    def _new_issue(
        self,
        issue_id: str,
        fingerprint: str,
        failure: FailureRecord,
        violation: Violation,
        context: Mapping[str, Any],
        observed_at: str,
    ) -> dict[str, Any]:
        if context.get("holdout") is True:
            return {
                "schema_version": _SCHEMA_VERSION,
                "issue_id": issue_id,
                "fingerprint": fingerprint,
                "status": "open",
                "severity": "blocking",
                "violation_code": violation.code,
                "holdout": True,
                "scenario_hash": self._holdout_hash(context),
                "first_seen_at": observed_at,
                "last_seen_at": observed_at,
                "occurrences": 1,
            }

        regression_source, health_bucket, intent, expectation = self._public_context(context)
        return {
            "schema_version": _SCHEMA_VERSION,
            "issue_id": issue_id,
            "fingerprint": fingerprint,
            "status": "open",
            "severity": "blocking",
            "violation_code": violation.code,
            "holdout": False,
            "scenario_ids": [failure.scenario_id],
            "health_buckets": [health_bucket],
            "intents": [intent],
            "original_messages": list(failure.original_messages),
            "minimized_messages": list(failure.minimized_messages),
            "expected": expectation,
            "latest_evidence": violation.to_dict()["evidence"],
            "regression_source": regression_source,
            "first_seen_at": observed_at,
            "last_seen_at": observed_at,
            "first_seen_commit": failure.commit_sha,
            "last_seen_commit": failure.commit_sha,
            "occurrences": 1,
            "seeds": [failure.seed],
        }

    def _merge_issue(
        self,
        issue: dict[str, Any],
        failure: FailureRecord,
        violation: Violation,
        context: Mapping[str, Any],
        observed_at: str,
    ) -> dict[str, Any]:
        result = _defensive_copy(issue)
        result["status"] = "open"
        result["occurrences"] += 1
        observed = _parse_observed_at(observed_at)
        first_seen = _parse_observed_at(result["first_seen_at"])
        last_seen = _parse_observed_at(result["last_seen_at"])
        if observed < first_seen:
            result["first_seen_at"] = observed_at
            if not result["holdout"]:
                result["first_seen_commit"] = failure.commit_sha
        if observed >= last_seen:
            result["last_seen_at"] = observed_at

        if result["holdout"]:
            scenario_hash = self._holdout_hash(context)
            if scenario_hash != result["scenario_hash"]:
                raise ValueError("holdout scenario hash does not match issue fingerprint")
            return result

        regression_source, health_bucket, intent, expectation = self._public_context(context)
        if expectation != result["expected"]:
            raise ValueError("issue expectation does not match its fingerprint")
        result["scenario_ids"] = _merged_history(
            result["scenario_ids"], failure.scenario_id, limit=HISTORY_LIMIT
        )
        result["seeds"] = _merged_history(result["seeds"], failure.seed, limit=HISTORY_LIMIT)
        result["health_buckets"] = _merged_history(result["health_buckets"], health_bucket)
        result["intents"] = _merged_history(result["intents"], intent)
        if observed >= last_seen:
            result["original_messages"] = list(failure.original_messages)
            result["minimized_messages"] = list(failure.minimized_messages)
            result["latest_evidence"] = violation.to_dict()["evidence"]
            result["regression_source"] = regression_source
            result["last_seen_commit"] = failure.commit_sha
        return result

    def ingest(
        self,
        report: EvaluationReport,
        scenario_context: Mapping[str, Mapping[str, Any]],
        *,
        observed_at: str,
        observation_id: str | None = None,
    ) -> tuple[str, ...]:
        if not isinstance(report, EvaluationReport):
            raise ValueError("report must be an EvaluationReport")
        if not isinstance(scenario_context, Mapping):
            raise ValueError("scenario_context must be a mapping")
        _parse_observed_at(observed_at)
        observation_hash = (
            None if observation_id is None else self._observation_hash(observation_id)
        )
        touched: set[str] = set()
        working: dict[str, dict[str, Any]] = {}
        sources: dict[str, tuple[str, Path] | None] = {}

        with self._locked():
            for failure in report.failures:
                blocking = tuple(
                    violation
                    for violation in failure.violations
                    if violation.severity == "blocking"
                )
                if not blocking:
                    continue
                context = scenario_context.get(failure.scenario_id)
                if not isinstance(context, Mapping):
                    raise ValueError(f"missing scenario context for {failure.scenario_id}")
                for violation in blocking:
                    fingerprint = issue_fingerprint(failure, violation, context)
                    issue_id = f"issue-{fingerprint}"
                    if issue_id not in working:
                        found = self._find_issue(issue_id)
                        if found is None:
                            issue = self._new_issue(
                                issue_id,
                                fingerprint,
                                failure,
                                violation,
                                context,
                                observed_at,
                            )
                            sources[issue_id] = None
                        else:
                            status, path, existing = found
                            issue = self._merge_issue(
                                existing,
                                failure,
                                violation,
                                context,
                                observed_at,
                            )
                            sources[issue_id] = (status, path)
                    else:
                        issue = self._merge_issue(
                            working[issue_id],
                            failure,
                            violation,
                            context,
                            observed_at,
                        )
                    self._validate_issue(issue, expected_issue_id=issue_id, expected_status="open")
                    working[issue_id] = issue
                    touched.add(issue_id)

            issue_ids = tuple(sorted(touched))
            marker_path: Path | None = None
            marker: dict[str, Any] | None = None
            if observation_hash is not None:
                marker_path = self._observation_path_from_hash(observation_hash)
                if marker_path.exists() or marker_path.is_symlink():
                    existing_marker = self._load_observation_path(
                        marker_path,
                        expected_hash=observation_hash,
                    )
                    if tuple(existing_marker["issue_ids"]) != issue_ids:
                        raise ValueError(
                            "observation_id was reused with a different issue set"
                        )
                    self._validate_index_matches_files()
                    return issue_ids
                marker = {
                    "schema_version": 1,
                    "observation_hash": observation_hash,
                    "issue_ids": list(issue_ids),
                }

            if not working and marker_path is None:
                return ()

            destinations: dict[str, Path] = {}
            for issue_id, issue in sorted(working.items()):
                destination = self._issue_path(issue_id, "open")
                destinations[issue_id] = destination
            transaction_paths = [self.index_path, *destinations.values()]
            if marker_path is not None:
                transaction_paths.append(marker_path)
            transaction_paths.extend(
                source[1]
                for source in sources.values()
                if source is not None
            )
            with self._transaction(transaction_paths):
                for issue_id, issue in sorted(working.items()):
                    _atomic_write_json(destinations[issue_id], issue)
                if marker_path is not None and marker is not None:
                    _atomic_write_json(marker_path, marker)
                for issue_id, source in sources.items():
                    if source is not None and source[1] != destinations[issue_id]:
                        source[1].unlink()
                self._rebuild_index()

        return issue_ids

    def load(self, issue_id: str) -> dict[str, Any]:
        issue_id = _validate_issue_id(issue_id)
        with self._locked():
            found = self._find_issue(issue_id)
            if found is None:
                raise FileNotFoundError(issue_id)
            return _defensive_copy(found[2])

    def _regression_candidate_path(self, issue_id: str) -> Path:
        candidates_root = self.evaluation_root / "candidates"
        regressions_root = candidates_root / "regressions"
        for path in (candidates_root, regressions_root):
            if _is_link_or_reparse_point(path):
                raise ValueError(
                    "regression candidate path must not contain links or reparse points"
                )
            path.mkdir(exist_ok=True)
            if _is_link_or_reparse_point(path) or not path.is_dir():
                raise ValueError("regression candidate path must be a real directory")
        _assert_no_link_ancestors(regressions_root)
        resolved_evaluation = self.evaluation_root.resolve()
        resolved_candidates = candidates_root.resolve()
        resolved_regressions = regressions_root.resolve()
        if resolved_candidates.parent != resolved_evaluation:
            raise ValueError("candidates path escapes evaluation_root")
        if resolved_regressions.parent != resolved_candidates:
            raise ValueError("regressions path escapes candidates root")
        path = regressions_root / f"{issue_id}.json"
        if _is_link_or_reparse_point(path):
            raise ValueError("regression candidate must not be a link or reparse point")
        if path.resolve(strict=False).parent != resolved_regressions:
            raise ValueError("regression candidate path escapes regressions root")
        return path

    @staticmethod
    def _assert_matching_candidate(path: Path, candidate: Mapping[str, Any]) -> None:
        if _is_link_or_reparse_point(path):
            raise ValueError("regression candidate must not be a link or reparse point")
        if not path.is_file():
            raise ValueError("regression candidate path must be a regular file")
        try:
            existing = _strict_json_object_from_path(path)
            Scenario.from_dict(existing)
        except (ValueError, UnicodeDecodeError) as exc:
            raise FileExistsError(
                "regression candidate already exists with different content"
            ) from exc
        if existing != candidate:
            raise FileExistsError(
                "regression candidate already exists with different content"
            )

    def export_regression_candidate(self, issue_id: str) -> Path:
        issue_id = _validate_issue_id(issue_id)
        with self._locked():
            found = self._find_issue(issue_id)
            if found is None:
                raise FileNotFoundError(issue_id)
            issue = found[2]
            if issue["holdout"]:
                raise ValueError("holdout issues cannot be exported as regression candidates")

            candidate_payload = _defensive_copy(issue["regression_source"])
            candidate_payload["scenario_id"] = f"regression-{issue['fingerprint']}"
            candidate_payload["messages"] = list(issue["minimized_messages"])
            candidate = Scenario.from_dict(candidate_payload).to_dict()
            path = self._regression_candidate_path(issue_id)
            if path.exists() or path.is_symlink():
                self._assert_matching_candidate(path, candidate)
                return path
            if not _atomic_create_json(path, candidate):
                self._assert_matching_candidate(path, candidate)
            return path

    @staticmethod
    def _validate_resolution_cycle(
        issue_id: str,
        verification_cycle: Mapping[str, Any] | None,
    ) -> None:
        if not isinstance(verification_cycle, Mapping):
            raise ValueError("resolving an issue requires a verification cycle")
        if verification_cycle.get("status") != "completed":
            raise ValueError("verification cycle must be completed")
        if verification_cycle.get("mode") not in {"daily", "deep"}:
            raise ValueError("verification cycle must use daily or deep mode")
        issue_ids = verification_cycle.get("issue_ids")
        if type(issue_ids) is not list or any(type(value) is not str for value in issue_ids):
            raise ValueError("verification cycle issue_ids must be an array of strings")
        if issue_id in issue_ids:
            raise ValueError("issue recurred in the verification cycle")

    def set_status(
        self,
        issue_id: str,
        status: str,
        *,
        verification_cycle: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        issue_id = _validate_issue_id(issue_id)
        if status not in ISSUE_STATUSES:
            raise ValueError("invalid issue status")
        with self._locked():
            found = self._find_issue(issue_id)
            if found is None:
                raise FileNotFoundError(issue_id)
            current_status, source, issue = found
            allowed = (current_status, status) in {
                ("open", "verifying"),
                ("verifying", "open"),
                ("resolved", "open"),
                ("verifying", "resolved"),
            }
            if not allowed:
                raise ValueError(f"invalid issue status transition: {current_status} -> {status}")
            if current_status == "verifying" and status == "resolved":
                self._validate_resolution_cycle(issue_id, verification_cycle)

            updated = _defensive_copy(issue)
            updated["status"] = status
            self._validate_issue(
                updated,
                expected_issue_id=issue_id,
                expected_status=status,
            )
            destination = self._issue_path(issue_id, status)
            with self._transaction((self.index_path, source, destination)):
                _atomic_write_json(destination, updated)
                source.unlink()
                self._rebuild_index()
            return _defensive_copy(updated)
