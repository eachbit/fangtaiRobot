from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.food_terms import expand_terms
from tests.evaluation.schemas import EvaluationReport, Scenario


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
_STRUCTURE_INTENTS = frozenset(
    {"structure_ratio", "relative_revision", "cooking_diversity"}
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


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_json_text(value), encoding="utf-8")
    temporary.replace(path)


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
) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    metadata = dict(metadata or {})
    intermediates = intermediates or {}
    minimizations = minimizations or {}
    source_metadata = source_metadata or {}

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
    _write_json(summary_path, _strip_session_ids(base))
    _write_json(coverage_path, report.to_dict()["coverage"])

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
    markdown_path.write_text(markdown, encoding="utf-8")

    failures_dir = output / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
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
            payload["minimizer"] = dict(
                minimizations.get(
                    failure.scenario_id,
                    {"attempts": 0, "reached_cap": False},
                )
            )
            payload["known_gap"] = any(
                violation.severity == "known_gap" for violation in failure.violations
            )
            payload["intermediates"] = list(intermediates.get(failure.scenario_id, ()))
        _write_json(failures_dir / filename, _strip_session_ids(payload))

    return {
        "summary_json": summary_path,
        "summary_markdown": markdown_path,
        "coverage_json": coverage_path,
        "failures_dir": failures_dir,
    }
