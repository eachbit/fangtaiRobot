from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
FrozenJSONValue: TypeAlias = (
    JSONScalar
    | tuple["FrozenJSONValue", ...]
    | Mapping[str, "FrozenJSONValue"]
)

PRIMARY_BUCKETS = frozenset(
    {
        "healthy",
        "single_condition",
        "multi_condition",
        "special_group",
        "high_risk",
    }
)
DIALOGUE_MODES = frozenset({"single_turn", "multi_turn"})
VIOLATION_SEVERITIES = frozenset({"blocking", "known_gap", "soft_review"})
JSON_INT_MIN = -(2**63)
JSON_INT_MAX = 2**63 - 1


def _field_path(path: str, field: str) -> str:
    return f"{path}.{field}" if field.isidentifier() else f"{path}[{field!r}]"


def _invalid(path: str, message: str) -> ValueError:
    return ValueError(f"{path}: {message}")


def _json_integer(value: Any, path: str) -> int:
    if type(value) is not int:
        raise _invalid(path, "expected an integer")
    if not JSON_INT_MIN <= value <= JSON_INT_MAX:
        raise _invalid(path, "integer must fit in signed 64-bit range")
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
        return _json_integer(value, path)
    if type(value) is float:
        if not math.isfinite(value):
            raise _invalid(path, "number must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJSONValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise _invalid(path, f"object key {key!r} must be a string")
            frozen[key] = _freeze_json(
                item,
                _field_path(path, key),
                allow_tuple=allow_tuple,
            )
        return MappingProxyType(frozen)
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
    if not isinstance(value, Mapping):
        raise _invalid(path, "expected an object")
    frozen = _freeze_json(value, path, allow_tuple=allow_tuple)
    if not isinstance(frozen, Mapping):
        raise _invalid(path, "expected an object")
    return frozen


def _json_safe(value: Any, path: str) -> JSONValue:
    if value is None or type(value) in (str, bool):
        return value
    if type(value) is int:
        return _json_integer(value, path)
    if type(value) is float:
        if not math.isfinite(value):
            raise _invalid(path, "number must be finite")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise _invalid(path, f"object key {key!r} must be a string")
            result[key] = _json_safe(item, _field_path(path, key))
        return result
    if type(value) in (list, tuple):
        return [
            _json_safe(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise _invalid(path, f"unsupported JSON value type {type(value).__name__}")


def _object(
    value: Any,
    path: str,
    *,
    allowed: frozenset[str],
    required: frozenset[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(path, "expected an object")
    for key in value:
        if type(key) is not str:
            raise _invalid(path, f"object key {key!r} must be a string")
        if key not in allowed:
            raise _invalid(_field_path(path, key), "unknown field")
    for field in sorted(required):
        if field not in value:
            raise _invalid(_field_path(path, field), "missing required field")
    return value


def _string(value: Any, path: str) -> str:
    if type(value) is not str:
        raise _invalid(path, "expected a string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _integer(value: Any, path: str) -> int:
    return _json_integer(value, path)


def _optional_integer(value: Any, path: str) -> int | None:
    if value is None:
        return None
    return _integer(value, path)


def _number(value: Any, path: str) -> float:
    if type(value) not in (int, float):
        raise _invalid(path, "expected a number")
    if type(value) is int:
        _json_integer(value, path)
    try:
        result = float(value)
    except OverflowError as exc:
        raise _invalid(path, "number is outside the finite float range") from exc
    if not math.isfinite(result):
        raise _invalid(path, "number must be finite")
    if type(value) is int and int(result) != value:
        raise _invalid(path, "integer cannot be represented exactly as a float")
    return result


def _optional_number(value: Any, path: str) -> float | None:
    if value is None:
        return None
    return _number(value, path)


def _optional_positive_number(value: Any, path: str) -> float | None:
    result = _optional_number(value, path)
    if result is not None and result <= 0:
        raise _invalid(path, "must be greater than zero")
    return result


def _optional_positive_integer(value: Any, path: str) -> int | None:
    result = _optional_integer(value, path)
    if result is not None and result <= 0:
        raise _invalid(path, "must be greater than zero")
    return result


def _boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise _invalid(path, "expected a boolean")
    return value


def _string_tuple_from_json(value: Any, path: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise _invalid(path, "expected an array")
    return tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(value))


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _invalid(path, "expected a tuple")
    for index, item in enumerate(value):
        _string(item, f"{path}[{index}]")
    return value


def _choice(value: str, choices: frozenset[str], path: str) -> str:
    if value not in choices:
        raise _invalid(path, f"unknown value {value!r}")
    return value


def _instance(value: Any, expected: type, path: str) -> Any:
    if not isinstance(value, expected):
        raise _invalid(path, f"expected {expected.__name__}")
    return value


def _typed_tuple(value: Any, expected: type, path: str) -> tuple[Any, ...]:
    if type(value) is not tuple:
        raise _invalid(path, "expected a tuple")
    for index, item in enumerate(value):
        _instance(item, expected, f"{path}[{index}]")
    return value


@dataclass(frozen=True)
class CheckupMetrics:
    fasting_glucose_mmol_l: float | None = None
    systolic_blood_pressure_mm_hg: int | None = None
    diastolic_blood_pressure_mm_hg: int | None = None
    total_cholesterol_mmol_l: float | None = None
    triglycerides_mmol_l: float | None = None
    ldl_mmol_l: float | None = None
    hdl_mmol_l: float | None = None
    uric_acid_umol_l: int | None = None

    _FIELDS = frozenset(
        {
            "fasting_glucose_mmol_l",
            "systolic_blood_pressure_mm_hg",
            "diastolic_blood_pressure_mm_hg",
            "total_cholesterol_mmol_l",
            "triglycerides_mmol_l",
            "ldl_mmol_l",
            "hdl_mmol_l",
            "uric_acid_umol_l",
        }
    )

    @staticmethod
    def _validate_blood_pressure(
        systolic: int | None,
        diastolic: int | None,
        path: str,
    ) -> None:
        if systolic is None and diastolic is not None:
            raise _invalid(
                _field_path(path, "systolic_blood_pressure_mm_hg"),
                "must be provided with diastolic blood pressure",
            )
        if systolic is not None and diastolic is None:
            raise _invalid(
                _field_path(path, "diastolic_blood_pressure_mm_hg"),
                "must be provided with systolic blood pressure",
            )
        if systolic is not None and diastolic is not None and systolic <= diastolic:
            raise _invalid(
                _field_path(path, "systolic_blood_pressure_mm_hg"),
                "must be greater than diastolic blood pressure",
            )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fasting_glucose_mmol_l",
            _optional_positive_number(
                self.fasting_glucose_mmol_l,
                "$.fasting_glucose_mmol_l",
            ),
        )
        systolic = _optional_positive_integer(
            self.systolic_blood_pressure_mm_hg,
            "$.systolic_blood_pressure_mm_hg",
        )
        diastolic = _optional_positive_integer(
            self.diastolic_blood_pressure_mm_hg,
            "$.diastolic_blood_pressure_mm_hg",
        )
        object.__setattr__(
            self,
            "total_cholesterol_mmol_l",
            _optional_positive_number(
                self.total_cholesterol_mmol_l,
                "$.total_cholesterol_mmol_l",
            ),
        )
        object.__setattr__(
            self,
            "triglycerides_mmol_l",
            _optional_positive_number(
                self.triglycerides_mmol_l,
                "$.triglycerides_mmol_l",
            ),
        )
        object.__setattr__(
            self,
            "ldl_mmol_l",
            _optional_positive_number(self.ldl_mmol_l, "$.ldl_mmol_l"),
        )
        object.__setattr__(
            self,
            "hdl_mmol_l",
            _optional_positive_number(self.hdl_mmol_l, "$.hdl_mmol_l"),
        )
        _optional_positive_integer(self.uric_acid_umol_l, "$.uric_acid_umol_l")
        self._validate_blood_pressure(systolic, diastolic, "$")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "fasting_glucose_mmol_l": self.fasting_glucose_mmol_l,
            "systolic_blood_pressure_mm_hg": self.systolic_blood_pressure_mm_hg,
            "diastolic_blood_pressure_mm_hg": self.diastolic_blood_pressure_mm_hg,
            "total_cholesterol_mmol_l": self.total_cholesterol_mmol_l,
            "triglycerides_mmol_l": self.triglycerides_mmol_l,
            "ldl_mmol_l": self.ldl_mmol_l,
            "hdl_mmol_l": self.hdl_mmol_l,
            "uric_acid_umol_l": self.uric_acid_umol_l,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CheckupMetrics:
        return cls._from_dict(data, "$")

    @classmethod
    def _from_dict(cls, data: Any, path: str) -> CheckupMetrics:
        value = _object(data, path, allowed=cls._FIELDS, required=frozenset())
        fasting_glucose = _optional_positive_number(
            value.get("fasting_glucose_mmol_l"),
            _field_path(path, "fasting_glucose_mmol_l"),
        )
        systolic = _optional_positive_integer(
            value.get("systolic_blood_pressure_mm_hg"),
            _field_path(path, "systolic_blood_pressure_mm_hg"),
        )
        diastolic = _optional_positive_integer(
            value.get("diastolic_blood_pressure_mm_hg"),
            _field_path(path, "diastolic_blood_pressure_mm_hg"),
        )
        total_cholesterol = _optional_positive_number(
            value.get("total_cholesterol_mmol_l"),
            _field_path(path, "total_cholesterol_mmol_l"),
        )
        triglycerides = _optional_positive_number(
            value.get("triglycerides_mmol_l"),
            _field_path(path, "triglycerides_mmol_l"),
        )
        ldl = _optional_positive_number(
            value.get("ldl_mmol_l"),
            _field_path(path, "ldl_mmol_l"),
        )
        hdl = _optional_positive_number(
            value.get("hdl_mmol_l"),
            _field_path(path, "hdl_mmol_l"),
        )
        uric_acid = _optional_positive_integer(
            value.get("uric_acid_umol_l"),
            _field_path(path, "uric_acid_umol_l"),
        )
        cls._validate_blood_pressure(systolic, diastolic, path)
        return cls(
            fasting_glucose_mmol_l=fasting_glucose,
            systolic_blood_pressure_mm_hg=systolic,
            diastolic_blood_pressure_mm_hg=diastolic,
            total_cholesterol_mmol_l=total_cholesterol,
            triglycerides_mmol_l=triglycerides,
            ldl_mmol_l=ldl,
            hdl_mmol_l=hdl,
            uric_acid_umol_l=uric_acid,
        )


@dataclass(frozen=True)
class HealthPersona:
    persona_id: str
    primary_bucket: str
    source_user_id: int | None = None
    gender: str | None = None
    age: int | None = None
    labor_intensity: str | None = None
    pregnancy_week: str | None = None
    taste_preference: str | None = None
    special_groups: tuple[str, ...] = ()
    allergens: tuple[str, ...] = ()
    health_goals: tuple[str, ...] = ()
    height_cm: float | None = None
    weight_kg: float | None = None
    bmi: float | None = None
    checkup_metrics: CheckupMetrics | None = None

    _FIELDS = frozenset(
        {
            "persona_id",
            "primary_bucket",
            "source_user_id",
            "gender",
            "age",
            "labor_intensity",
            "pregnancy_week",
            "taste_preference",
            "height_cm",
            "weight_kg",
            "bmi",
            "checkup_metrics",
            "special_groups",
            "allergens",
            "health_goals",
        }
    )
    _REQUIRED = frozenset({"persona_id", "primary_bucket"})

    def __post_init__(self) -> None:
        _string(self.persona_id, "$.persona_id")
        _choice(_string(self.primary_bucket, "$.primary_bucket"), PRIMARY_BUCKETS, "$.primary_bucket")
        _optional_integer(self.source_user_id, "$.source_user_id")
        _optional_string(self.gender, "$.gender")
        _optional_integer(self.age, "$.age")
        _optional_string(self.labor_intensity, "$.labor_intensity")
        _optional_string(self.pregnancy_week, "$.pregnancy_week")
        _optional_string(self.taste_preference, "$.taste_preference")
        object.__setattr__(
            self,
            "height_cm",
            _optional_positive_number(self.height_cm, "$.height_cm"),
        )
        object.__setattr__(
            self,
            "weight_kg",
            _optional_positive_number(self.weight_kg, "$.weight_kg"),
        )
        object.__setattr__(
            self,
            "bmi",
            _optional_positive_number(self.bmi, "$.bmi"),
        )
        if self.checkup_metrics is not None:
            _instance(self.checkup_metrics, CheckupMetrics, "$.checkup_metrics")
        _string_tuple(self.special_groups, "$.special_groups")
        _string_tuple(self.allergens, "$.allergens")
        _string_tuple(self.health_goals, "$.health_goals")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "persona_id": self.persona_id,
            "primary_bucket": self.primary_bucket,
            "source_user_id": self.source_user_id,
            "gender": self.gender,
            "age": self.age,
            "labor_intensity": self.labor_intensity,
            "pregnancy_week": self.pregnancy_week,
            "taste_preference": self.taste_preference,
            "height_cm": self.height_cm,
            "weight_kg": self.weight_kg,
            "bmi": self.bmi,
            "checkup_metrics": (
                self.checkup_metrics.to_dict()
                if self.checkup_metrics is not None
                else None
            ),
            "special_groups": list(self.special_groups),
            "allergens": list(self.allergens),
            "health_goals": list(self.health_goals),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> HealthPersona:
        return cls._from_dict(data, "$")

    @classmethod
    def _from_dict(cls, data: Any, path: str) -> HealthPersona:
        value = _object(data, path, allowed=cls._FIELDS, required=cls._REQUIRED)
        bucket_path = _field_path(path, "primary_bucket")
        bucket = _choice(_string(value["primary_bucket"], bucket_path), PRIMARY_BUCKETS, bucket_path)
        return cls(
            persona_id=_string(value["persona_id"], _field_path(path, "persona_id")),
            primary_bucket=bucket,
            source_user_id=_optional_integer(value.get("source_user_id"), _field_path(path, "source_user_id")),
            gender=_optional_string(value.get("gender"), _field_path(path, "gender")),
            age=_optional_integer(value.get("age"), _field_path(path, "age")),
            labor_intensity=_optional_string(value.get("labor_intensity"), _field_path(path, "labor_intensity")),
            pregnancy_week=_optional_string(value.get("pregnancy_week"), _field_path(path, "pregnancy_week")),
            taste_preference=_optional_string(value.get("taste_preference"), _field_path(path, "taste_preference")),
            height_cm=_optional_positive_number(
                value.get("height_cm"),
                _field_path(path, "height_cm"),
            ),
            weight_kg=_optional_positive_number(
                value.get("weight_kg"),
                _field_path(path, "weight_kg"),
            ),
            bmi=_optional_positive_number(
                value.get("bmi"),
                _field_path(path, "bmi"),
            ),
            checkup_metrics=(
                None
                if value.get("checkup_metrics") is None
                else CheckupMetrics._from_dict(
                    value["checkup_metrics"],
                    _field_path(path, "checkup_metrics"),
                )
            ),
            special_groups=_string_tuple_from_json(
                value.get("special_groups", []),
                _field_path(path, "special_groups"),
            ),
            allergens=_string_tuple_from_json(value.get("allergens", []), _field_path(path, "allergens")),
            health_goals=_string_tuple_from_json(value.get("health_goals", []), _field_path(path, "health_goals")),
        )


@dataclass(frozen=True)
class MenuExpectation:
    dish_count: int | None = None
    meat_count: int | None = None
    vegetable_count: int | None = None
    minimum_cooking_methods: int | None = None
    forbidden_terms: tuple[str, ...] = ()
    clarification_required: bool = False
    preserve_unaffected: bool = False

    _FIELDS = frozenset(
        {
            "dish_count",
            "meat_count",
            "vegetable_count",
            "minimum_cooking_methods",
            "forbidden_terms",
            "clarification_required",
            "preserve_unaffected",
        }
    )

    def __post_init__(self) -> None:
        _optional_integer(self.dish_count, "$.dish_count")
        _optional_integer(self.meat_count, "$.meat_count")
        _optional_integer(self.vegetable_count, "$.vegetable_count")
        _optional_integer(self.minimum_cooking_methods, "$.minimum_cooking_methods")
        _string_tuple(self.forbidden_terms, "$.forbidden_terms")
        _boolean(self.clarification_required, "$.clarification_required")
        _boolean(self.preserve_unaffected, "$.preserve_unaffected")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "dish_count": self.dish_count,
            "meat_count": self.meat_count,
            "vegetable_count": self.vegetable_count,
            "minimum_cooking_methods": self.minimum_cooking_methods,
            "forbidden_terms": list(self.forbidden_terms),
            "clarification_required": self.clarification_required,
            "preserve_unaffected": self.preserve_unaffected,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MenuExpectation:
        return cls._from_dict(data, "$")

    @classmethod
    def _from_dict(cls, data: Any, path: str) -> MenuExpectation:
        value = _object(data, path, allowed=cls._FIELDS, required=frozenset())
        return cls(
            dish_count=_optional_integer(value.get("dish_count"), _field_path(path, "dish_count")),
            meat_count=_optional_integer(value.get("meat_count"), _field_path(path, "meat_count")),
            vegetable_count=_optional_integer(value.get("vegetable_count"), _field_path(path, "vegetable_count")),
            minimum_cooking_methods=_optional_integer(
                value.get("minimum_cooking_methods"),
                _field_path(path, "minimum_cooking_methods"),
            ),
            forbidden_terms=_string_tuple_from_json(
                value.get("forbidden_terms", []),
                _field_path(path, "forbidden_terms"),
            ),
            clarification_required=_boolean(
                value.get("clarification_required", False),
                _field_path(path, "clarification_required"),
            ),
            preserve_unaffected=_boolean(
                value.get("preserve_unaffected", False),
                _field_path(path, "preserve_unaffected"),
            ),
        )


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    persona: HealthPersona
    messages: tuple[str, ...]
    expectation: MenuExpectation
    seed: int
    intent: str = "general_recommendation"
    dialogue_mode: str = "single_turn"

    _FIELDS = frozenset(
        {"scenario_id", "persona", "messages", "expectation", "seed", "intent", "dialogue_mode"}
    )
    _REQUIRED = frozenset({"scenario_id", "persona", "messages", "expectation", "seed"})

    def __post_init__(self) -> None:
        scenario_id = _string(self.scenario_id, "$.scenario_id")
        if not scenario_id.strip():
            raise _invalid("$.scenario_id", "must not be empty")
        _instance(self.persona, HealthPersona, "$.persona")
        _string_tuple(self.messages, "$.messages")
        _instance(self.expectation, MenuExpectation, "$.expectation")
        _integer(self.seed, "$.seed")
        intent = _string(self.intent, "$.intent")
        if not intent.strip():
            raise _invalid("$.intent", "must not be empty")
        _choice(_string(self.dialogue_mode, "$.dialogue_mode"), DIALOGUE_MODES, "$.dialogue_mode")

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "scenario_id": self.scenario_id,
            "persona": self.persona.to_dict(),
            "messages": list(self.messages),
            "expectation": self.expectation.to_dict(),
            "seed": self.seed,
            "intent": self.intent,
            "dialogue_mode": self.dialogue_mode,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Scenario:
        value = _object(data, "$", allowed=cls._FIELDS, required=cls._REQUIRED)
        dialogue_path = "$.dialogue_mode"
        dialogue_mode = _choice(
            _string(value.get("dialogue_mode", "single_turn"), dialogue_path),
            DIALOGUE_MODES,
            dialogue_path,
        )
        return cls(
            scenario_id=_string(value["scenario_id"], "$.scenario_id"),
            persona=HealthPersona._from_dict(value["persona"], "$.persona"),
            messages=_string_tuple_from_json(value["messages"], "$.messages"),
            expectation=MenuExpectation._from_dict(value["expectation"], "$.expectation"),
            seed=_integer(value["seed"], "$.seed"),
            intent=_string(value.get("intent", "general_recommendation"), "$.intent"),
            dialogue_mode=dialogue_mode,
        )


@dataclass(frozen=True)
class Violation:
    code: str
    severity: str
    message: str
    evidence: FrozenJSONValue

    _FIELDS = frozenset({"code", "severity", "message", "evidence"})

    def __post_init__(self) -> None:
        _string(self.code, "$.code")
        _choice(_string(self.severity, "$.severity"), VIOLATION_SEVERITIES, "$.severity")
        _string(self.message, "$.message")
        object.__setattr__(self, "evidence", _freeze_json(self.evidence, "$.evidence"))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": _json_safe(self.evidence, "$.evidence"),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Violation:
        return cls._from_dict(data, "$")

    @classmethod
    def _from_dict(cls, data: Any, path: str) -> Violation:
        value = _object(data, path, allowed=cls._FIELDS, required=cls._FIELDS)
        severity_path = _field_path(path, "severity")
        severity = _choice(
            _string(value["severity"], severity_path),
            VIOLATION_SEVERITIES,
            severity_path,
        )
        evidence_path = _field_path(path, "evidence")
        evidence = _freeze_json(
            value["evidence"],
            evidence_path,
            allow_tuple=False,
        )
        return cls(
            code=_string(value["code"], _field_path(path, "code")),
            severity=severity,
            message=_string(value["message"], _field_path(path, "message")),
            evidence=evidence,
        )


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    passed: bool
    violations: tuple[Violation, ...]
    elapsed_ms: float

    _FIELDS = frozenset({"scenario_id", "passed", "violations", "elapsed_ms"})

    def __post_init__(self) -> None:
        _string(self.scenario_id, "$.scenario_id")
        _boolean(self.passed, "$.passed")
        _typed_tuple(self.violations, Violation, "$.violations")
        object.__setattr__(self, "elapsed_ms", _number(self.elapsed_ms, "$.elapsed_ms"))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "violations": [violation.to_dict() for violation in self.violations],
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScenarioResult:
        value = _object(data, "$", allowed=cls._FIELDS, required=cls._FIELDS)
        violations_path = "$.violations"
        violations = value["violations"]
        if type(violations) is not list:
            raise _invalid(violations_path, "expected an array")
        return cls(
            scenario_id=_string(value["scenario_id"], "$.scenario_id"),
            passed=_boolean(value["passed"], "$.passed"),
            violations=tuple(
                Violation._from_dict(item, f"{violations_path}[{index}]")
                for index, item in enumerate(violations)
            ),
            elapsed_ms=_number(value["elapsed_ms"], "$.elapsed_ms"),
        )


@dataclass(frozen=True)
class FailureRecord:
    scenario_id: str
    seed: int
    commit_sha: str
    original_messages: tuple[str, ...]
    minimized_messages: tuple[str, ...]
    violations: tuple[Violation, ...]
    elapsed_ms: float

    _FIELDS = frozenset(
        {
            "scenario_id",
            "seed",
            "commit_sha",
            "original_messages",
            "minimized_messages",
            "violations",
            "elapsed_ms",
        }
    )

    def __post_init__(self) -> None:
        _string(self.scenario_id, "$.scenario_id")
        _integer(self.seed, "$.seed")
        _string(self.commit_sha, "$.commit_sha")
        _string_tuple(self.original_messages, "$.original_messages")
        _string_tuple(self.minimized_messages, "$.minimized_messages")
        _typed_tuple(self.violations, Violation, "$.violations")
        object.__setattr__(self, "elapsed_ms", _number(self.elapsed_ms, "$.elapsed_ms"))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "commit_sha": self.commit_sha,
            "original_messages": list(self.original_messages),
            "minimized_messages": list(self.minimized_messages),
            "violations": [violation.to_dict() for violation in self.violations],
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FailureRecord:
        return cls._from_dict(data, "$")

    @classmethod
    def _from_dict(cls, data: Any, path: str) -> FailureRecord:
        value = _object(data, path, allowed=cls._FIELDS, required=cls._FIELDS)
        violations_path = _field_path(path, "violations")
        violations = value["violations"]
        if type(violations) is not list:
            raise _invalid(violations_path, "expected an array")
        return cls(
            scenario_id=_string(value["scenario_id"], _field_path(path, "scenario_id")),
            seed=_integer(value["seed"], _field_path(path, "seed")),
            commit_sha=_string(value["commit_sha"], _field_path(path, "commit_sha")),
            original_messages=_string_tuple_from_json(
                value["original_messages"],
                _field_path(path, "original_messages"),
            ),
            minimized_messages=_string_tuple_from_json(
                value["minimized_messages"],
                _field_path(path, "minimized_messages"),
            ),
            violations=tuple(
                Violation._from_dict(item, f"{violations_path}[{index}]")
                for index, item in enumerate(violations)
            ),
            elapsed_ms=_number(value["elapsed_ms"], _field_path(path, "elapsed_ms")),
        )


@dataclass(frozen=True)
class EvaluationReport:
    total: int
    passed: int
    failures: tuple[FailureRecord, ...]
    coverage: Mapping[str, FrozenJSONValue]
    metrics: Mapping[str, FrozenJSONValue]
    timings: Mapping[str, FrozenJSONValue]

    _FIELDS = frozenset({"total", "passed", "failures", "coverage", "metrics", "timings"})

    def __post_init__(self) -> None:
        _integer(self.total, "$.total")
        _integer(self.passed, "$.passed")
        _typed_tuple(self.failures, FailureRecord, "$.failures")
        object.__setattr__(self, "coverage", _freeze_json_object(self.coverage, "$.coverage"))
        object.__setattr__(self, "metrics", _freeze_json_object(self.metrics, "$.metrics"))
        object.__setattr__(self, "timings", _freeze_json_object(self.timings, "$.timings"))

    def to_dict(self) -> dict[str, JSONValue]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failures": [failure.to_dict() for failure in self.failures],
            "coverage": _json_safe(self.coverage, "$.coverage"),
            "metrics": _json_safe(self.metrics, "$.metrics"),
            "timings": _json_safe(self.timings, "$.timings"),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvaluationReport:
        value = _object(data, "$", allowed=cls._FIELDS, required=cls._FIELDS)
        failures = value["failures"]
        if type(failures) is not list:
            raise _invalid("$.failures", "expected an array")
        coverage = _freeze_json_object(
            value["coverage"],
            "$.coverage",
            allow_tuple=False,
        )
        metrics = _freeze_json_object(
            value["metrics"],
            "$.metrics",
            allow_tuple=False,
        )
        timings = _freeze_json_object(
            value["timings"],
            "$.timings",
            allow_tuple=False,
        )
        return cls(
            total=_integer(value["total"], "$.total"),
            passed=_integer(value["passed"], "$.passed"),
            failures=tuple(
                FailureRecord._from_dict(item, f"$.failures[{index}]")
                for index, item in enumerate(failures)
            ),
            coverage=coverage,
            metrics=metrics,
            timings=timings,
        )
