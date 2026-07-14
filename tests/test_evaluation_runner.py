from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from app.data_loader import load_recipes
from tests.evaluation.failure_minimizer import minimize_failure
from tests.evaluation.report import (
    compute_reviewed_metrics,
    safe_scenario_filename,
    write_report,
)
from tests.evaluation.runner import (
    EvaluationRunner,
    MODE_COUNTS,
    default_oracle_adapter,
)
from scripts.run_evaluation import build_parser, default_output_dir, main
from tests.evaluation.schemas import (
    EvaluationReport,
    FailureRecord,
    HealthPersona,
    MenuExpectation,
    Scenario,
    ScenarioResult,
    Violation,
)


class EvaluationModuleContractTests(unittest.TestCase):
    def test_evaluation_modules_are_importable(self) -> None:
        from tests.evaluation.failure_minimizer import minimize_failure
        from tests.evaluation.report import write_report
        from tests.evaluation.runner import EvaluationRunner

        self.assertTrue(callable(minimize_failure))
        self.assertTrue(callable(write_report))
        self.assertTrue(callable(EvaluationRunner))


class FailureMinimizerTests(unittest.TestCase):
    def test_rejects_candidate_that_reproduces_only_two_of_three_times(self) -> None:
        calls = 0

        def unstable(messages: tuple[str, ...]) -> tuple[str, ...]:
            nonlocal calls
            if messages == ("第二轮",):
                calls += 1
                return ("target.failure",) if calls < 3 else ()
            return ()

        result = minimize_failure(
            ("第一轮", "第二轮"), "target.failure", unstable
        )

        self.assertEqual(result.messages, ("第一轮", "第二轮"))
        self.assertEqual(result.attempts, 4)
        self.assertFalse(result.reached_cap)

    def test_accepts_candidate_after_three_consecutive_confirmations(self) -> None:
        result = minimize_failure(
            ("可删除", "保留"),
            "target.failure",
            lambda messages: ("target.failure",) if messages == ("保留",) else (),
        )

        self.assertEqual(result.messages, ("保留",))
        self.assertEqual(result.attempts, 3)
        self.assertFalse(result.reached_cap)

    def test_attempts_whole_turn_removal_before_clause_removal(self) -> None:
        candidates: list[tuple[str, ...]] = []

        def record(messages: tuple[str, ...]) -> tuple[str, ...]:
            candidates.append(messages)
            return ()

        minimize_failure(("甲，乙。", "第二轮"), "target.failure", record)

        self.assertEqual(candidates[0], ("第二轮",))
        self.assertEqual(candidates[1], ("甲，乙。",))
        self.assertIn(("乙。", "第二轮"), candidates[2:])

    def test_removes_chinese_and_english_punctuation_delimited_clauses(self) -> None:
        result = minimize_failure(
            ("删除，保留;也删除！",),
            "target.failure",
            lambda messages: ("target.failure",) if "保留" in messages[0] else (),
        )

        self.assertEqual(result.messages, ("保留;",))

    def test_never_exceeds_attempt_cap_or_accepts_partial_confirmation(self) -> None:
        result = minimize_failure(
            ("删除", "保留"),
            "target.failure",
            lambda messages: ("target.failure",),
            max_attempts=2,
        )

        self.assertEqual(result.messages, ("删除", "保留"))
        self.assertEqual(result.attempts, 2)
        self.assertTrue(result.reached_cap)


class EvaluationReportTests(unittest.TestCase):
    @staticmethod
    def scenario(
        scenario_id: str,
        *,
        persona: HealthPersona,
        expectation: MenuExpectation,
        intent: str,
    ) -> Scenario:
        return Scenario(
            scenario_id=scenario_id,
            persona=persona,
            messages=("测试消息",),
            expectation=expectation,
            seed=7,
            intent=intent,
        )

    def test_reviewed_label_metrics_have_exact_counts_and_scores(self) -> None:
        structure = self.scenario(
            "structure",
            persona=HealthPersona(
                "p1",
                "special_group",
                special_groups=("孕妇",),
                allergens=("花生",),
                health_goals=("控糖",),
            ),
            expectation=MenuExpectation(dish_count=2, meat_count=1, vegetable_count=1),
            intent="structure_ratio",
        )
        conflict = self.scenario(
            "conflict",
            persona=HealthPersona(
                "p2", "healthy", health_goals=("低盐",)
            ),
            expectation=MenuExpectation(dish_count=3),
            intent="multi_person_conflict",
        )
        structure_response = {
            "menu": [{"id": 1}, {"id": 2}],
            "constraints": {
                "inferred_profile": {
                    "special_groups": ["孕妇", "哺乳期"],
                    "allergens": [],
                },
                "allergens": [],
                "health_goals": ["控糖"],
                "meat_count": 1,
                "vegetable_count": 1,
            },
        }
        conflict_response = {
            "menu": [{"id": 1}, {"id": 2}],
            "constraints": {
                "inferred_profile": {"special_groups": [], "allergens": []},
                "allergens": ["坚果"],
                "health_goals": [],
                "people_count": 1,
            },
        }
        metrics = compute_reviewed_metrics(
            (
                (structure, structure_response),
                (conflict, conflict_response),
            )
        )

        self.assertEqual(
            set(metrics),
            {
                "special_groups",
                "allergens",
                "health_goals",
                "dish_count",
                "people_count",
                "structure_intent",
                "overall",
            },
        )
        self.assertEqual(
            metrics["special_groups"],
            {"tp": 1, "fp": 1, "fn": 0, "precision": 0.5, "recall": 1.0, "f1": 0.6667},
        )
        self.assertEqual(
            metrics["allergens"],
            {"tp": 0, "fp": 1, "fn": 1, "precision": 0.0, "recall": 0.0, "f1": 0.0},
        )
        self.assertEqual(
            metrics["health_goals"],
            {"tp": 1, "fp": 0, "fn": 1, "precision": 1.0, "recall": 0.5, "f1": 0.6667},
        )
        self.assertEqual(metrics["dish_count"]["tp"], 1)
        self.assertEqual(metrics["dish_count"]["fp"], 1)
        self.assertEqual(metrics["dish_count"]["fn"], 1)
        self.assertEqual(metrics["people_count"]["tp"], 0)
        self.assertEqual(metrics["people_count"]["fp"], 1)
        self.assertEqual(metrics["people_count"]["fn"], 1)
        self.assertEqual(metrics["structure_intent"]["tp"], 1)
        self.assertEqual(metrics["structure_intent"]["precision"], 1.0)
        self.assertEqual(
            metrics["overall"],
            {"tp": 4, "fp": 4, "fn": 4, "precision": 0.5, "recall": 0.5, "f1": 0.5},
        )

    def test_structure_metric_ignores_oracle_result_without_explicit_response_fields(self) -> None:
        scenario = self.scenario(
            "structure-without-evidence",
            persona=HealthPersona("p", "healthy"),
            expectation=MenuExpectation(dish_count=None, meat_count=1, vegetable_count=2),
            intent="structure_ratio",
        )
        response = {
            "menu": [],
            "constraints": {
                "inferred_profile": {"special_groups": [], "allergens": []},
                "allergens": [],
                "health_goals": [],
            },
        }

        metrics = compute_reviewed_metrics(
            ((scenario, response, ScenarioResult(scenario.scenario_id, True, (), 1.0)),)
        )

        self.assertEqual(
            metrics["structure_intent"],
            {"tp": 0, "fp": 0, "fn": 1, "precision": 0.0, "recall": 0.0, "f1": 0.0},
        )

    def test_write_report_creates_four_file_types_with_stable_json(self) -> None:
        violation = Violation("runner.failure", "blocking", "Failed", None)
        failure = FailureRecord(
            "../unsafe scenario",
            7,
            "abc123",
            ("原始消息",),
            ("最小消息",),
            (violation,),
            12.5,
        )
        report = EvaluationReport(
            20,
            19,
            (failure,),
            {"primary_bucket": {"healthy": 20}, "intent": {}, "pairs": {}},
            {"known_gap_scenarios": 0},
            {"p50_ms": 1.0, "p95_ms": 2.0},
        )
        metadata = {"commit_sha": "abc123", "seed": 7, "mode": "quick"}

        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = Path(first_dir)
            second = Path(second_dir)
            write_report(
                report,
                first,
                metadata=metadata,
                intermediates={failure.scenario_id: ({"turn": 0, "menu_ids": [1]},)},
                minimizations={failure.scenario_id: {"attempts": 3, "reached_cap": False}},
            )
            write_report(report, second, metadata=metadata)

            self.assertTrue((first / "summary.json").is_file())
            self.assertTrue((first / "summary.md").is_file())
            self.assertTrue((first / "coverage.json").is_file())
            failure_files = list((first / "failures").glob("*.json"))
            self.assertEqual(len(failure_files), 1)
            self.assertEqual(failure_files[0].parent, first / "failures")
            payload = json.loads((first / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["commit_sha"], "abc123")
            self.assertEqual(payload["seed"], 7)
            self.assertIn("violation_counts", payload)
            self.assertEqual(
                (first / "summary.json").read_text(encoding="utf-8"),
                (second / "summary.json").read_text(encoding="utf-8"),
            )

    def test_holdout_failure_artifact_contains_only_hash_and_aggregate_codes(self) -> None:
        failure = FailureRecord(
            "private/secret-case",
            9,
            "deadbeef",
            ("绝密原始消息",),
            ("绝密最小消息",),
            (Violation("secret.failure", "blocking", "secret detail", {"expected": "secret"}),),
            8.0,
        )
        report = EvaluationReport(1, 0, (failure,), {}, {}, {})

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_report(
                report,
                output,
                metadata={"seed": 9, "mode": "deep", "commit_sha": "deadbeef"},
                source_metadata={
                    failure.scenario_id: {
                        "holdout": True,
                        "scenario_hash": "0123456789abcdef",
                    }
                },
            )

            artifact_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output.rglob("*")
                if path.is_file()
            )
            self.assertNotIn("绝密原始消息", artifact_text)
            self.assertNotIn("绝密最小消息", artifact_text)
            self.assertNotIn("secret detail", artifact_text)
            failure_payload = json.loads(
                next((output / "failures").glob("*.json")).read_text(encoding="utf-8")
            )
            self.assertEqual(failure_payload["scenario_hash"], "0123456789abcdef")
            self.assertEqual(failure_payload["violation_codes"], {"secret.failure": 1})

    def test_safe_failure_filenames_are_stable_distinct_and_do_not_overwrite(self) -> None:
        first_id = "a/b"
        second_id = "a?b"
        first_name = safe_scenario_filename(first_id)
        second_name = safe_scenario_filename(second_id)

        self.assertNotEqual(first_name, second_name)
        self.assertRegex(first_name, r"^[A-Za-z0-9._-]+-[0-9a-f]{12}$")
        self.assertRegex(second_name, r"^[A-Za-z0-9._-]+-[0-9a-f]{12}$")
        self.assertLessEqual(len(first_name), 93)
        self.assertEqual(first_name, safe_scenario_filename(first_id))

        violation = Violation("blocking", "blocking", "failed", None)
        failures = tuple(
            FailureRecord(
                scenario_id,
                1,
                "sha",
                ("original",),
                ("minimal",),
                (violation,),
                1.0,
            )
            for scenario_id in (first_id, second_id)
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_report(EvaluationReport(2, 0, failures, {}, {}, {}), output)

            names = sorted(path.name for path in (output / "failures").glob("*.json"))
            self.assertEqual(names, sorted([first_name + ".json", second_name + ".json"]))


class StepClock:
    def __init__(self, step: float = 0.001) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.value
        self.value += self.step
        return value


class EvaluationRunnerTests(unittest.TestCase):
    @staticmethod
    def scenarios(seed: int, count: int) -> tuple[Scenario, ...]:
        values = []
        for index in range(count):
            multi = index == 0
            values.append(
                Scenario(
                    scenario_id=f"case-{index}",
                    persona=HealthPersona(f"p-{index}", "healthy"),
                    messages=("第一轮", "第二轮") if multi else ("推荐两道晚餐",),
                    expectation=MenuExpectation(dish_count=2),
                    seed=seed,
                    intent="relative_revision" if multi else "hard_constraint",
                    dialogue_mode="multi_turn" if multi else "single_turn",
                )
            )
        return tuple(values)

    @staticmethod
    def response(version: int = 1) -> dict[str, object]:
        return {
            "menu": [{"id": 1}, {"id": 2}],
            "constraints": {
                "inferred_profile": {"special_groups": [], "allergens": []},
                "allergens": [],
                "health_goals": [],
                "people_count": 1,
            },
            "changes": {"mode": "minimal_revision"},
            "score_card": {"minimal_change": True},
            "session_id": "random-session-value",
            "menu_version": version,
        }

    @staticmethod
    def evaluator(
        scenario: Scenario,
        response: object,
        official_recipes: object,
        *,
        intermediates: tuple[dict[str, object], ...],
        elapsed_ms: float,
        known_gaps_path: Path | None,
    ) -> ScenarioResult:
        violations = (
            (Violation("fake.blocking", "blocking", "failure", None),)
            if scenario.scenario_id == "case-1"
            else ()
        )
        return ScenarioResult(
            scenario.scenario_id,
            not violations,
            violations,
            elapsed_ms,
        )

    def build_runner(self, output: Path, **overrides: object) -> EvaluationRunner:
        options = {
            "recommend_fn": lambda user_id, messages: self.response(),
            "session_fn": lambda user_id, messages, **kwargs: self.response(
                int(kwargs.get("menu_version") or 0) + 1
            ),
            "clock": StepClock(),
            "official_recipes": {},
            "evaluate_fn": self.evaluator,
            "generate_fn": self.scenarios,
            "commit_sha": "fixedsha",
            "corpus_root": output / "missing-corpus",
        }
        options.update(overrides)
        mode = str(options.pop("mode", "quick"))
        return EvaluationRunner(output, seed=17, mode=mode, **options)

    def test_mode_counts_are_exact(self) -> None:
        self.assertEqual(MODE_COUNTS, {"quick": 120, "daily": 2000, "deep": 10000})

    def test_run_count_writes_report_and_reproducible_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            runner = self.build_runner(output)
            report = runner.run_count(20)

            self.assertEqual(report.total, 20)
            self.assertEqual(report.passed, 19)
            self.assertEqual(len(report.failures), 1)
            self.assertEqual(report.failures[0].seed, 17)
            self.assertEqual(report.failures[0].commit_sha, "fixedsha")
            self.assertTrue(report.failures[0].minimized_messages)
            self.assertTrue((output / "summary.json").is_file())
            self.assertTrue((output / "summary.md").is_file())
            self.assertTrue((output / "coverage.json").is_file())
            self.assertEqual(len(list((output / "failures").glob("*.json"))), 1)
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["seed"], 17)
            self.assertEqual(summary["commit_sha"], "fixedsha")

    def test_multi_turn_uses_delta_version_and_records_sanitized_intermediates(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        received_evidence: list[tuple[dict[str, object], ...]] = []

        def session(user_id: int | None, messages: list[str], **kwargs: object) -> dict[str, object]:
            calls.append((messages, kwargs))
            return self.response(int(kwargs.get("menu_version") or 0) + 1)

        def evaluator(
            scenario: Scenario,
            response: object,
            official_recipes: object,
            *,
            intermediates: tuple[dict[str, object], ...],
            elapsed_ms: float,
            known_gaps_path: Path | None,
        ) -> ScenarioResult:
            if scenario.scenario_id == "case-0":
                received_evidence.append(intermediates)
            return ScenarioResult(scenario.scenario_id, True, (), elapsed_ms)

        with tempfile.TemporaryDirectory() as directory:
            runner = self.build_runner(
                Path(directory), session_fn=session, evaluate_fn=evaluator
            )
            runner.run_count(10)

            self.assertEqual(calls[0], (["第一轮"], {}))
            self.assertEqual(calls[1][0], ["第二轮"])
            self.assertEqual(
                calls[1][1],
                {
                    "session_id": "random-session-value",
                    "menu_version": 1,
                    "is_delta": True,
                },
            )
            turns = runner.intermediates["case-0"]
            self.assertEqual([item["turn"] for item in turns], [0, 1])
            self.assertEqual([item["menu_ids"] for item in turns], [[1, 2], [1, 2]])
            self.assertNotIn("session_id", json.dumps(turns, ensure_ascii=False))
            self.assertEqual(len(received_evidence), 1)
            self.assertIsInstance(received_evidence[0], tuple)
            self.assertEqual([item["turn"] for item in received_evidence[0]], [0, 1])

    def test_default_adapter_blocks_false_minimal_change_claim_from_turn_evidence(self) -> None:
        scenario = Scenario(
            "multi-evidence",
            HealthPersona("p", "healthy"),
            ("第一轮", "全部换掉"),
            MenuExpectation(dish_count=2, preserve_unaffected=True),
            7,
            intent="relative_revision",
            dialogue_mode="multi_turn",
        )
        response = self.response(2)
        response["changes"] = {
            "mode": "minimal_revision",
            "kept_dishes": [1],
            "change_count": 1,
        }
        result = default_oracle_adapter(
            scenario,
            response,
            {},
            intermediates=(
                {"turn": 0, "menu_ids": [1, 2], "constraints": {}, "changes": {}},
                {"turn": 1, "menu_ids": [3, 4], "constraints": {}, "changes": response["changes"]},
            ),
            elapsed_ms=1.0,
            known_gaps_path=None,
        )

        self.assertEqual(
            [item.code for item in result.violations].count("dialogue.minimal_change"),
            1,
        )
        self.assertIn(
            "dialogue.minimal_change", {item.code for item in result.violations}
        )

    def test_default_adapter_blocks_lost_allergen_context(self) -> None:
        scenario = Scenario(
            "context-evidence",
            HealthPersona("p", "healthy", allergens=("花生",)),
            ("我对花生过敏", "再少一道菜"),
            MenuExpectation(dish_count=2, forbidden_terms=("花生",)),
            7,
            intent="relative_revision",
            dialogue_mode="multi_turn",
        )
        response = self.response(2)
        result = default_oracle_adapter(
            scenario,
            response,
            {},
            intermediates=(
                {
                    "turn": 0,
                    "menu_ids": [1, 2],
                    "constraints": {"allergens": ["花生"], "avoid_ingredients": []},
                    "changes": {},
                },
                {
                    "turn": 1,
                    "menu_ids": [1, 2],
                    "constraints": {"allergens": [], "avoid_ingredients": []},
                    "changes": {},
                },
            ),
            elapsed_ms=1.0,
            known_gaps_path=None,
        )

        self.assertIn(
            "dialogue.context_consistency", {item.code for item in result.violations}
        )

    def test_default_adapter_requires_evidence_for_every_turn(self) -> None:
        scenario = Scenario(
            "missing-turn-evidence",
            HealthPersona("p", "healthy"),
            ("第一轮", "第二轮"),
            MenuExpectation(dish_count=2),
            7,
            intent="relative_revision",
            dialogue_mode="multi_turn",
        )

        result = default_oracle_adapter(
            scenario,
            self.response(2),
            {},
            intermediates=(
                {"turn": 0, "menu_ids": [1, 2], "constraints": {}, "changes": {}},
            ),
            elapsed_ms=1.0,
            known_gaps_path=None,
        )

        self.assertIn(
            "dialogue.execution_evidence", {item.code for item in result.violations}
        )

    def test_minimizer_replay_uses_adapter_with_complete_candidate_intermediates(self) -> None:
        received_turn_counts: list[int] = []

        def evaluator(
            scenario: Scenario,
            response: object,
            official_recipes: object,
            *,
            intermediates: tuple[dict[str, object], ...],
            elapsed_ms: float,
            known_gaps_path: Path | None,
        ) -> ScenarioResult:
            if scenario.scenario_id == "case-0":
                received_turn_counts.append(len(intermediates))
                violations = (
                    Violation("replay.block", "blocking", "blocking", None),
                )
            else:
                violations = ()
            return ScenarioResult(
                scenario.scenario_id, not violations, violations, elapsed_ms
            )

        with tempfile.TemporaryDirectory() as directory:
            self.build_runner(
                Path(directory),
                evaluate_fn=evaluator,
                minimizer_max_attempts=3,
            ).run_count(10)

        self.assertEqual(received_turn_counts, [2, 1, 1, 1])

    def test_exception_is_isolated_as_stable_blocking_violation(self) -> None:
        def recommend(user_id: int | None, messages: list[str]) -> dict[str, object]:
            if messages == ["推荐两道晚餐"]:
                raise LookupError("unstable details must not be recorded")
            return self.response()

        with tempfile.TemporaryDirectory() as directory:
            runner = self.build_runner(Path(directory), recommend_fn=recommend)
            report = runner.run_count(10)

            self.assertEqual(report.passed, 1)
            self.assertEqual(len(report.failures), 9)
            violation = report.failures[0].violations[0]
            self.assertEqual(violation.code, "runner.exception")
            self.assertEqual(
                violation.to_dict()["evidence"],
                {"exception_type": "LookupError", "turn": 0},
            )
            self.assertNotIn("unstable details", json.dumps(report.to_dict()))

    def test_known_gap_and_soft_review_do_not_block_or_create_failure_record(self) -> None:
        def evaluator(
            scenario: Scenario,
            response: object,
            official_recipes: object,
            *,
            intermediates: tuple[dict[str, object], ...],
            elapsed_ms: float,
            known_gaps_path: Path | None,
        ) -> ScenarioResult:
            if scenario.scenario_id == "case-0":
                violations = (Violation("known.one", "known_gap", "known", None),)
            elif scenario.scenario_id == "case-1":
                violations = (Violation("soft.one", "soft_review", "soft", None),)
            elif scenario.scenario_id == "case-2":
                violations = (Violation("block.one", "blocking", "block", None),)
            else:
                violations = ()
            return ScenarioResult(
                scenario.scenario_id,
                not any(item.severity == "blocking" for item in violations),
                violations,
                elapsed_ms,
            )

        with tempfile.TemporaryDirectory() as directory:
            report = self.build_runner(Path(directory), evaluate_fn=evaluator).run_count(10)

            self.assertEqual(report.passed, 9)
            self.assertEqual([item.scenario_id for item in report.failures], ["case-2"])
            self.assertEqual(report.metrics["known_gap_scenarios"], 1)
            self.assertEqual(report.metrics["violation_counts"]["by_severity"]["known_gap"], 1)
            self.assertEqual(report.metrics["violation_counts"]["by_severity"]["soft_review"], 1)
            summary = json.loads(
                (Path(directory) / "summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["violation_counts"]["by_severity"]["known_gap"], 1)
            self.assertEqual(summary["violation_counts"]["by_severity"]["soft_review"], 1)

    def test_injected_clock_and_sha_make_runner_report_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = Path(first_dir)
            second = Path(second_dir)
            self.build_runner(first).run_count(10)
            self.build_runner(second).run_count(10)

            self.assertEqual(
                (first / "summary.json").read_text(encoding="utf-8"),
                (second / "summary.json").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                next((first / "failures").glob("*.json")).read_text(encoding="utf-8"),
                next((second / "failures").glob("*.json")).read_text(encoding="utf-8"),
            )

    def test_malformed_corpus_and_duplicate_ids_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regressions = root / "regressions"
            regressions.mkdir()
            (regressions / "bad.json").write_text("{bad", encoding="utf-8")
            runner = self.build_runner(root / "out", corpus_root=root)
            with self.assertRaisesRegex(ValueError, "bad.json"):
                runner.run_count(10)

            (regressions / "bad.json").write_text(
                json.dumps([self.scenarios(17, 10)[0].to_dict()] * 2, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate scenario_id"):
                runner.run_count(10)

    def test_holdout_requires_strict_schema_and_never_leaks_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            holdout = root / "holdout"
            holdout.mkdir()
            private = self.scenarios(17, 10)[1].to_dict()
            private["scenario_id"] = "private-case"
            private["messages"] = ["private-message-never-write"]
            private["unknown"] = True
            path = holdout / "private.json"
            path.write_text(json.dumps(private, ensure_ascii=False), encoding="utf-8")
            runner = self.build_runner(
                root / "out",
                mode="deep",
                include_holdout=True,
                holdout_dir=holdout,
            )
            with self.assertRaisesRegex(ValueError, "private.json"):
                runner.run_count(10)

            del private["unknown"]
            path.write_text(json.dumps(private, ensure_ascii=False), encoding="utf-8")
            def fail_private(
                scenario: Scenario,
                response: object,
                official_recipes: object,
                *,
                intermediates: tuple[dict[str, object], ...],
                elapsed_ms: float,
                known_gaps_path: Path | None,
            ) -> ScenarioResult:
                violations = (
                    (Violation("private.block", "blocking", "private failure", None),)
                    if scenario.scenario_id == "private-case"
                    else ()
                )
                return ScenarioResult(
                    scenario.scenario_id, not violations, violations, elapsed_ms
                )

            runner = self.build_runner(
                root / "out",
                mode="deep",
                include_holdout=True,
                holdout_dir=holdout,
                evaluate_fn=fail_private,
            )
            report = runner.run_count(10)
            self.assertEqual(report.total, 10)
            artifact_text = "\n".join(
                item.read_text(encoding="utf-8")
                for item in (root / "out").rglob("*")
                if item.is_file()
            )
            self.assertNotIn("private-message-never-write", artifact_text)

    def test_small_real_recommendation_smoke(self) -> None:
        recipes = {recipe.id: recipe for recipe in load_recipes()}

        def smoke_scenarios(seed: int, count: int) -> tuple[Scenario, ...]:
            return tuple(
                Scenario(
                    f"smoke-{index}",
                    HealthPersona(f"smoke-p-{index}", "healthy"),
                    ("推荐两道晚餐",),
                    MenuExpectation(dish_count=2),
                    seed,
                    intent="hard_constraint",
                )
                for index in range(count)
            )

        with tempfile.TemporaryDirectory() as directory:
            report = EvaluationRunner(
                Path(directory),
                seed=3,
                official_recipes=recipes,
                generate_fn=smoke_scenarios,
                corpus_root=Path(directory) / "missing",
                minimizer_max_attempts=0,
                commit_sha="smoke",
            ).run_count(10)

            self.assertEqual(report.total, 10)


class EvaluationCliTests(unittest.TestCase):
    def test_parser_and_default_utc_output(self) -> None:
        arguments = build_parser().parse_args(
            [
                "--mode",
                "deep",
                "--seed",
                "42",
                "--count",
                "20",
                "--include-holdout",
                "--holdout-dir",
                "private",
            ]
        )

        self.assertEqual(arguments.mode, "deep")
        self.assertEqual(arguments.seed, 42)
        self.assertEqual(arguments.count, 20)
        self.assertTrue(arguments.include_holdout)
        self.assertEqual(arguments.holdout_dir, Path("private"))
        self.assertEqual(
            default_output_dir(datetime(2026, 7, 14, 1, 2, 3, tzinfo=timezone.utc)),
            Path("artifacts/evaluation/20260714T010203Z"),
        )

    def test_cli_exit_code_depends_only_on_blocking_failures(self) -> None:
        known_gap_report = EvaluationReport(
            10,
            10,
            (),
            {},
            {"known_gap_scenarios": 1},
            {},
        )
        failure = FailureRecord(
            "failed",
            3,
            "sha",
            ("original",),
            ("minimal",),
            (Violation("block", "blocking", "blocking", None),),
            1.0,
        )
        blocking_report = EvaluationReport(10, 9, (failure,), {}, {}, {})

        class FakeRunner:
            def __init__(self, report: EvaluationReport) -> None:
                self.report = report

            def run_count(self, count: int) -> EvaluationReport:
                return self.report

            def run_mode(self) -> EvaluationReport:
                return self.report

        with tempfile.TemporaryDirectory() as directory:
            arguments = [
                "--mode",
                "quick",
                "--seed",
                "3",
                "--count",
                "10",
                "--output",
                directory,
            ]
            self.assertEqual(
                main(arguments, runner_factory=lambda *args, **kwargs: FakeRunner(known_gap_report)),
                0,
            )
            self.assertEqual(
                main(arguments, runner_factory=lambda *args, **kwargs: FakeRunner(blocking_report)),
                1,
            )

    def test_gitignore_keeps_data_rules_and_ignores_evaluation_artifacts(self) -> None:
        content = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(
            encoding="utf-8"
        )

        self.assertIn("data/*.csv", content)
        self.assertIn("data/*.json", content)
        self.assertIn("!data/README.md", content)
        self.assertIn("artifacts/evaluation/", content)


if __name__ == "__main__":
    unittest.main()
