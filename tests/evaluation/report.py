from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any

from app.food_terms import expand_terms
from tests.evaluation.issue_registry import (
    _assert_directory_identity,
    _assert_no_link_ancestors,
    _directory_identity,
    _durable_unlink,
    _file_identity,
    _is_link_or_reparse_point,
    _windows_close_handle as _registry_windows_close_handle,
    _windows_handle_identity as _registry_windows_handle_identity,
    _windows_mark_handle_for_deletion as _registry_windows_mark_handle_for_deletion,
)
from tests.evaluation.schemas import EvaluationReport, Scenario


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_STRUCTURE_INTENTS = frozenset(
    {"structure_ratio", "relative_revision", "cooking_diversity"}
)
_WINDOWS_DELETE = 0x00010000
_WINDOWS_GENERIC_WRITE = 0x40000000
_WINDOWS_FILE_READ_ATTRIBUTES = 0x00000080
_WINDOWS_FILE_SHARE_READ = 0x00000001
_WINDOWS_FILE_SHARE_WRITE = 0x00000002
_WINDOWS_FILE_SHARE_DELETE = 0x00000004
_WINDOWS_CREATE_NEW = 1
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_ATTRIBUTE_NORMAL = 0x00000080
_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_RENAME_INFO_CLASS = 3
_WINDOWS_FILE_RENAME_INFORMATION_CLASS = 10
_WINDOWS_ERROR_FILE_EXISTS = 80
_WINDOWS_ERROR_INVALID_PARAMETER = 87


class _WindowsFileRenameInformation(ctypes.Structure):
    _fields_ = (
        ("ReplaceIfExists", wintypes.BOOLEAN),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    )


class _WindowsIoStatusBlock(ctypes.Structure):
    _fields_ = (
        ("Status", ctypes.c_ssize_t),
        ("Information", ctypes.c_size_t),
    )


def _string_set(value: object) -> set[str]:
    if type(value) is not list:
        return set()
    return {item for item in value if type(item) is str}


def _score(counts: Mapping[str, int]) -> dict[str, int | float]:
    tp = counts["tp"]
    fp = counts["fp"]
    fn = counts["fn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _add_sets(counts: dict[str, int], expected: set[str], actual: set[str]) -> None:
    counts["tp"] += len(expected & actual)
    counts["fp"] += len(actual - expected)
    counts["fn"] += len(expected - actual)


def _add_exact(counts: dict[str, int], expected: object, actual: object) -> None:
    if actual == expected:
        counts["tp"] += 1
    else:
        counts["fp"] += 1
        counts["fn"] += 1


def _response_health_fields(response: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    constraints = response.get("constraints")
    if type(constraints) is not dict:
        return set(), set(), set()
    inferred = constraints.get("inferred_profile")
    inferred = inferred if type(inferred) is dict else {}
    user = response.get("user")
    official_groups = (
        _string_set(user.get("特殊人群")) if type(user) is dict else set()
    )
    special_groups = _string_set(inferred.get("special_groups")) | official_groups
    allergens = _string_set(constraints.get("allergens")) | _string_set(
        inferred.get("allergens")
    )
    goals = _string_set(constraints.get("health_goals"))
    return special_groups, set(expand_terms(sorted(allergens))), goals


def _explicit_structure_signal(
    scenario: Scenario, response: Mapping[str, Any]
) -> bool:
    constraints = response.get("constraints")
    if type(constraints) is not dict:
        return False
    if constraints.get("structure_intent") == scenario.intent:
        return True
    if scenario.intent == "structure_ratio":
        actual_meat = constraints.get("requested_meat_count")
        if actual_meat is None:
            actual_meat = constraints.get("meat_count")
        actual_vegetable = constraints.get("requested_vegetable_count")
        if actual_vegetable is None:
            actual_vegetable = constraints.get("vegetable_count")
        expected = {
            "meat_count": scenario.expectation.meat_count,
            "vegetable_count": scenario.expectation.vegetable_count,
        }
        required = {key: value for key, value in expected.items() if value is not None}
        if not required:
            return (
                constraints.get("clarification_required") is True
                or response.get("clarification_required") is True
            )
        actual = {
            "meat_count": actual_meat,
            "vegetable_count": actual_vegetable,
        }
        return all(actual[key] == value for key, value in required.items())
    if scenario.intent == "cooking_diversity":
        expected_minimum = scenario.expectation.minimum_cooking_methods
        actual_minimum = constraints.get("minimum_cooking_methods")
        return (
            type(expected_minimum) is int
            and type(actual_minimum) is int
            and actual_minimum >= expected_minimum
        )
    if scenario.intent == "relative_revision":
        changes = response.get("changes")
        return constraints.get("preserve_unaffected") is True or (
            type(changes) is dict
            and changes.get("mode") == "minimal_revision"
            and type(changes.get("kept_dishes")) is list
            and bool(changes["kept_dishes"])
        )
    return any(
        constraints.get(key) is not None
        for key in (
            "meat_count",
            "vegetable_count",
            "requested_meat_count",
            "requested_vegetable_count",
            "minimum_cooking_methods",
            "structure_intent",
        )
    ) or constraints.get("preserve_unaffected") is True


def compute_reviewed_metrics(
    rows: Iterable[
        tuple[Scenario, Mapping[str, Any]]
        | tuple[Scenario, Mapping[str, Any], object]
    ],
) -> dict[str, dict[str, int | float]]:
    names = (
        "special_groups",
        "allergens",
        "health_goals",
        "dish_count",
        "people_count",
        "structure_intent",
    )
    counts = {name: {"tp": 0, "fp": 0, "fn": 0} for name in names}

    for row in rows:
        scenario, response = row[0], row[1]
        actual_groups, actual_allergens, actual_goals = _response_health_fields(response)
        _add_sets(counts["special_groups"], set(scenario.persona.special_groups), actual_groups)
        _add_sets(
            counts["allergens"],
            set(expand_terms(list(scenario.persona.allergens))),
            actual_allergens,
        )
        _add_sets(
            counts["health_goals"],
            set(scenario.persona.health_goals),
            actual_goals,
        )

        menu = response.get("menu")
        if scenario.expectation.dish_count is not None:
            actual_count = len(menu) if type(menu) is list else None
            _add_exact(counts["dish_count"], scenario.expectation.dish_count, actual_count)

        if scenario.intent == "multi_person_conflict":
            constraints = response.get("constraints")
            actual_people = (
                constraints.get("people_count") if type(constraints) is dict else None
            )
            _add_exact(counts["people_count"], 2, actual_people)

        expected_structure = scenario.intent in _STRUCTURE_INTENTS
        actual_structure = _explicit_structure_signal(scenario, response)
        if expected_structure and actual_structure:
            counts["structure_intent"]["tp"] += 1
        elif actual_structure:
            counts["structure_intent"]["fp"] += 1
        elif expected_structure:
            counts["structure_intent"]["fn"] += 1

    overall = {
        key: sum(counts[name][key] for name in names)
        for key in ("tp", "fp", "fn")
    }
    return {
        **{name: _score(counts[name]) for name in names},
        "overall": _score(overall),
    }


def _json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"


def _write_descriptor(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("report write made no progress")
        offset += written


def _validate_posix_parent_descriptor(
    descriptor: int,
    expected: tuple[int, int, Path],
) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != expected[:2]
    ):
        raise ValueError("report directory changed during file operation")


def _posix_destination_is_link(parent_descriptor: int, name: str) -> bool:
    try:
        metadata = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode)


def _cleanup_posix_temp(
    parent_descriptor: int,
    name: str,
    identity: tuple[int, int] | None,
) -> None:
    if identity is None:
        return
    try:
        metadata = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if (
        stat.S_ISREG(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino) == identity
    ):
        os.unlink(name, dir_fd=parent_descriptor)


def _atomic_write_text_posix(
    path: Path,
    content: bytes,
    parent_identity: tuple[int, int, Path],
) -> None:
    _assert_directory_identity(path.parent, parent_identity)
    if _is_link_or_reparse_point(path):
        raise ValueError("report destination must not be a link or reparse point")
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    parent_flags |= getattr(os, "O_NOFOLLOW", 0)
    parent_descriptor = os.open(path.parent, parent_flags)
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    temporary_identity: tuple[int, int] | None = None
    renamed = False
    try:
        _validate_posix_parent_descriptor(parent_descriptor, parent_identity)
        if _posix_destination_is_link(parent_descriptor, path.name):
            raise ValueError("report destination must not be a link or reparse point")
        for _ in range(100):
            candidate = f".{path.name}.{secrets.token_hex(8)}.tmp"
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            flags |= getattr(os, "O_BINARY", 0)
            try:
                temporary_descriptor = os.open(
                    candidate,
                    flags,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        if temporary_descriptor is None or temporary_name is None:
            raise FileExistsError("could not allocate report temporary file")
        metadata = os.fstat(temporary_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("report temporary path must be a regular file")
        temporary_identity = (metadata.st_dev, metadata.st_ino)
        _validate_posix_parent_descriptor(parent_descriptor, parent_identity)
        _write_descriptor(temporary_descriptor, content)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        _validate_posix_parent_descriptor(parent_descriptor, parent_identity)
        if _posix_destination_is_link(parent_descriptor, path.name):
            raise ValueError("report destination must not be a link or reparse point")
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        renamed = True
        os.fsync(parent_descriptor)
        _validate_posix_parent_descriptor(parent_descriptor, parent_identity)
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        if not renamed and temporary_name is not None:
            _cleanup_posix_temp(
                parent_descriptor,
                temporary_name,
                temporary_identity,
            )
        os.close(parent_descriptor)


def _windows_create_file(
    path: Path,
    desired_access: int,
    creation_disposition: int,
    flags: int,
) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        desired_access,
        _WINDOWS_FILE_SHARE_READ
        | _WINDOWS_FILE_SHARE_WRITE
        | _WINDOWS_FILE_SHARE_DELETE,
        None,
        creation_disposition,
        flags,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle)


def _windows_close_report_handle(handle: int) -> None:
    _registry_windows_close_handle(handle)


def _windows_parent_handle_identity(handle: int) -> tuple[int, int]:
    identity, attributes = _registry_windows_handle_identity(handle)
    if attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("report directory handle must not be a reparse point")
    if not attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
        raise ValueError("report directory handle must reference a directory")
    return identity


def _windows_regular_handle_identity(handle: int) -> tuple[int, int]:
    identity, attributes = _registry_windows_handle_identity(handle)
    if attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("report temporary handle must not be a reparse point")
    if attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
        raise ValueError("report temporary handle must reference a regular file")
    return identity


def _windows_open_report_directory(
    path: Path,
    expected_identity: tuple[int, int, Path],
) -> int:
    handle = _windows_create_file(
        path,
        _WINDOWS_FILE_READ_ATTRIBUTES,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT
        | _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS,
    )
    try:
        if _windows_parent_handle_identity(handle) != expected_identity[:2]:
            raise ValueError("report directory handle identity changed")
        return handle
    except BaseException:
        _windows_close_report_handle(handle)
        raise


def _windows_create_report_temp(path: Path) -> int:
    return _windows_create_file(
        path,
        _WINDOWS_GENERIC_WRITE
        | _WINDOWS_FILE_READ_ATTRIBUTES
        | _WINDOWS_DELETE,
        _WINDOWS_CREATE_NEW,
        _WINDOWS_FILE_ATTRIBUTE_NORMAL,
    )


def _windows_write_report_handle(handle: int, content: bytes) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    write_file = kernel32.WriteFile
    write_file.argtypes = (
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    write_file.restype = wintypes.BOOL
    offset = 0
    while offset < len(content):
        written = wintypes.DWORD()
        chunk = content[offset : offset + 65536]
        if not write_file(
            handle,
            chunk,
            len(chunk),
            ctypes.byref(written),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value == 0:
            raise OSError("report write made no progress")
        offset += written.value


def _windows_flush_report_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flush = kernel32.FlushFileBuffers
    flush.argtypes = (wintypes.HANDLE,)
    flush.restype = wintypes.BOOL
    if not flush(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_rename_information(
    parent_handle: int,
    destination_name: str,
) -> tuple[ctypes.Array[ctypes.c_char], int]:
    encoded_name = destination_name.encode("utf-16-le")
    size = _WindowsFileRenameInformation.FileName.offset + len(encoded_name)
    buffer = ctypes.create_string_buffer(size)
    information = ctypes.cast(
        buffer,
        ctypes.POINTER(_WindowsFileRenameInformation),
    ).contents
    information.ReplaceIfExists = True
    information.RootDirectory = parent_handle
    information.FileNameLength = len(encoded_name)
    ctypes.memmove(
        ctypes.addressof(buffer) + _WindowsFileRenameInformation.FileName.offset,
        encoded_name,
        len(encoded_name),
    )
    return buffer, size


def _windows_nt_rename_report_handle(
    handle: int,
    information: ctypes.Array[ctypes.c_char],
    size: int,
) -> None:
    ntdll = ctypes.WinDLL("ntdll")
    rename = ntdll.NtSetInformationFile
    rename.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsIoStatusBlock),
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.INT,
    )
    rename.restype = ctypes.c_long
    status_block = _WindowsIoStatusBlock()
    status = rename(
        handle,
        ctypes.byref(status_block),
        information,
        size,
        _WINDOWS_FILE_RENAME_INFORMATION_CLASS,
    )
    if status < 0:
        rtl_status_to_error = ntdll.RtlNtStatusToDosError
        rtl_status_to_error.argtypes = (ctypes.c_long,)
        rtl_status_to_error.restype = wintypes.ULONG
        raise ctypes.WinError(rtl_status_to_error(status))


def _windows_rename_report_handle(
    handle: int,
    parent_handle: int,
    destination_name: str,
) -> None:
    information, size = _windows_rename_information(
        parent_handle,
        destination_name,
    )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    rename = kernel32.SetFileInformationByHandle
    rename.argtypes = (
        wintypes.HANDLE,
        wintypes.INT,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    rename.restype = wintypes.BOOL
    if rename(
        handle,
        _WINDOWS_FILE_RENAME_INFO_CLASS,
        information,
        size,
    ):
        return
    error = ctypes.get_last_error()
    if error != _WINDOWS_ERROR_INVALID_PARAMETER:
        raise ctypes.WinError(error)
    _windows_nt_rename_report_handle(handle, information, size)


def _windows_delete_report_handle(handle: int) -> None:
    _registry_windows_mark_handle_for_deletion(handle)


def _atomic_write_text_windows(
    path: Path,
    content: bytes,
    parent_identity: tuple[int, int, Path],
) -> None:
    _assert_directory_identity(path.parent, parent_identity)
    if _is_link_or_reparse_point(path):
        raise ValueError("report destination must not be a link or reparse point")
    parent_handle: int | None = None
    temporary_handle: int | None = None
    temporary_path: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    renamed = False
    primary_error: BaseException | None = None
    try:
        parent_handle = _windows_open_report_directory(
            path.parent,
            parent_identity,
        )
        _assert_directory_identity(path.parent, parent_identity)
        if _windows_parent_handle_identity(parent_handle) != parent_identity[:2]:
            raise ValueError("report directory handle identity changed")
        for _ in range(100):
            temporary_path = path.parent / (
                f".{path.name}.{secrets.token_hex(8)}.tmp"
            )
            try:
                temporary_handle = _windows_create_report_temp(temporary_path)
            except OSError as error:
                if getattr(error, "winerror", None) == _WINDOWS_ERROR_FILE_EXISTS:
                    continue
                raise
            break
        if temporary_handle is None or temporary_path is None:
            raise FileExistsError("could not allocate report temporary file")
        temporary_identity = _windows_regular_handle_identity(temporary_handle)
        _assert_directory_identity(path.parent, parent_identity)
        if _windows_parent_handle_identity(parent_handle) != parent_identity[:2]:
            raise ValueError("report directory handle identity changed")
        if _file_identity(temporary_path) != temporary_identity:
            raise ValueError("report temporary entry identity changed")
        _windows_write_report_handle(temporary_handle, content)
        _windows_flush_report_handle(temporary_handle)
        if _windows_regular_handle_identity(temporary_handle) != temporary_identity:
            raise ValueError("report temporary handle identity changed")
        _assert_directory_identity(path.parent, parent_identity)
        if _windows_parent_handle_identity(parent_handle) != parent_identity[:2]:
            raise ValueError("report directory handle identity changed")
        if _is_link_or_reparse_point(path):
            raise ValueError("report destination must not be a link or reparse point")
        _windows_rename_report_handle(
            temporary_handle,
            parent_handle,
            path.name,
        )
        renamed = True
        if _windows_regular_handle_identity(temporary_handle) != temporary_identity:
            raise ValueError("report destination handle identity changed")
        if _windows_parent_handle_identity(parent_handle) != parent_identity[:2]:
            raise ValueError("report directory handle identity changed")
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_error: BaseException | None = None
        if temporary_handle is not None and not renamed:
            try:
                _windows_delete_report_handle(temporary_handle)
            except BaseException as error:
                cleanup_error = error
        if temporary_handle is not None:
            try:
                _windows_close_report_handle(temporary_handle)
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
        if parent_handle is not None:
            try:
                _windows_close_report_handle(parent_handle)
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error


def _atomic_write_text(
    path: Path,
    content: str,
    parent_identity: tuple[int, int, Path],
) -> None:
    encoded = content.encode("utf-8")
    if os.name == "nt":
        _atomic_write_text_windows(path, encoded, parent_identity)
    else:
        _atomic_write_text_posix(path, encoded, parent_identity)


def _write_json(
    path: Path,
    value: object,
    parent_identity: tuple[int, int, Path],
) -> None:
    _atomic_write_text(path, _json_text(value), parent_identity)


def _prepare_report_directories(
    output: Path,
) -> tuple[tuple[int, int, Path], Path, tuple[int, int, Path]]:
    _assert_no_link_ancestors(output)
    output.mkdir(parents=True, exist_ok=True)
    _assert_no_link_ancestors(output)
    output_identity = _directory_identity(output)

    failures = output / "failures"
    _assert_directory_identity(output, output_identity)
    if _is_link_or_reparse_point(failures):
        raise ValueError("report failures path must not be a link or reparse point")
    failures.mkdir(exist_ok=True)
    _assert_directory_identity(output, output_identity)
    _assert_no_link_ancestors(failures)
    failures_identity = _directory_identity(failures)
    if failures_identity[2].parent != output_identity[2]:
        raise ValueError("report failures path escapes output directory")
    _assert_directory_identity(failures, failures_identity)
    return output_identity, failures, failures_identity


def _clean_failure_json(
    failures: Path,
    failures_identity: tuple[int, int, Path],
) -> None:
    _assert_directory_identity(failures, failures_identity)
    for path in sorted(failures.iterdir(), key=lambda item: item.name):
        _assert_directory_identity(failures, failures_identity)
        if path.suffix != ".json":
            continue
        if _is_link_or_reparse_point(path):
            continue
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            continue
        identity = _file_identity(path)
        _assert_directory_identity(failures, failures_identity)
        _durable_unlink(
            path,
            expected_parent_identity=failures_identity,
            expected_file_identity=identity,
        )
        _assert_directory_identity(failures, failures_identity)


def safe_scenario_filename(scenario_id: str) -> str:
    safe = _SAFE_NAME_RE.sub("_", scenario_id)
    while ".." in safe:
        safe = safe.replace("..", "__")
    safe = safe.lstrip(".")
    safe = (safe or "scenario")[:80]
    digest = hashlib.sha256(scenario_id.encode("utf-8")).hexdigest()[:12]
    return f"{safe}-{digest}"


def summarize_violation_counts(report: EvaluationReport) -> dict[str, dict[str, int]]:
    by_severity: Counter[str] = Counter()
    by_code: Counter[str] = Counter()
    for failure in report.failures:
        for violation in failure.violations:
            by_severity[violation.severity] += 1
            by_code[violation.code] += 1
    return {
        "by_severity": dict(sorted(by_severity.items())),
        "by_code": dict(sorted(by_code.items())),
    }


def _strip_session_ids(value: object) -> object:
    if type(value) is dict:
        return {
            key: _strip_session_ids(item)
            for key, item in value.items()
            if key != "session_id"
        }
    if type(value) in (list, tuple):
        return [_strip_session_ids(item) for item in value]
    return value


def _holdout_failure_payload(
    scenario_hash: str, codes: Mapping[str, int]
) -> dict[str, object]:
    return {
        "holdout": True,
        "scenario_hash": scenario_hash,
        "violation_codes": dict(sorted(codes.items())),
    }


def write_report(
    report: EvaluationReport,
    output_dir: Path | str,
    *,
    metadata: Mapping[str, Any] | None = None,
    intermediates: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    minimizations: Mapping[str, Mapping[str, Any]] | None = None,
    source_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    scenario_context: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Path]:
    output = Path(output_dir)
    output_identity, failures_dir, failures_identity = _prepare_report_directories(
        output
    )
    _clean_failure_json(failures_dir, failures_identity)
    metadata = dict(metadata or {})
    intermediates = intermediates or {}
    minimizations = minimizations or {}
    source_metadata = source_metadata or {}
    contexts_supplied = scenario_context is not None
    scenario_context = scenario_context or {}

    base = report.to_dict()
    sanitized_failures: list[dict[str, Any]] = []
    for failure in report.failures:
        source = source_metadata.get(failure.scenario_id, {})
        if source.get("holdout") is True:
            code_counts = Counter(violation.code for violation in failure.violations)
            sanitized_failures.append(
                _holdout_failure_payload(str(source["scenario_hash"]), code_counts)
            )
        else:
            sanitized_failures.append(failure.to_dict())
    base["failures"] = sanitized_failures
    for key, value in metadata.items():
        if key in EvaluationReport._FIELDS:
            raise ValueError(f"metadata must not override report field {key!r}")
        base[key] = value
    metric_counts = base["metrics"].get("violation_counts")
    base["violation_counts"] = (
        metric_counts
        if type(metric_counts) is dict
        else summarize_violation_counts(report)
    )

    summary_path = output / "summary.json"
    markdown_path = output / "summary.md"
    coverage_path = output / "coverage.json"
    _write_json(summary_path, _strip_session_ids(base), output_identity)
    _write_json(coverage_path, report.to_dict()["coverage"], output_identity)

    blocking = report.total - report.passed
    markdown = (
        "# Evaluation Summary\n\n"
        "| Metric | Value |\n"
        "| --- | ---: |\n"
        f"| Total | {report.total} |\n"
        f"| Passed | {report.passed} |\n"
        f"| Blocking | {blocking} |\n"
        f"| P50 (ms) | {report.timings.get('p50_ms', 0)} |\n"
        f"| P95 (ms) | {report.timings.get('p95_ms', 0)} |\n"
    )
    _atomic_write_text(markdown_path, markdown, output_identity)

    for failure in report.failures:
        source = source_metadata.get(failure.scenario_id, {})
        if source.get("holdout") is True:
            scenario_hash = str(source["scenario_hash"])
            filename = safe_scenario_filename(f"holdout-{scenario_hash}") + ".json"
            code_counts = Counter(violation.code for violation in failure.violations)
            payload: dict[str, Any] = _holdout_failure_payload(
                scenario_hash, code_counts
            )
        else:
            filename = safe_scenario_filename(failure.scenario_id) + ".json"
            payload = failure.to_dict()
            payload["minimized"] = failure.original_messages != failure.minimized_messages
            payload["minimization"] = dict(
                minimizations.get(
                    failure.scenario_id,
                    {"attempts": 0, "reached_cap": False},
                )
            )
            payload["intermediates"] = list(intermediates.get(failure.scenario_id, ()))
            if contexts_supplied:
                context = scenario_context.get(failure.scenario_id)
                if not isinstance(context, Mapping):
                    raise ValueError(
                        f"missing scenario context for {failure.scenario_id}"
                    )
                payload["scenario_context"] = dict(context)
        _write_json(
            failures_dir / filename,
            _strip_session_ids(payload),
            failures_identity,
        )

    return {
        "summary_json": summary_path,
        "summary_markdown": markdown_path,
        "coverage_json": coverage_path,
        "failures_dir": failures_dir,
    }
