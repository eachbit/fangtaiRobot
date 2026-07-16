from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from tests.evaluation.schemas import EvaluationReport, FailureRecord, Scenario, Violation


ISSUE_STATUSES = frozenset({"open", "verifying", "resolved"})
ISSUE_ID_PATTERN = re.compile(r"issue-[0-9a-f]{24}\Z")
HISTORY_LIMIT = 256

_SCHEMA_VERSION = 1
_LOCK_RETRIES = 100
_LOCK_RETRY_DELAY_SECONDS = 0.01
_STALE_LOCK_SECONDS = 30.0
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


class IssueRegistry:
    def __init__(self, evaluation_root: str | os.PathLike[str]) -> None:
        self.evaluation_root = Path(evaluation_root).absolute()
        self.issues_root = self.evaluation_root / "issues"
        self.status_directories = {
            status: self.issues_root / status for status in sorted(ISSUE_STATUSES)
        }
        self.index_path = self.issues_root / "index.json"
        self._lock_path = self.issues_root / ".registry.lock"
        self._create_layout()
        with self._locked():
            self._rebuild_index()

    def _create_layout(self) -> None:
        paths = [
            self.evaluation_root,
            self.issues_root,
            *(self.status_directories[status] for status in sorted(ISSUE_STATUSES)),
        ]
        for path in paths:
            if _is_link_or_reparse_point(path):
                raise ValueError("issue registry path must not contain links or reparse points")
            path.mkdir(parents=True, exist_ok=True)
            if _is_link_or_reparse_point(path):
                raise ValueError("issue registry path must not contain links or reparse points")
        self._assert_layout()

    def _assert_layout(self) -> None:
        paths = [
            self.evaluation_root,
            self.issues_root,
            *(self.status_directories[status] for status in sorted(ISSUE_STATUSES)),
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

        for path in (self.index_path, self._lock_path):
            if _is_link_or_reparse_point(path):
                raise ValueError("issue registry files must not be links or reparse points")
            if path.resolve().parent != resolved_issues:
                raise ValueError("issue registry file escapes issues root")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._assert_layout()
        descriptor: int | None = None
        for attempt in range(_LOCK_RETRIES):
            try:
                descriptor = os.open(
                    self._lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if _is_link_or_reparse_point(self._lock_path):
                    raise ValueError("registry lock must not be a link or reparse point")
                try:
                    stale = time.time() - self._lock_path.stat().st_mtime > _STALE_LOCK_SECONDS
                except FileNotFoundError:
                    continue
                if stale:
                    try:
                        self._lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if attempt + 1 == _LOCK_RETRIES:
                    raise TimeoutError("timed out waiting for issue registry lock")
                time.sleep(_LOCK_RETRY_DELAY_SECONDS)
            else:
                break
        if descriptor is None:
            raise TimeoutError("timed out waiting for issue registry lock")
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            yield
        finally:
            try:
                self._lock_path.unlink()
            except FileNotFoundError:
                pass

    def _issue_path(self, issue_id: str, status: str) -> Path:
        _validate_issue_id(issue_id)
        if status not in ISSUE_STATUSES:
            raise ValueError("invalid issue status")
        directory = self.status_directories[status]
        path = directory / f"{issue_id}.json"
        if path.resolve().parent != directory.resolve():
            raise ValueError("issue path escapes status directory")
        return path

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
        if value["schema_version"] != _SCHEMA_VERSION:
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
            if type(value["scenario_hash"]) is not str or not value["scenario_hash"]:
                raise ValueError("holdout issue must contain a scenario hash")
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
        index = {
            "schema_version": _SCHEMA_VERSION,
            "issues": self._index_entries(preferred_statuses),
        }
        _atomic_write_json(self.index_path, index)

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
        scenario_hash = context.get("scenario_hash")
        if type(scenario_hash) is not str or not scenario_hash:
            raise ValueError("holdout scenario context must contain a hash")
        return scenario_hash

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
    ) -> tuple[str, ...]:
        if not isinstance(report, EvaluationReport):
            raise ValueError("report must be an EvaluationReport")
        if not isinstance(scenario_context, Mapping):
            raise ValueError("scenario_context must be a mapping")
        _parse_observed_at(observed_at)
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

            if not working:
                return ()

            destinations: dict[str, Path] = {}
            newly_created: list[Path] = []
            for issue_id, issue in sorted(working.items()):
                destination = self._issue_path(issue_id, "open")
                destinations[issue_id] = destination
                source = sources[issue_id]
                if source is None or source[1] != destination:
                    newly_created.append(destination)
                _atomic_write_json(destination, issue)

            try:
                self._rebuild_index({issue_id: "open" for issue_id in working})
            except Exception:
                for path in newly_created:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                raise

            for issue_id, source in sources.items():
                if source is not None and source[1] != destinations[issue_id]:
                    source[1].unlink()

        return tuple(sorted(touched))

    def load(self, issue_id: str) -> dict[str, Any]:
        issue_id = _validate_issue_id(issue_id)
        with self._locked():
            found = self._find_issue(issue_id)
            if found is None:
                raise FileNotFoundError(issue_id)
            return _defensive_copy(found[2])

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
            _atomic_write_json(destination, updated)
            try:
                self._rebuild_index({issue_id: status})
            except Exception:
                try:
                    destination.unlink()
                except FileNotFoundError:
                    pass
                raise
            source.unlink()
            return _defensive_copy(updated)
