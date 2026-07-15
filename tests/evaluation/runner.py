from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from app.agent import recommend, recommend_with_session
from app.data_loader import load_recipes
from app.food_terms import expand_terms
from app.models import Recipe
from tests.evaluation.deterministic_oracle import (
    DEFAULT_KNOWN_GAPS_PATH,
    evaluate_result,
)
from tests.evaluation.failure_minimizer import MinimizationResult, minimize_failure
from tests.evaluation.report import compute_reviewed_metrics, write_report
from tests.evaluation.scenario_generator import generate_scenarios, summarize_coverage
from tests.evaluation.schemas import (
    EvaluationReport,
    FailureRecord,
    Scenario,
    ScenarioResult,
    Violation,
)


MODE_COUNTS = {"quick": 120, "daily": 2000, "deep": 10000}
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CORPUS_ROOT = _REPO_ROOT / "tests" / "corpus"
_CONTEXT_COLLECTION_FIELDS = (
    ("health_goals", ("health_goals",), "health_goals", False),
    ("allergens", ("allergens",), "allergens", True),
    ("avoid_ingredients", ("avoid_ingredients",), None, True),
    (
        "inferred_profile.special_groups",
        ("inferred_profile", "special_groups"),
        "special_groups",
        False,
    ),
    (
        "inferred_profile.allergens",
        ("inferred_profile", "allergens"),
        "allergens",
        True,
    ),
    (
        "inferred_profile.health_goals",
        ("inferred_profile", "health_goals"),
        "health_goals",
        False,
    ),
)


def nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return round(float(ordered[rank - 1]), 2)


def summarize_timings(values: Sequence[float]) -> dict[str, Any]:
    rounded = [round(float(value), 2) for value in values]
    return {
        "turn_count": len(rounded),
        "p50_ms": nearest_rank_percentile(rounded, 0.50),
        "p95_ms": nearest_rank_percentile(rounded, 0.95),
        "samples_ms": rounded,
    }


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _scenario_hash(scenario: Scenario) -> str:
    return hashlib.sha256(_stable_json(scenario.to_dict()).encode("utf-8")).hexdigest()


def _safe_json(value: object) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if type(value) in (list, tuple):
        return [_safe_json(item) for item in value]
    if type(value) is dict:
        return {
            str(key): _safe_json(item)
            for key, item in value.items()
            if str(key) != "session_id"
        }
    return None


def _fallback_coverage(scenarios: Sequence[Scenario]) -> dict[str, Any]:
    dimensions = {
        "primary_bucket": [item.persona.primary_bucket for item in scenarios],
        "intent": [item.intent for item in scenarios],
        "dialogue": [item.dialogue_mode for item in scenarios],
    }
    pairs: dict[str, dict[str, int]] = {}
    for left, right in (
        ("intent", "dialogue"),
        ("primary_bucket", "dialogue"),
        ("primary_bucket", "intent"),
    ):
        counts = Counter(
            f"{left}={left_value}|{right}={right_value}"
            for left_value, right_value in zip(
                dimensions[left], dimensions[right], strict=True
            )
        )
        pairs[f"{left},{right}"] = dict(sorted(counts.items()))
    return {
        "primary_bucket": dict(sorted(Counter(dimensions["primary_bucket"]).items())),
        "intent": dict(sorted(Counter(dimensions["intent"]).items())),
        "dialogue": dict(sorted(Counter(dimensions["dialogue"]).items())),
        "operation": {},
        "pairs": dict(sorted(pairs.items())),
    }


def _violation_counts(results: Iterable[ScenarioResult]) -> dict[str, dict[str, int]]:
    by_severity: Counter[str] = Counter()
    by_code: Counter[str] = Counter()
    for result in results:
        for violation in result.violations:
            by_severity[violation.severity] += 1
            by_code[violation.code] += 1
    return {
        "by_severity": dict(sorted(by_severity.items())),
        "by_code": dict(sorted(by_code.items())),
    }


def _context_collection(
    constraints: Mapping[str, Any], path: tuple[str, ...], expand: bool
) -> set[str]:
    value: object = constraints
    for key in path:
        if not isinstance(value, Mapping):
            return set()
        value = value.get(key)
    if type(value) is not list:
        return set()
    values = [item for item in value if type(item) is str and item]
    return set(expand_terms(values) if expand else values)


def _persona_context(
    scenario: Scenario, persona_field: str | None, expand: bool
) -> set[str]:
    if persona_field is None:
        return set()
    values = tuple(getattr(scenario.persona, persona_field))
    if scenario.persona.source_user_id is None:
        values = tuple(
            value
            for value in values
            if any(value in message for message in scenario.messages)
        )
    return set(expand_terms(list(values)) if expand else values)


def _menu_id_set(value: object) -> set[int | str]:
    if type(value) is not list:
        return set()
    return {item for item in value if type(item) in (int, str)}


def _append_blocking_once(
    result: ScenarioResult,
    code: str,
    message: str,
    evidence: Mapping[str, Any] | None,
) -> ScenarioResult:
    replacement = Violation(code, "blocking", message, evidence)
    violations = list(result.violations)
    matching = [index for index, item in enumerate(violations) if item.code == code]
    blocking = [
        index for index in matching if violations[index].severity == "blocking"
    ]
    if blocking:
        keep = blocking[0]
        deduplicated = tuple(
            item
            for index, item in enumerate(violations)
            if item.code != code or index == keep
        )
        return ScenarioResult(
            result.scenario_id,
            False,
            deduplicated,
            result.elapsed_ms,
        )
    if matching:
        keep = matching[0]
        violations = [
            replacement if index == keep else item
            for index, item in enumerate(violations)
            if item.code != code or index == keep
        ]
        return ScenarioResult(
            result.scenario_id,
            False,
            tuple(violations),
            result.elapsed_ms,
        )
    violations.append(replacement)
    return ScenarioResult(result.scenario_id, False, tuple(violations), result.elapsed_ms)


def default_oracle_adapter(
    scenario: Scenario,
    final_response: object,
    official_recipes: Mapping[int, Recipe],
    *,
    intermediates: tuple[Mapping[str, Any], ...],
    elapsed_ms: float,
    known_gaps_path: Path | None,
) -> ScenarioResult:
    result = evaluate_result(
        scenario,
        final_response,
        official_recipes,
        elapsed_ms=elapsed_ms,
        known_gaps_path=known_gaps_path,
    )
    if scenario.dialogue_mode != "multi_turn":
        return result

    valid_evidence = len(intermediates) == len(scenario.messages)
    for turn, item in enumerate(intermediates):
        if (
            not isinstance(item, Mapping)
            or item.get("turn") != turn
            or type(item.get("menu_ids")) is not list
            or not isinstance(item.get("constraints"), Mapping)
        ):
            valid_evidence = False
            result = _append_blocking_once(
                result,
                "dialogue.execution_evidence",
                "Multi-turn execution evidence is incomplete.",
                {"turn": turn},
            )
            break
    if not valid_evidence:
        return _append_blocking_once(
            result,
            "dialogue.execution_evidence",
            "Multi-turn execution evidence is incomplete.",
            {"turn": 0 if not intermediates else len(intermediates) - 1},
        )

    first_ids = _menu_id_set(intermediates[0]["menu_ids"])
    last_ids = _menu_id_set(intermediates[-1]["menu_ids"])
    response_map = final_response if type(final_response) is dict else {}
    changes = response_map.get("changes")
    changes = changes if type(changes) is dict else {}
    if (
        scenario.expectation.preserve_unaffected
        and first_ids
        and last_ids
        and first_ids.isdisjoint(last_ids)
    ):
        kept = _menu_id_set(changes.get("kept_dishes"))
        change_count = changes.get("change_count")
        result = _append_blocking_once(
            result,
            "dialogue.minimal_change",
            "Turn evidence contradicts the claimed minimal revision.",
            {
                "first_count": len(first_ids),
                "last_count": len(last_ids),
                "declared_kept_count": len(kept),
                "declared_change_count": (
                    change_count if type(change_count) is int else None
                ),
            },
        )

    first_constraints = intermediates[0]["constraints"]
    required_by_field: list[
        tuple[str, tuple[str, ...], bool, set[str]]
    ] = []
    for field, path, persona_field, expand in _CONTEXT_COLLECTION_FIELDS:
        required = _context_collection(first_constraints, path, expand)
        required.update(_persona_context(scenario, persona_field, expand))
        required_by_field.append((field, path, expand, required))

    for turn, item in enumerate(intermediates):
        for field, path, expand, required in required_by_field:
            actual = _context_collection(item["constraints"], path, expand)
            missing = sorted(required - actual)
            if missing:
                result = _append_blocking_once(
                    result,
                    "dialogue.context_consistency",
                    "A persistent hard constraint was lost between dialogue turns.",
                    {"turn": turn, "field": field, "missing": missing},
                )
                return result
    return result


def evaluate_execution(
    scenario: Scenario,
    final_response: object,
    official_recipes: Mapping[int, Recipe],
    *,
    intermediates: tuple[Mapping[str, Any], ...],
    elapsed_ms: float,
    known_gaps_path: Path | None,
) -> ScenarioResult:
    return default_oracle_adapter(
        scenario,
        final_response,
        official_recipes,
        intermediates=intermediates,
        elapsed_ms=elapsed_ms,
        known_gaps_path=known_gaps_path,
    )


class EvaluationRunner:
    def __init__(
        self,
        output_dir: Path | str,
        seed: int,
        mode: str = "quick",
        *,
        recommend_fn: Callable[[int | None, list[str]], object] = recommend,
        session_fn: Callable[..., object] = recommend_with_session,
        clock: Callable[[], float] = time.perf_counter,
        official_recipes: Mapping[int, Recipe] | Iterable[Recipe] | None = None,
        known_gaps_path: Path | None = DEFAULT_KNOWN_GAPS_PATH,
        evaluate_fn: Callable[..., ScenarioResult] = default_oracle_adapter,
        generate_fn: Callable[[int, int], tuple[Scenario, ...]] = generate_scenarios,
        corpus_root: Path | str = _DEFAULT_CORPUS_ROOT,
        include_holdout: bool = False,
        holdout_dir: Path | str | None = None,
        commit_sha: str | None = None,
        minimizer_max_attempts: int = 100,
        minimizer_confirmations: int = 3,
    ) -> None:
        if mode not in MODE_COUNTS:
            raise ValueError(f"unknown evaluation mode: {mode!r}")
        self.output_dir = Path(output_dir)
        self.seed = seed
        self.mode = mode
        self.recommend_fn = recommend_fn
        self.session_fn = session_fn
        self.clock = clock
        self.known_gaps_path = known_gaps_path
        self.evaluate_fn = evaluate_fn
        self.generate_fn = generate_fn
        self.corpus_root = Path(corpus_root)
        self.include_holdout = include_holdout
        env_holdout = os.environ.get("EVAL_HOLDOUT_DIR")
        self.holdout_dir = (
            Path(holdout_dir)
            if holdout_dir is not None
            else Path(env_holdout)
            if env_holdout
            else None
        )
        self.commit_sha = commit_sha or self._read_commit_sha()
        self.minimizer_max_attempts = minimizer_max_attempts
        self.minimizer_confirmations = minimizer_confirmations
        if official_recipes is None:
            self.official_recipes = {item.id: item for item in load_recipes()}
        elif isinstance(official_recipes, Mapping):
            self.official_recipes = dict(official_recipes)
        else:
            self.official_recipes = {item.id: item for item in official_recipes}
        self.intermediates: dict[str, list[dict[str, Any]]] = {}
        self.source_metadata: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _read_commit_sha() -> str:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=_REPO_ROOT,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    def _corpus_directories(self) -> list[tuple[Path, bool, str]]:
        directories: list[tuple[Path, bool, str]] = []
        if self.mode == "deep" and self.include_holdout and self.holdout_dir is not None:
            directories.append((self.holdout_dir, True, "holdout"))
        directories.extend(
            (
                (self.corpus_root / "regressions", False, "regression"),
                (self.corpus_root / "seeds", False, "seed"),
            )
        )
        if self.mode in {"daily", "deep"}:
            directories.extend(
                (
                    (
                        self.corpus_root / "agent_candidates" / "validated",
                        False,
                        "validated_agent_candidate",
                    ),
                    (
                        self.corpus_root / "validated_agent_candidates",
                        False,
                        "validated_agent_candidate",
                    ),
                )
            )
        if self.mode == "deep":
            directories.extend(
                (
                    (self.corpus_root / "long_dialogue", False, "long_dialogue"),
                    (self.corpus_root / "long_dialogues", False, "long_dialogue"),
                )
            )
        return directories

    @staticmethod
    def _load_file(path: Path) -> tuple[Scenario, ...]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid scenario corpus {path}: {error}") from error
        values = payload if type(payload) is list else [payload]
        scenarios: list[Scenario] = []
        for index, value in enumerate(values):
            try:
                scenarios.append(Scenario.from_dict(value))
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"invalid scenario corpus {path}[{index}]: {error}"
                ) from error
        return tuple(scenarios)

    def _load_scenarios(self, count: int) -> tuple[Scenario, ...]:
        loaded: list[Scenario] = []
        loaded_by_source: dict[str, list[Scenario]] = {}
        sources: dict[str, dict[str, Any]] = {}
        for directory, holdout, source_name in self._corpus_directories():
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*.json"), key=lambda item: item.as_posix()):
                for scenario in self._load_file(path):
                    loaded.append(scenario)
                    loaded_by_source.setdefault(source_name, []).append(scenario)
                    sources.setdefault(
                        scenario.scenario_id,
                        {
                            "source": source_name,
                            "holdout": holdout,
                            "scenario_hash": _scenario_hash(scenario),
                        },
                    )

        duplicate_ids = sorted(
            scenario_id
            for scenario_id, frequency in Counter(
                item.scenario_id for item in loaded
            ).items()
            if frequency > 1
        )
        if duplicate_ids:
            raise ValueError(f"duplicate scenario_id values: {duplicate_ids}")

        selected: list[Scenario] = []
        source_offset = 0
        loaded_limit = count - 1
        source_groups = tuple(loaded_by_source.values())
        while len(selected) < loaded_limit:
            added = False
            for group in source_groups:
                if source_offset >= len(group):
                    continue
                selected.append(group[source_offset])
                added = True
                if len(selected) == loaded_limit:
                    break
            if not added:
                break
            source_offset += 1

        remaining = count - len(selected)
        generated = self.generate_fn(self.seed, max(10, remaining))[:remaining]
        selected.extend(generated)
        for scenario in generated:
            sources[scenario.scenario_id] = {
                "source": "generated",
                "holdout": False,
                "scenario_hash": _scenario_hash(scenario),
            }

        ids = [item.scenario_id for item in (*loaded, *generated)]
        duplicates = sorted(
            scenario_id
            for scenario_id, frequency in Counter(ids).items()
            if frequency > 1
        )
        if duplicates:
            raise ValueError(f"duplicate scenario_id values: {duplicates}")
        if len(selected) != count:
            raise ValueError(
                f"scenario provider returned {len(selected)} scenarios; expected {count}"
            )
        self.source_metadata = {item.scenario_id: sources[item.scenario_id] for item in selected}
        return tuple(selected)

    def _elapsed(self, started: float) -> float:
        return round(max(0.0, (self.clock() - started) * 1000.0), 2)

    @staticmethod
    def _intermediate(turn: int, response: object, elapsed_ms: float) -> dict[str, Any]:
        value = response if type(response) is dict else {}
        menu = value.get("menu")
        menu_ids = (
            [item.get("id") for item in menu if type(item) is dict]
            if type(menu) is list
            else []
        )
        return {
            "turn": turn,
            "menu_ids": _safe_json(menu_ids),
            "constraints": _safe_json(value.get("constraints", {})),
            "changes": _safe_json(value.get("changes", {})),
            "elapsed_ms": elapsed_ms,
        }

    @staticmethod
    def _exception_result(
        scenario: Scenario, error: Exception, turn: int, elapsed_ms: float
    ) -> ScenarioResult:
        return ScenarioResult(
            scenario.scenario_id,
            False,
            (
                Violation(
                    "runner.exception",
                    "blocking",
                    "Evaluation scenario raised an exception.",
                    {"exception_type": type(error).__name__, "turn": turn},
                ),
            ),
            elapsed_ms,
        )

    def _execute(
        self, scenario: Scenario, messages: tuple[str, ...]
    ) -> tuple[ScenarioResult, Mapping[str, Any], list[dict[str, Any]], list[float]]:
        response: object = {}
        intermediates: list[dict[str, Any]] = []
        timings: list[float] = []
        turn = 0
        try:
            if scenario.dialogue_mode == "single_turn":
                started = self.clock()
                try:
                    response = self.recommend_fn(
                        scenario.persona.source_user_id, list(messages)
                    )
                except Exception as error:
                    elapsed = self._elapsed(started)
                    timings.append(elapsed)
                    intermediates.append(self._intermediate(0, {}, elapsed))
                    return (
                        self._exception_result(scenario, error, 0, elapsed),
                        {},
                        intermediates,
                        timings,
                    )
                elapsed = self._elapsed(started)
                timings.append(elapsed)
                intermediates.append(self._intermediate(0, response, elapsed))
            else:
                for turn, message in enumerate(messages):
                    started = self.clock()
                    try:
                        if turn == 0:
                            response = self.session_fn(
                                scenario.persona.source_user_id, [message]
                            )
                        else:
                            if type(response) is not dict:
                                raise TypeError("session response must be an object")
                            response = self.session_fn(
                                scenario.persona.source_user_id,
                                [message],
                                session_id=response.get("session_id"),
                                menu_version=response.get("menu_version"),
                                is_delta=True,
                            )
                    except Exception as error:
                        elapsed = self._elapsed(started)
                        timings.append(elapsed)
                        intermediates.append(self._intermediate(turn, {}, elapsed))
                        return (
                            self._exception_result(scenario, error, turn, elapsed),
                            {},
                            intermediates,
                            timings,
                        )
                    elapsed = self._elapsed(started)
                    timings.append(elapsed)
                    intermediates.append(self._intermediate(turn, response, elapsed))

            final_elapsed = timings[-1] if timings else 0.0
            replay_scenario = replace(scenario, messages=messages)
            try:
                result = self.evaluate_fn(
                    replay_scenario,
                    response,
                    self.official_recipes,
                    intermediates=tuple(intermediates),
                    elapsed_ms=final_elapsed,
                    known_gaps_path=self.known_gaps_path,
                )
            except Exception as error:
                result = self._exception_result(scenario, error, turn, final_elapsed)
            response_map = response if type(response) is dict else {}
            return result, response_map, intermediates, timings
        except Exception as error:
            return self._exception_result(scenario, error, turn, 0.0), {}, intermediates, timings

    def run_mode(self) -> EvaluationReport:
        return self.run_count(MODE_COUNTS[self.mode])

    def run_count(self, count: int) -> EvaluationReport:
        if count < 10:
            raise ValueError("count must be at least 10")
        scenarios = self._load_scenarios(count)
        self.intermediates = {}
        results: list[ScenarioResult] = []
        reviewed_rows: list[tuple[Scenario, Mapping[str, Any]]] = []
        failures: list[FailureRecord] = []
        minimizations: dict[str, dict[str, Any]] = {}
        all_timings: list[float] = []

        for scenario in scenarios:
            result, response, intermediates, timings = self._execute(
                scenario, scenario.messages
            )
            self.intermediates[scenario.scenario_id] = intermediates
            results.append(result)
            reviewed_rows.append((scenario, response))
            all_timings.extend(timings)
            blocking = tuple(
                violation
                for violation in result.violations
                if violation.severity == "blocking"
            )
            if not blocking:
                continue

            target_code = blocking[0].code

            def evaluate_codes(candidate: tuple[str, ...]) -> tuple[str, ...]:
                replay_result, _, _, _ = self._execute(scenario, candidate)
                return tuple(
                    violation.code
                    for violation in replay_result.violations
                    if violation.severity == "blocking"
                )

            minimized = minimize_failure(
                scenario.messages,
                target_code,
                evaluate_codes,
                max_attempts=self.minimizer_max_attempts,
                confirmations=self.minimizer_confirmations,
            )
            minimizations[scenario.scenario_id] = {
                "attempts": minimized.attempts,
                "reached_cap": minimized.reached_cap,
            }
            failures.append(
                FailureRecord(
                    scenario.scenario_id,
                    self.seed,
                    self.commit_sha,
                    scenario.messages,
                    minimized.messages,
                    result.violations,
                    result.elapsed_ms,
                )
            )

        try:
            coverage = summarize_coverage(scenarios)
        except ValueError:
            coverage = _fallback_coverage(scenarios)
        counts = _violation_counts(results)
        known_gap_scenarios = sum(
            any(item.severity == "known_gap" for item in result.violations)
            for result in results
        )
        metrics: dict[str, Any] = {
            "reviewed_labels": compute_reviewed_metrics(reviewed_rows),
            "known_gap_scenarios": known_gap_scenarios,
            "violation_counts": counts,
        }
        report = EvaluationReport(
            total=count,
            passed=count - len(failures),
            failures=tuple(failures),
            coverage=coverage,
            metrics=metrics,
            timings=summarize_timings(all_timings),
        )
        failures_dir = self.output_dir / "failures"
        failures_dir.mkdir(parents=True, exist_ok=True)
        for path in failures_dir.glob("*.json"):
            if path.is_file():
                path.unlink()
        write_report(
            report,
            self.output_dir,
            metadata={
                "commit_sha": self.commit_sha,
                "seed": self.seed,
                "mode": self.mode,
                "include_holdout": self.include_holdout,
            },
            intermediates=self.intermediates,
            minimizations=minimizations,
            source_metadata=self.source_metadata,
        )
        return report
