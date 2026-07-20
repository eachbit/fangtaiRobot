from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
from types import MappingProxyType
from typing import Any, TypeAlias

from tests.evaluation.schemas import PRIMARY_BUCKETS


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
FrozenJSONValue: TypeAlias = (
    JSONScalar
    | tuple["FrozenJSONValue", ...]
    | Mapping[str, "FrozenJSONValue"]
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

_ALLOWED_FIELDS = frozenset(
    {
        "candidate_id",
        "health_bucket",
        "messages",
        "structured_ground_truth",
        "agent_review",
    }
)
_REQUIRED_FIELDS = frozenset(
    {"candidate_id", "messages", "structured_ground_truth"}
)
_SAFE_IDENTIFIER = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z"
)
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
_JSON_INT_MIN = -(2**63)
_JSON_INT_MAX = 2**63 - 1


def _invalid(path: str, message: str) -> ValueError:
    return ValueError(f"{path}: {message}")


def _field_path(path: str, field_name: str) -> str:
    return (
        f"{path}.{field_name}"
        if field_name.isidentifier()
        else f"{path}[{field_name!r}]"
    )


def _object(
    value: Any,
    path: str,
    *,
    allowed: frozenset[str] | None = None,
    required: frozenset[str] = frozenset(),
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(path, "expected an object")
    for key in value:
        if type(key) is not str:
            raise _invalid(path, f"object key {key!r} must be a string")
        if allowed is not None and key not in allowed:
            raise _invalid(_field_path(path, key), "unknown field")
    for key in sorted(required):
        if key not in value:
            raise _invalid(_field_path(path, key), "missing required field")
    return value


def _string(value: Any, path: str) -> str:
    if type(value) is not str:
        raise _invalid(path, "expected a string")
    return value


def _freeze_json(
    value: Any,
    path: str,
    *,
    allow_tuple: bool = True,
) -> FrozenJSONValue:
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int:
        if not _JSON_INT_MIN <= value <= _JSON_INT_MAX:
            raise _invalid(path, "integer must fit in signed 64-bit range")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _invalid(path, "number must be finite")
        return value
    if isinstance(value, Mapping):
        result: dict[str, FrozenJSONValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise _invalid(path, f"object key {key!r} must be a string")
            result[key] = _freeze_json(
                item,
                _field_path(path, key),
                allow_tuple=allow_tuple,
            )
        return MappingProxyType(result)
    if type(value) is tuple and not allow_tuple:
        raise _invalid(path, "expected a JSON array, not a tuple")
    if type(value) in (list, tuple):
        return tuple(
            _freeze_json(
                item,
                f"{path}[{index}]",
                allow_tuple=allow_tuple,
            )
            for index, item in enumerate(value)
        )
    raise _invalid(path, f"unsupported JSON value type {type(value).__name__}")


def _freeze_json_object(
    value: Any,
    path: str,
    *,
    allow_tuple: bool = True,
) -> Mapping[str, FrozenJSONValue]:
    _object(value, path)
    frozen = _freeze_json(value, path, allow_tuple=allow_tuple)
    if not isinstance(frozen, Mapping):
        raise _invalid(path, "expected an object")
    return frozen


def _json_safe(value: Any, path: str) -> JSONValue:
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int:
        if not _JSON_INT_MIN <= value <= _JSON_INT_MAX:
            raise _invalid(path, "integer must fit in signed 64-bit range")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _invalid(path, "number must be finite")
        return value
    if isinstance(value, Mapping):
        return {
            key: _json_safe(item, _field_path(path, key))
            for key, item in value.items()
        }
    if type(value) in (list, tuple):
        return [
            _json_safe(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise _invalid(path, f"unsupported JSON value type {type(value).__name__}")


def _json_safe_object(value: Any, path: str) -> dict[str, JSONValue]:
    safe = _json_safe(value, path)
    if not isinstance(safe, dict):
        raise _invalid(path, "expected an object")
    return safe


def _safe_candidate_id(value: Any, path: str) -> str:
    candidate_id = _string(value, path)
    if (
        _SAFE_IDENTIFIER.fullmatch(candidate_id) is None
        or candidate_id.partition(".")[0].upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise _invalid(path, "expected a safe identifier")
    return candidate_id


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


def _literal_candidate_directory(root: Path) -> Path:
    current = root
    components = ("artifacts", "evaluation", "candidates")
    paths = [root]
    for component in components:
        current = current / component
        paths.append(current)
    for path in paths:
        if _is_link_or_reparse_point(path):
            raise ValueError(
                "candidate path must not contain links or reparse points"
            )
    current.mkdir(parents=True, exist_ok=True)
    for path in paths:
        if _is_link_or_reparse_point(path):
            raise ValueError(
                "candidate path must not contain links or reparse points"
            )
    return current


@dataclass(frozen=True)
class AgentCandidate:
    candidate_id: str
    messages: tuple[str, ...]
    health_bucket: str | None = None
    _structured_ground_truth: Mapping[str, FrozenJSONValue] = field(
        default_factory=dict,
        repr=False,
    )
    _soft_review: Mapping[str, FrozenJSONValue] = field(
        default_factory=dict,
        repr=False,
    )

    def __post_init__(self) -> None:
        _safe_candidate_id(self.candidate_id, "$.candidate_id")
        if type(self.messages) is not tuple:
            raise _invalid("$.messages", "expected a tuple")
        if len(self.messages) > 12:
            raise _invalid("$.messages", "at most 12 turns")
        for index, item in enumerate(self.messages):
            path = f"$.messages[{index}]"
            message = _string(item, path)
            if len(message) > 500:
                raise _invalid(path, "at most 500 characters")
        if self.health_bucket is not None:
            bucket = _string(self.health_bucket, "$.health_bucket")
            if bucket not in PRIMARY_BUCKETS:
                raise _invalid(
                    "$.health_bucket",
                    f"unknown value {bucket!r}",
                )
        object.__setattr__(
            self,
            "_structured_ground_truth",
            _freeze_json_object(
                self._structured_ground_truth,
                "$.structured_ground_truth",
            ),
        )
        object.__setattr__(
            self,
            "_soft_review",
            _freeze_json_object(self._soft_review, "$.agent_review"),
        )

    @property
    def structured_ground_truth(self) -> dict[str, JSONValue]:
        return _json_safe_object(
            self._structured_ground_truth,
            "$.structured_ground_truth",
        )

    @property
    def soft_review(self) -> dict[str, JSONValue]:
        return _json_safe_object(self._soft_review, "$.agent_review")

    @property
    def agent_review_is_soft(self) -> bool:
        return True

    def to_dict(self) -> dict[str, JSONValue]:
        result: dict[str, JSONValue] = {
            "candidate_id": self.candidate_id,
            "messages": list(self.messages),
            "structured_ground_truth": self.structured_ground_truth,
        }
        if self.health_bucket is not None:
            result["health_bucket"] = self.health_bucket
        if self._soft_review:
            result["agent_review"] = self.soft_review
        return result


def validate_candidate(data: Mapping[str, Any]) -> AgentCandidate:
    value = _object(
        data,
        "$",
        allowed=_ALLOWED_FIELDS,
        required=_REQUIRED_FIELDS,
    )
    candidate_id = _safe_candidate_id(value["candidate_id"], "$.candidate_id")

    messages_value = value["messages"]
    if type(messages_value) is not list:
        raise _invalid("$.messages", "expected a JSON array")
    if len(messages_value) > 12:
        raise _invalid("$.messages", "at most 12 turns")
    messages: list[str] = []
    for index, item in enumerate(messages_value):
        path = f"$.messages[{index}]"
        message = _string(item, path)
        if len(message) > 500:
            raise _invalid(path, "at most 500 characters")
        messages.append(message)

    health_bucket: str | None = None
    if "health_bucket" in value:
        health_bucket = _string(value["health_bucket"], "$.health_bucket")
        if health_bucket not in PRIMARY_BUCKETS:
            raise _invalid(
                "$.health_bucket",
                f"unknown value {health_bucket!r}",
            )

    ground_truth = _freeze_json_object(
        value["structured_ground_truth"],
        "$.structured_ground_truth",
        allow_tuple=False,
    )
    soft_review = _freeze_json_object(
        value.get("agent_review", {}),
        "$.agent_review",
        allow_tuple=False,
    )
    return AgentCandidate(
        candidate_id=candidate_id,
        messages=tuple(messages),
        health_bucket=health_bucket,
        _structured_ground_truth=ground_truth,
        _soft_review=soft_review,
    )


def save_unreviewed_candidate(
    candidate: AgentCandidate | Mapping[str, Any],
    *,
    repository_root: str | Path | None = None,
) -> Path:
    if type(candidate) is AgentCandidate:
        value = validate_candidate(candidate.to_dict())
    elif isinstance(candidate, Mapping):
        value = validate_candidate(candidate)
    else:
        raise _invalid(
            "$.candidate",
            "expected an exact AgentCandidate or JSON object",
        )
    root = Path(
        _REPOSITORY_ROOT if repository_root is None else repository_root
    ).absolute()
    literal_candidate_directory = _literal_candidate_directory(root)
    resolved_root = root.resolve()
    candidate_directory = literal_candidate_directory.resolve()
    try:
        candidate_directory.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("candidate directory must remain below repository root") from exc

    destination = (candidate_directory / f"{value.candidate_id}.json").resolve()
    if destination.parent != candidate_directory:
        raise ValueError(
            "candidate destination must remain directly below candidate directory"
        )
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=candidate_directory,
            prefix=f".{value.candidate_id}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                value.to_dict(),
                temporary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return destination
