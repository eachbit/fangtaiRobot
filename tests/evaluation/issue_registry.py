from __future__ import annotations

import ctypes
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
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tests.evaluation.schemas import EvaluationReport, FailureRecord, Scenario, Violation


ISSUE_STATUSES = frozenset({"open", "verifying", "resolved"})
ISSUE_ID_PATTERN = re.compile(r"issue-[0-9a-f]{24}\Z")
HISTORY_LIMIT = 256

_FAILURE_FILE_ATTACHMENTS = frozenset(
    {"minimized", "minimization", "intermediates", "scenario_context"}
)
_PUBLIC_CONTEXT_FIELDS = frozenset(
    {"holdout", "health_bucket", "intent", "expectation", "scenario"}
)

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
_WINDOWS_DELETE = 0x00010000
_WINDOWS_GENERIC_READ = 0x80000000
_WINDOWS_FILE_READ_ATTRIBUTES = 0x00000080
_WINDOWS_FILE_SHARE_READ = 0x00000001
_WINDOWS_FILE_SHARE_WRITE = 0x00000002
_WINDOWS_FILE_SHARE_DELETE = 0x00000004
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_DISPOSITION_INFO_CLASS = 4
_WINDOWS_FILE_DISPOSITION_INFO_EX_CLASS = 21
_WINDOWS_FILE_DISPOSITION_FLAG_DELETE = 0x00000001
_WINDOWS_FILE_DISPOSITION_FLAG_POSIX_SEMANTICS = 0x00000002
_WINDOWS_FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE = 0x00000010
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


class _WindowsByHandleFileInformation(ctypes.Structure):
    _fields_ = (
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    )


class _WindowsFileDispositionInformation(ctypes.Structure):
    _fields_ = (("DeleteFile", wintypes.BOOLEAN),)


class _WindowsFileDispositionInformationEx(ctypes.Structure):
    _fields_ = (("Flags", wintypes.DWORD),)


@dataclass
class _TransactionIdentities:
    parents: dict[Path, tuple[int, int, Path]]
    files: dict[Path, tuple[int, int] | None]


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
    required: Any = None,
) -> list[Any]:
    values = {*existing, value}
    if required is not None:
        values.add(required)
    if limit is None:
        return sorted(values)
    if required is None:
        return sorted(values)[-limit:]
    values.discard(required)
    retained_count = max(limit - 1, 0)
    retained = sorted(values)[-retained_count:] if retained_count else []
    return sorted([*retained, required])


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


def _directory_identity(path: Path) -> tuple[int, int, Path]:
    _assert_no_link_ancestors(path)
    metadata = path.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or (reparse_flag and attributes & reparse_flag):
        raise ValueError("issue registry path must not contain links or reparse points")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("issue registry path must be a directory")
    resolved = path.resolve()
    current = path.lstat()
    current_attributes = getattr(current, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(current.st_mode)
        or (reparse_flag and current_attributes & reparse_flag)
        or not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
    ):
        raise ValueError("issue registry directory changed during identity check")
    return metadata.st_dev, metadata.st_ino, resolved


def _assert_directory_identity(
    path: Path,
    expected: tuple[int, int, Path],
) -> None:
    try:
        actual = _directory_identity(path)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ValueError("issue registry directory changed during file operation") from exc
    if actual != expected:
        raise ValueError("issue registry directory changed during file operation")


def _is_unsupported_windows_directory_sync(error: OSError) -> bool:
    if os.name != "nt":
        return False
    return error.errno in {
        errno.EACCES,
        errno.EINVAL,
        getattr(errno, "ENOTSUP", errno.EINVAL),
    } or getattr(error, "winerror", None) in {1, 5, 50}


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if _is_unsupported_windows_directory_sync(error):
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if not _is_unsupported_windows_directory_sync(error):
                raise
    finally:
        os.close(descriptor)


def _windows_open_file_handle(path: Path, desired_access: int) -> int:
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
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle)


def _windows_open_delete_handle(path: Path) -> int:
    return _windows_open_file_handle(
        path,
        _WINDOWS_DELETE | _WINDOWS_FILE_READ_ATTRIBUTES,
    )


def _windows_close_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    if not close_handle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_handle_identity(handle: int) -> tuple[tuple[int, int], int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsByHandleFileInformation),
    )
    get_information.restype = wintypes.BOOL
    information = _WindowsByHandleFileInformation()
    if not get_information(handle, ctypes.byref(information)):
        raise ctypes.WinError(ctypes.get_last_error())
    file_index = (int(information.nFileIndexHigh) << 32) | int(
        information.nFileIndexLow
    )
    return (
        (int(information.dwVolumeSerialNumber), file_index),
        int(information.dwFileAttributes),
    )


def _windows_read_handle(handle: int) -> bytes:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    read_file = kernel32.ReadFile
    read_file.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    read_file.restype = wintypes.BOOL
    chunks: list[bytes] = []
    buffer = ctypes.create_string_buffer(65536)
    while True:
        bytes_read = wintypes.DWORD()
        if not read_file(
            handle,
            buffer,
            len(buffer),
            ctypes.byref(bytes_read),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if bytes_read.value == 0:
            return b"".join(chunks)
        chunks.append(buffer.raw[: bytes_read.value])


def _windows_mark_handle_for_deletion(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        wintypes.INT,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    basic = _WindowsFileDispositionInformation(True)
    if set_information(
        handle,
        _WINDOWS_FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(basic),
        ctypes.sizeof(basic),
    ):
        return
    extended = _WindowsFileDispositionInformationEx(
        _WINDOWS_FILE_DISPOSITION_FLAG_DELETE
        | _WINDOWS_FILE_DISPOSITION_FLAG_POSIX_SEMANTICS
        | _WINDOWS_FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE
    )
    if set_information(
        handle,
        _WINDOWS_FILE_DISPOSITION_INFO_EX_CLASS,
        ctypes.byref(extended),
        ctypes.sizeof(extended),
    ):
        return
    raise ctypes.WinError(ctypes.get_last_error())


def _validate_windows_file_information(
    identity: tuple[int, int],
    attributes: int,
) -> tuple[int, int]:
    if attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("transaction file must not be a reparse point")
    if attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
        raise ValueError("transaction path must be a regular file")
    return identity


def _windows_file_identity(path: Path) -> tuple[int, int]:
    handle = _windows_open_file_handle(path, _WINDOWS_FILE_READ_ATTRIBUTES)
    try:
        identity, attributes = _windows_handle_identity(handle)
        return _validate_windows_file_information(identity, attributes)
    finally:
        _windows_close_handle(handle)


def _read_windows_failure_file(
    path: Path,
    parent_identity: tuple[int, int, Path],
    expected_file_identity: tuple[int, int],
) -> bytes:
    _assert_directory_identity(path.parent, parent_identity)
    handle = _windows_open_file_handle(
        path,
        _WINDOWS_GENERIC_READ | _WINDOWS_FILE_READ_ATTRIBUTES,
    )
    try:
        identity, attributes = _windows_handle_identity(handle)
        actual_identity = _validate_windows_file_information(identity, attributes)
        if actual_identity != expected_file_identity:
            raise ValueError("failure file changed before read")
        data = _windows_read_handle(handle)
        final_identity, final_attributes = _windows_handle_identity(handle)
        validated_final = _validate_windows_file_information(
            final_identity,
            final_attributes,
        )
        if validated_final != actual_identity:
            raise ValueError("failure file changed during read")
        return data
    finally:
        _windows_close_handle(handle)


def _windows_delete_by_handle(
    path: Path,
    expected_file_identity: tuple[int, int],
) -> None:
    handle = _windows_open_delete_handle(path)
    try:
        identity, attributes = _windows_handle_identity(handle)
        actual_identity = _validate_windows_file_information(identity, attributes)
        if actual_identity != expected_file_identity:
            raise ValueError("transaction file changed before deletion")
        _windows_mark_handle_for_deletion(handle)
    finally:
        _windows_close_handle(handle)


def _file_identity(path: Path) -> tuple[int, int]:
    if os.name == "nt":
        return _windows_file_identity(path)
    metadata = path.lstat()
    if _is_link_or_reparse_point(path) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("transaction path must be a regular file")
    return metadata.st_dev, metadata.st_ino


def _durable_unlink(
    path: Path,
    *,
    expected_parent_identity: tuple[int, int, Path] | None = None,
    expected_file_identity: tuple[int, int] | None = None,
) -> None:
    parent = path.parent
    if os.name == "nt":
        if expected_parent_identity is not None:
            _assert_directory_identity(parent, expected_parent_identity)
        file_identity = expected_file_identity or _file_identity(path)
        _windows_delete_by_handle(path, file_identity)
        if expected_parent_identity is not None:
            _assert_directory_identity(parent, expected_parent_identity)
            _fsync_directory(parent)
        elif not _is_link_or_reparse_point(parent):
            _fsync_directory(parent)
        return
    if expected_parent_identity is None:
        path.unlink()
        _fsync_directory(parent)
        return
    parent_identity = expected_parent_identity
    _assert_directory_identity(parent, parent_identity)
    if os.unlink in os.supports_dir_fd:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(parent, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != parent_identity[:2]
            ):
                raise ValueError("unlink parent directory changed before deletion")
            target_metadata = os.stat(
                path.name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            file_identity = expected_file_identity or (
                target_metadata.st_dev,
                target_metadata.st_ino,
            )
            if (
                not stat.S_ISREG(target_metadata.st_mode)
                or (target_metadata.st_dev, target_metadata.st_ino) != file_identity
            ):
                raise ValueError("transaction file changed before deletion")
            os.unlink(path.name, dir_fd=descriptor)
        finally:
            os.close(descriptor)
    else:
        raise OSError(errno.ENOTSUP, "safe relative unlink is unavailable")
    _assert_directory_identity(parent, parent_identity)
    _fsync_directory(path.parent)


def _unlink_if_identity(path: Path, identity: tuple[int, int]) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        not _is_link_or_reparse_point(path)
        and stat.S_ISREG(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino) == identity
    ):
        _durable_unlink(path)


def _read_regular_file_bytes(
    path: Path,
    parent_identity: tuple[int, int, Path],
) -> bytes:
    _assert_directory_identity(path.parent, parent_identity)
    if _is_link_or_reparse_point(path):
        raise ValueError("file must not be a link or reparse point")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ValueError("file changed during read") from exc
    try:
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise ValueError("path must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)

    _assert_directory_identity(path.parent, parent_identity)
    try:
        current_metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError("file changed during read") from exc
    if (
        _is_link_or_reparse_point(path)
        or not stat.S_ISREG(current_metadata.st_mode)
        or (current_metadata.st_dev, current_metadata.st_ino)
        != (opened_metadata.st_dev, opened_metadata.st_ino)
    ):
        raise ValueError("file changed during read")
    return b"".join(chunks)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    parent_identity = _directory_identity(path.parent)
    if _is_link_or_reparse_point(path):
        raise ValueError("atomic write destination must not be a link or reparse point")
    temporary_path: Path | None = None
    temporary_identity: tuple[int, int] | None = None
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
            metadata = os.fstat(temporary.fileno())
            temporary_identity = (metadata.st_dev, metadata.st_ino)
        _assert_directory_identity(path.parent, parent_identity)
        if _is_link_or_reparse_point(path):
            raise ValueError("atomic write destination must not be a link or reparse point")
        os.replace(temporary_path, path)
        try:
            _assert_directory_identity(path.parent, parent_identity)
            metadata = path.lstat()
            if (
                _is_link_or_reparse_point(path)
                or not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != temporary_identity
            ):
                raise ValueError("atomic write destination changed during replace")
            _fsync_directory(path.parent)
            _assert_directory_identity(path.parent, parent_identity)
        except ValueError:
            if temporary_identity is not None:
                _unlink_if_identity(path, temporary_identity)
            raise
    finally:
        if temporary_path is not None and temporary_path.exists():
            if temporary_identity is None:
                _durable_unlink(temporary_path)
            else:
                _unlink_if_identity(temporary_path, temporary_identity)


def _atomic_create_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    parent_identity: tuple[int, int, Path] | None = None,
) -> bool:
    if parent_identity is None:
        parent_identity = _directory_identity(path.parent)
    else:
        _assert_directory_identity(path.parent, parent_identity)
    if _is_link_or_reparse_point(path):
        raise ValueError("atomic create destination must not be a link or reparse point")
    temporary_path: Path | None = None
    temporary_identity: tuple[int, int] | None = None
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
            metadata = os.fstat(temporary.fileno())
            temporary_identity = (metadata.st_dev, metadata.st_ino)
        _assert_directory_identity(path.parent, parent_identity)
        if _is_link_or_reparse_point(path):
            raise ValueError("atomic create destination must not be a link or reparse point")
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            return False
        try:
            _assert_directory_identity(path.parent, parent_identity)
            metadata = path.lstat()
            if (
                _is_link_or_reparse_point(path)
                or not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != temporary_identity
            ):
                raise ValueError("atomic create destination changed during link")
            _fsync_directory(path.parent)
            _assert_directory_identity(path.parent, parent_identity)
        except ValueError:
            if temporary_identity is not None:
                _unlink_if_identity(path, temporary_identity)
            raise
        return True
    finally:
        if temporary_path is not None and temporary_path.exists():
            if temporary_identity is None:
                _durable_unlink(temporary_path)
            else:
                _unlink_if_identity(temporary_path, temporary_identity)


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
        self.candidates_root = self.evaluation_root / "candidates"
        self.regressions_root = self.candidates_root / "regressions"
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
            self.candidates_root,
            self.regressions_root,
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
            self.candidates_root,
            self.regressions_root,
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
        resolved_candidates = self.candidates_root.resolve()
        if resolved_candidates.parent != resolved_root:
            raise ValueError("candidates path escapes evaluation_root")
        if self.regressions_root.resolve().parent != resolved_candidates:
            raise ValueError("regressions path escapes candidates root")

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
        primary_error: BaseException | None = None
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
        except BaseException as error:
            primary_error = error
            raise
        finally:
            release_error: OSError | None = None
            if acquired:
                try:
                    self._release_advisory_lock(descriptor)
                except OSError as error:
                    if not self._is_closed_descriptor(error):
                        release_error = error
            close_error: OSError | None = None
            try:
                os.close(descriptor)
            except OSError as error:
                if not self._is_closed_descriptor(error):
                    close_error = error
            if primary_error is None:
                if close_error is not None:
                    if release_error is not None:
                        raise close_error from release_error
                    raise close_error

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

    @staticmethod
    def _transaction_parent_identities(
        paths: Iterable[Path],
    ) -> dict[Path, tuple[int, int, Path]]:
        parents = sorted({path.parent for path in paths}, key=str)
        return {parent: _directory_identity(parent) for parent in parents}

    @staticmethod
    def _assert_transaction_parent_identities(
        identities: Mapping[Path, tuple[int, int, Path]],
    ) -> None:
        for parent, identity in identities.items():
            _assert_directory_identity(parent, identity)

    def _begin_transaction(
        self,
        paths: Iterable[Path],
    ) -> _TransactionIdentities:
        if self._journal_path.exists() or self._journal_path.is_symlink():
            raise ValueError("an issue registry transaction is already active")
        relative_paths = sorted({self._transaction_relative_path(path) for path in paths})
        if "index.json" not in relative_paths:
            raise ValueError("transaction must include index.json")
        transaction_paths = [
            self._transaction_path(relative_path) for relative_path in relative_paths
        ]
        parent_identities = self._transaction_parent_identities(transaction_paths)
        files = []
        file_identities: dict[Path, tuple[int, int] | None] = {}
        for relative_path, path in zip(relative_paths, transaction_paths, strict=True):
            exists = path.exists() or path.is_symlink()
            identity = _file_identity(path) if exists else None
            before = self._read_transaction_snapshot(relative_path, path)
            if relative_path == "index.json" and before is None:
                raise ValueError("transaction requires an existing index")
            if (before is None) != (identity is None):
                raise ValueError("transaction file changed during snapshot")
            if identity is not None and _file_identity(path) != identity:
                raise ValueError("transaction file changed during snapshot")
            file_identities[path] = identity
            files.append({"path": relative_path, "before": before})
        _atomic_write_json(
            self._journal_path,
            {"schema_version": 1, "files": files},
        )
        self._assert_transaction_parent_identities(parent_identities)
        return _TransactionIdentities(parent_identities, file_identities)

    def _restore_transaction_file(
        self,
        relative_path: str,
        path: Path,
        before: dict[str, Any] | None,
        parent_identity: tuple[int, int, Path],
        file_identity: tuple[int, int] | None,
    ) -> None:
        _assert_directory_identity(path.parent, parent_identity)
        exists = path.exists() or path.is_symlink()
        if exists and _is_link_or_reparse_point(path):
            raise ValueError("transaction file must not be a link or reparse point")
        if before is None:
            if exists:
                _durable_unlink(
                    path,
                    expected_parent_identity=parent_identity,
                    expected_file_identity=file_identity,
                )
            return
        if exists:
            current = self._read_transaction_snapshot(relative_path, path)
            if current == before:
                return
        _atomic_write_json(path, before)
        _assert_directory_identity(path.parent, parent_identity)

    def _cleanup_observation_transaction_temps(
        self,
        entries: list[tuple[str, Path, dict[str, Any] | None]],
        parent_identity: tuple[int, int, Path],
    ) -> None:
        _assert_directory_identity(self.observations_root, parent_identity)
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
            _durable_unlink(
                path,
                expected_parent_identity=parent_identity,
            )

    def _recover_transaction(
        self,
        transaction_identities: _TransactionIdentities | None = None,
    ) -> None:
        if not self._journal_path.exists() and not self._journal_path.is_symlink():
            return
        entries = self._load_transaction_journal()
        transaction_paths = [path for _, path, _ in entries]
        identities = transaction_identities or _TransactionIdentities(
            self._transaction_parent_identities(transaction_paths),
            {path: None for path in transaction_paths},
        )
        required_parents = {path.parent for _, path, _ in entries}
        if not required_parents.issubset(identities.parents):
            raise ValueError("transaction parent identity is missing")
        self._assert_transaction_parent_identities(identities.parents)
        observations_identity = identities.parents.get(self.observations_root)
        if observations_identity is not None:
            self._cleanup_observation_transaction_temps(
                entries,
                observations_identity,
            )
        for relative_path, path, before in sorted(
            entries,
            key=lambda entry: entry[0] == "index.json",
        ):
            self._restore_transaction_file(
                relative_path,
                path,
                before,
                identities.parents[path.parent],
                identities.files.get(path),
            )
        self._assert_transaction_parent_identities(identities.parents)
        self._validate_index_matches_files()
        _durable_unlink(
            self._journal_path,
            expected_parent_identity=identities.parents[self.issues_root],
        )

    @contextmanager
    def _transaction(
        self,
        paths: Iterable[Path],
    ) -> Iterator[_TransactionIdentities]:
        identities = self._begin_transaction(paths)
        try:
            yield identities
            self._assert_transaction_parent_identities(identities.parents)
            self._validate_index_matches_files()
            _durable_unlink(
                self._journal_path,
                expected_parent_identity=identities.parents[self.issues_root],
            )
        except BaseException as error:
            try:
                self._recover_transaction(identities)
            except BaseException as recovery_error:
                raise recovery_error from error
            raise

    def _public_context(
        self,
        context: Mapping[str, Any],
        failure: FailureRecord,
    ) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
        if context.get("holdout") is not False:
            raise ValueError("public scenario context must set holdout to false")
        scenario_payload = context.get("scenario")
        if type(scenario_payload) is not dict:
            raise ValueError("public scenario context must contain a strict Scenario dict")
        scenario = Scenario.from_dict(scenario_payload)
        if scenario.scenario_id != failure.scenario_id:
            raise ValueError("scenario context scenario_id does not match FailureRecord")
        if scenario.seed != failure.seed:
            raise ValueError("scenario context seed does not match FailureRecord")
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

        regression_source, health_bucket, intent, expectation = self._public_context(
            context,
            failure,
        )
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

        regression_source, health_bucket, intent, expectation = self._public_context(
            context,
            failure,
        )
        if expectation != result["expected"]:
            raise ValueError("issue expectation does not match its fingerprint")
        latest = observed >= last_seen
        selected_source = Scenario.from_dict(
            regression_source if latest else result["regression_source"]
        )
        result["scenario_ids"] = _merged_history(
            result["scenario_ids"],
            failure.scenario_id,
            limit=HISTORY_LIMIT,
            required=selected_source.scenario_id,
        )
        result["seeds"] = _merged_history(
            result["seeds"],
            failure.seed,
            limit=HISTORY_LIMIT,
            required=selected_source.seed,
        )
        result["health_buckets"] = _merged_history(result["health_buckets"], health_bucket)
        result["intents"] = _merged_history(result["intents"], intent)
        if latest:
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
            with self._transaction(transaction_paths) as identities:
                for issue_id, issue in sorted(working.items()):
                    _atomic_write_json(destinations[issue_id], issue)
                    identities.files[destinations[issue_id]] = _file_identity(
                        destinations[issue_id]
                    )
                if marker_path is not None and marker is not None:
                    _atomic_write_json(marker_path, marker)
                    identities.files[marker_path] = _file_identity(marker_path)
                for issue_id, source in sources.items():
                    if source is not None and source[1] != destinations[issue_id]:
                        _durable_unlink(
                            source[1],
                            expected_parent_identity=identities.parents[source[1].parent],
                            expected_file_identity=identities.files[source[1]],
                        )
                self._rebuild_index()

        return issue_ids

    def ingest_failure_file(
        self,
        path: str | os.PathLike[str],
        observed_at: str,
        observation_id: str | None = None,
    ) -> tuple[str, ...]:
        if not isinstance(path, (str, os.PathLike)):
            raise ValueError("failure file path must be path-like")
        lexical_path = Path(path)
        if ".." in lexical_path.parts:
            raise ValueError("failure file path must not contain parent traversal parts")
        if not lexical_path.is_absolute():
            lexical_path = Path.cwd() / lexical_path
        failure_path = Path(os.path.abspath(lexical_path))
        _assert_no_link_ancestors(failure_path)
        metadata = failure_path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("failure file path must be a regular file")
        parent_identity = _directory_identity(failure_path.parent)
        _assert_directory_identity(failure_path.parent, parent_identity)
        if _is_link_or_reparse_point(failure_path):
            raise ValueError("failure file must not be a link or reparse point")
        if os.name == "nt":
            expected_file_identity = _file_identity(failure_path)
            _assert_directory_identity(failure_path.parent, parent_identity)
            data = _read_windows_failure_file(
                failure_path,
                parent_identity,
                expected_file_identity,
            )
        else:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(failure_path, flags)
            try:
                opened_metadata = os.fstat(descriptor)
                if not stat.S_ISREG(opened_metadata.st_mode):
                    raise ValueError("failure file path must be a regular file")
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                data = b"".join(chunks)
            finally:
                os.close(descriptor)
        _assert_directory_identity(failure_path.parent, parent_identity)
        return self.ingest_failure_bytes(
            data,
            observed_at=observed_at,
            observation_id=observation_id,
        )

    def ingest_failure_bytes(
        self,
        data: bytes,
        observed_at: str,
        observation_id: str | None = None,
    ) -> tuple[str, ...]:
        if type(data) is not bytes:
            raise ValueError("failure data must be bytes")
        try:
            payload = _strict_json_loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("failure file is not valid strict JSON") from exc
        if type(payload) is not dict:
            raise ValueError("failure file must contain a JSON object")
        _json_value(payload)

        allowed = FailureRecord._FIELDS | _FAILURE_FILE_ATTACHMENTS
        fields = set(payload)
        unexpected = fields - allowed
        if unexpected:
            raise ValueError(
                f"failure file contains unsupported fields: {sorted(unexpected)}"
            )
        missing = (FailureRecord._FIELDS | {"scenario_context"}) - fields
        if missing:
            raise ValueError(
                f"failure file is missing required fields: {sorted(missing)}"
            )

        if "minimized" in payload and type(payload["minimized"]) is not bool:
            raise ValueError("failure file minimized attachment must be boolean")
        if "minimization" in payload:
            minimization = payload["minimization"]
            if type(minimization) is not dict or set(minimization) != {
                "attempts",
                "reached_cap",
            }:
                raise ValueError("failure file minimization attachment is invalid")
            if type(minimization["attempts"]) is not int or minimization["attempts"] < 0:
                raise ValueError("failure file minimization attempts is invalid")
            if type(minimization["reached_cap"]) is not bool:
                raise ValueError("failure file minimization reached_cap is invalid")
        if "intermediates" in payload and type(payload["intermediates"]) is not list:
            raise ValueError("failure file intermediates attachment must be an array")

        context = payload["scenario_context"]
        if type(context) is not dict or set(context) != _PUBLIC_CONTEXT_FIELDS:
            raise ValueError("failure file scenario_context is not a strict public context")
        if context.get("holdout") is not False:
            raise ValueError("holdout failure artifacts cannot be imported")

        failure = FailureRecord.from_dict(
            {field: payload[field] for field in FailureRecord._FIELDS}
        )
        self._public_context(context, failure)
        report = EvaluationReport(1, 0, (failure,), {}, {}, {})
        effective_observation_id = (
            f"failure-file-bytes:{hashlib.sha256(data).hexdigest()}"
            if observation_id is None
            else observation_id
        )
        return self.ingest(
            report,
            {failure.scenario_id: context},
            observed_at=observed_at,
            observation_id=effective_observation_id,
        )

    def load(self, issue_id: str) -> dict[str, Any]:
        issue_id = _validate_issue_id(issue_id)
        with self._locked():
            found = self._find_issue(issue_id)
            if found is None:
                raise FileNotFoundError(issue_id)
            return _defensive_copy(found[2])

    def _regression_candidate_path(self, issue_id: str) -> Path:
        self._assert_layout()
        path = self.regressions_root / f"{issue_id}.json"
        if _is_link_or_reparse_point(path):
            raise ValueError("regression candidate must not be a link or reparse point")
        if path.resolve(strict=False).parent != self.regressions_root.resolve():
            raise ValueError("regression candidate path escapes regressions root")
        return path

    @staticmethod
    def _validated_regression_source(issue: Mapping[str, Any]) -> Scenario:
        scenario = Scenario.from_dict(issue["regression_source"])
        checks = (
            (
                scenario.scenario_id in issue["scenario_ids"],
                "scenario_id is not tracked by the issue",
            ),
            (scenario.seed in issue["seeds"], "seed is not tracked by the issue"),
            (scenario.intent in issue["intents"], "intent is not tracked by the issue"),
            (
                scenario.persona.primary_bucket in issue["health_buckets"],
                "health bucket is not tracked by the issue",
            ),
            (
                scenario.expectation.to_dict() == issue["expected"],
                "expectation does not match expected",
            ),
        )
        for valid, message in checks:
            if not valid:
                raise ValueError(f"regression source {message}")
        return scenario

    @staticmethod
    def _assert_matching_candidate(
        path: Path,
        candidate: Mapping[str, Any],
        parent_identity: tuple[int, int, Path],
    ) -> None:
        raw = _read_regular_file_bytes(path, parent_identity)
        try:
            existing = _strict_json_loads(raw.decode("utf-8"))
            if type(existing) is not dict:
                raise ValueError("regression candidate must be a JSON object")
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
            minimized_messages = issue["minimized_messages"]
            if not minimized_messages or any(not item for item in minimized_messages):
                raise ValueError("minimized_messages must contain non-empty strings")

            source = self._validated_regression_source(issue)
            candidate_payload = source.to_dict()
            candidate_payload["scenario_id"] = f"regression-{issue['fingerprint']}"
            candidate_payload["messages"] = list(minimized_messages)
            candidate = Scenario.from_dict(candidate_payload).to_dict()
            parent_identity = _directory_identity(self.regressions_root)
            path = self._regression_candidate_path(issue_id)
            _assert_directory_identity(self.regressions_root, parent_identity)
            if path.exists() or path.is_symlink():
                self._assert_matching_candidate(path, candidate, parent_identity)
                return path
            if not _atomic_create_json(
                path,
                candidate,
                parent_identity=parent_identity,
            ):
                self._assert_matching_candidate(path, candidate, parent_identity)
            _assert_directory_identity(self.regressions_root, parent_identity)
            return path

    @staticmethod
    def _validate_resolution_cycle(
        issue_id: str,
        verification_cycle: Mapping[str, Any] | None,
    ) -> None:
        if not isinstance(verification_cycle, Mapping):
            raise ValueError("resolving an issue requires a verification cycle")
        from tests.evaluation.autonomous_cycle import _validate_completed_cycle_payload

        cycle = _defensive_copy(verification_cycle)
        validated = _validate_completed_cycle_payload(
            cycle,
            cycle_id=cycle.get("cycle_id"),
        )
        issue_ids = validated["issue_ids"]
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
            with self._transaction(
                (self.index_path, source, destination)
            ) as identities:
                _atomic_write_json(destination, updated)
                identities.files[destination] = _file_identity(destination)
                _durable_unlink(
                    source,
                    expected_parent_identity=identities.parents[source.parent],
                    expected_file_identity=identities.files[source],
                )
                self._rebuild_index()
            return _defensive_copy(updated)
