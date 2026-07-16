from __future__ import annotations

import io
import json
import math
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import manage_evaluation_issue as manage_issue_cli
from scripts import run_autonomous_cycle as cli_module
from scripts.run_autonomous_cycle import main
from tests.evaluation import autonomous_cycle
from tests.evaluation.autonomous_cycle import run_cycle
from tests.evaluation.issue_registry import IssueRegistry
from tests.evaluation.schemas import (
    EvaluationReport,
    FailureRecord,
    HealthPersona,
    MenuExpectation,
    Scenario,
    Violation,
)


class StepClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


class SequenceUtcNow:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> str:
        return next(self._values)


class RaisingClock:
    def __call__(self) -> float:
        raise RuntimeError("clock unavailable")


class FakeRunner:
    def __init__(self, report: EvaluationReport, context: dict[str, object]) -> None:
        self._report = report
        self.scenario_context = context
        self.calls = 0

    def run_mode(self) -> EvaluationReport:
        self.calls += 1
        return self._report


class RecordingFactory:
    def __init__(
        self,
        reports: dict[int, EvaluationReport] | None = None,
        errors: dict[int, Exception] | None = None,
        contexts: dict[int, dict[str, object]] | None = None,
    ) -> None:
        self.reports = reports or {}
        self.errors = errors or {}
        self.contexts = contexts or {}
        self.calls: list[tuple[Path, int, str]] = []

    def __call__(self, output_dir: Path, *, seed: int, mode: str) -> FakeRunner:
        self.calls.append((Path(output_dir), seed, mode))
        if seed in self.errors:
            raise self.errors[seed]
        report = self.reports.get(seed, EvaluationReport(4, 4, (), {}, {}, {}))
        context = self.contexts.get(seed, {f"scenario-{seed}": {"seed": seed}})
        return FakeRunner(report, context)


class FakeRegistry:
    def __init__(self, issue_ids: dict[int, tuple[str, ...]] | None = None) -> None:
        self.issue_ids = issue_ids or {}
        self.calls: list[
            tuple[EvaluationReport, dict[str, object], str, str | None]
        ] = []

    def ingest(
        self,
        report: EvaluationReport,
        scenario_context: dict[str, object],
        *,
        observed_at: str,
        observation_id: str | None = None,
    ) -> tuple[str, ...]:
        self.calls.append((report, scenario_context, observed_at, observation_id))
        seed = int(next(iter(scenario_context.values()))["seed"])
        return self.issue_ids.get(seed, ())


class AutonomousCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository_root = Path(self.temporary_directory.name) / "repo"
        self.repository_root.mkdir()
        self.root = self.repository_root / "artifacts" / "evaluation"

    @staticmethod
    def utc_now() -> str:
        return "2026-07-16T01:02:03Z"

    def execute_cycle(
        self,
        *,
        cycle_id: str = "daily.2026-07-16",
        mode: str = "quick",
        rounds: int = 3,
        base_seed: int = 40,
        factory: RecordingFactory | None = None,
        registry: FakeRegistry | None = None,
        continue_on_error: bool = False,
        clock: StepClock | None = None,
    ) -> dict[str, object]:
        return run_cycle(
            self.root,
            cycle_id,
            mode,
            rounds,
            base_seed,
            continue_on_error=continue_on_error,
            runner_factory=factory or RecordingFactory(),
            registry=registry or FakeRegistry(),
            clock=clock
            or StepClock(*([1.0, 1.125] * (rounds if type(rounds) is int and rounds > 0 else 1))),
            utc_now=self.utc_now,
            commit_sha="abc123",
            repository_root=self.repository_root,
        )

    def cycle_dir(self, cycle_id: str = "daily.2026-07-16") -> Path:
        return self.root / "cycles" / cycle_id

    def load_cycle(self, cycle_id: str = "daily.2026-07-16") -> dict[str, object]:
        return json.loads(
            (self.cycle_dir(cycle_id) / "cycle.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def blocking_evaluation(seed: int = 40) -> tuple[EvaluationReport, dict[str, object]]:
        scenario = Scenario(
            f"scenario-{seed}",
            HealthPersona(f"persona-{seed}", "single_condition"),
            ("Plan dinner without peanuts.",),
            MenuExpectation(forbidden_terms=("peanut",)),
            seed,
            "hard_constraint",
        )
        failure = FailureRecord(
            scenario.scenario_id,
            seed,
            "abc123",
            scenario.messages,
            ("No peanuts.",),
            (
                Violation(
                    "constraint.forbidden_term",
                    "blocking",
                    "Found peanut.",
                    {"term": "peanut"},
                ),
            ),
            1.0,
        )
        context = {
            "holdout": False,
            "health_bucket": scenario.persona.primary_bucket,
            "intent": scenario.intent,
            "expectation": scenario.expectation.to_dict(),
            "scenario": scenario.to_dict(),
        }
        return EvaluationReport(1, 0, (failure,), {}, {}, {}), {
            scenario.scenario_id: context
        }

    @staticmethod
    def make_directory_link(link: Path, target: Path) -> bool:
        try:
            link.symlink_to(target, target_is_directory=True)
            return True
        except OSError:
            if os.name != "nt":
                return False
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0

    def test_runs_deterministic_seeds_directories_and_persists_stable_state(self) -> None:
        factory = RecordingFactory()
        state = self.execute_cycle(factory=factory, clock=StepClock(1.0, 1.1, 2.0, 2.2, 3.0, 3.3))

        expected_dirs = [
            self.cycle_dir() / "rounds" / "0001-40",
            self.cycle_dir() / "rounds" / "0002-41",
            self.cycle_dir() / "rounds" / "0003-42",
        ]
        self.assertEqual(
            factory.calls,
            [
                (path, 40 + index, "quick")
                for index, path in enumerate(expected_dirs)
            ],
        )
        self.assertTrue(all(path.is_dir() for path in expected_dirs))
        self.assertEqual(state, self.load_cycle())
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["completed_rounds"], 3)
        self.assertEqual([item["index"] for item in state["rounds"]], [0, 1, 2])
        self.assertEqual([item["seed"] for item in state["rounds"]], [40, 41, 42])
        self.assertEqual([item["elapsed_ms"] for item in state["rounds"]], [100.0, 200.0, 300.0])
        self.assertEqual(
            [item["output_path"] for item in state["rounds"]],
            ["rounds/0001-40", "rounds/0002-41", "rounds/0003-42"],
        )

    def test_completed_cycle_is_idempotent_without_factory_or_registry_calls(self) -> None:
        self.execute_cycle()
        factory = RecordingFactory()
        registry = FakeRegistry()

        state = self.execute_cycle(factory=factory, registry=registry, clock=StepClock())

        self.assertEqual(state["status"], "completed")
        self.assertEqual(factory.calls, [])
        self.assertEqual(registry.calls, [])

    def test_resume_runs_only_failed_round_and_keeps_later_completed_round(self) -> None:
        first = RecordingFactory(errors={41: OSError("round failed")})
        failed = self.execute_cycle(
            factory=first,
            continue_on_error=True,
            clock=StepClock(1, 1.1, 2, 2.2, 3, 3.3),
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(
            [item["status"] for item in failed["rounds"]],
            ["completed", "failed", "completed"],
        )

        second = RecordingFactory()
        recovered = self.execute_cycle(factory=second, clock=StepClock(4, 4.4))

        self.assertEqual(second.calls, [(self.cycle_dir() / "rounds" / "0002-41", 41, "quick")])
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["completed_rounds"], 3)

    def test_resume_clears_stale_running_round_before_earlier_failed_round(self) -> None:
        first = RecordingFactory(errors={40: OSError("round failed")})
        failed = self.execute_cycle(
            cycle_id="stale-running",
            factory=first,
            continue_on_error=True,
            clock=StepClock(1, 1.1, 2, 2.2, 3, 3.3),
        )
        path = self.cycle_dir("stale-running") / "cycle.json"
        stale_running = {
            **failed["rounds"][2],
            "status": "running",
            "total": None,
            "passed": None,
            "failures": None,
            "elapsed_ms": None,
            "issue_ids": [],
            "finished_at": None,
            "error_type": None,
            "error": None,
        }
        crashed = {
            **failed,
            "status": "running",
            "completed_rounds": 1,
            "rounds": [failed["rounds"][0], failed["rounds"][1], stale_running],
        }
        path.write_text(json.dumps(crashed), encoding="utf-8")
        factory = RecordingFactory()

        recovered = self.execute_cycle(
            cycle_id="stale-running",
            factory=factory,
            clock=StepClock(4, 4.1, 5, 5.1),
        )

        self.assertEqual([call[1] for call in factory.calls], [40, 42])
        self.assertEqual(recovered["status"], "completed")

    def test_rejects_resume_parameter_mismatch(self) -> None:
        self.execute_cycle()
        for key, value in (("mode", "daily"), ("rounds", 4), ("base_seed", 41)):
            arguments = {"mode": "quick", "rounds": 3, "base_seed": 40}
            arguments[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                self.execute_cycle(**arguments)

    def test_stop_on_operational_error_records_failed_round_and_stops(self) -> None:
        factory = RecordingFactory(errors={41: RuntimeError("runner exploded")})
        state = self.execute_cycle(factory=factory, clock=StepClock(1, 1.1, 2, 2.25))

        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["completed_rounds"], 1)
        self.assertEqual([call[1] for call in factory.calls], [40, 41])
        self.assertEqual([item["status"] for item in state["rounds"]], ["completed", "failed"])
        self.assertEqual(state["rounds"][1]["error_type"], "RuntimeError")
        self.assertEqual(state["rounds"][1]["error"], "runner exploded")

    def test_continue_on_error_runs_later_rounds_and_finishes_failed(self) -> None:
        factory = RecordingFactory(errors={41: ValueError("bad operation")})
        state = self.execute_cycle(
            factory=factory,
            continue_on_error=True,
            clock=StepClock(1, 1.1, 2, 2.2, 3, 3.3),
        )

        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["completed_rounds"], 2)
        self.assertEqual([call[1] for call in factory.calls], [40, 41, 42])
        self.assertEqual(
            [item["status"] for item in state["rounds"]],
            ["completed", "failed", "completed"],
        )

    def test_ingests_issues_and_blocking_report_is_not_operational_error(self) -> None:
        blocking_report = EvaluationReport(4, 3, (), {}, {}, {})
        factory = RecordingFactory(reports={40: blocking_report})
        registry = FakeRegistry({40: ("issue-z", "issue-a"), 41: ("issue-a",)})

        state = self.execute_cycle(factory=factory, registry=registry)

        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["issue_ids"], ["issue-z", "issue-a"])
        self.assertEqual(state["rounds"][0]["failures"], 1)
        self.assertEqual(state["rounds"][0]["issue_ids"], ["issue-z", "issue-a"])
        self.assertEqual(len(registry.calls), 3)
        self.assertEqual(registry.calls[0][2], "2026-07-16T01:02:03Z")
        self.assertEqual(
            [call[3] for call in registry.calls],
            [
                "daily.2026-07-16:round:0",
                "daily.2026-07-16:round:1",
                "daily.2026-07-16:round:2",
            ],
        )

    def test_real_registry_retry_after_cycle_write_failure_is_idempotent(self) -> None:
        report, context = self.blocking_evaluation()
        factory = RecordingFactory(reports={40: report}, contexts={40: context})
        registry = IssueRegistry(self.root)
        real_atomic_write = autonomous_cycle._atomic_write_json
        failed = False

        def fail_first_completed_state(path: Path, value: object) -> None:
            nonlocal failed
            rounds = value.get("rounds", []) if isinstance(value, dict) else []
            if (
                path.name == "cycle.json"
                and any(item.get("status") == "completed" for item in rounds)
                and not failed
            ):
                failed = True
                raise OSError("simulated completed state write failure")
            real_atomic_write(path, value)

        with mock.patch.object(
            autonomous_cycle,
            "_atomic_write_json",
            side_effect=fail_first_completed_state,
        ):
            with self.assertRaisesRegex(OSError, "completed state write failure"):
                self.execute_cycle(
                    cycle_id="idempotent-retry",
                    rounds=1,
                    factory=factory,
                    registry=registry,
                    clock=StepClock(1, 1.1),
                )

        state = self.execute_cycle(
            cycle_id="idempotent-retry",
            rounds=1,
            factory=factory,
            registry=registry,
            clock=StepClock(2, 2.1),
        )

        self.assertEqual(state["status"], "completed")
        issue_id = state["issue_ids"][0]
        self.assertEqual(registry.load(issue_id)["occurrences"], 1)
        self.assertEqual(len(factory.calls), 2)

    def test_invalid_finished_at_becomes_operational_failure_with_valid_timestamp(self) -> None:
        valid = "2026-07-16T01:02:03Z"
        utc_now = SequenceUtcNow(valid, valid, valid, valid, "invalid", valid, valid)

        state = run_cycle(
            self.root,
            "invalid-finished-at",
            "quick",
            1,
            10,
            runner_factory=RecordingFactory(),
            registry=FakeRegistry(),
            clock=StepClock(1, 1.1, 1.2),
            utc_now=utc_now,
            commit_sha="abc123",
            repository_root=self.repository_root,
        )

        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["rounds"][0]["status"], "failed")
        self.assertEqual(state["rounds"][0]["error_type"], "ValueError")
        persisted = json.loads(
            (self.cycle_dir("invalid-finished-at") / "cycle.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(persisted["rounds"][0]["finished_at"], valid)
        self.assertEqual(persisted["updated_at"], valid)

    def test_persistently_invalid_finished_at_leaves_recoverable_running_state(self) -> None:
        valid = "2026-07-16T01:02:03Z"
        calls = 0

        def utc_now() -> str:
            nonlocal calls
            calls += 1
            return valid if calls <= 4 else "invalid"

        with self.assertRaises(ValueError):
            run_cycle(
                self.root,
                "persistent-invalid-time",
                "quick",
                1,
                10,
                runner_factory=RecordingFactory(),
                registry=FakeRegistry(),
                clock=StepClock(1, 1.1, 1.2),
                utc_now=utc_now,
                commit_sha="abc123",
                repository_root=self.repository_root,
            )

        persisted = self.load_cycle("persistent-invalid-time")
        self.assertEqual(persisted["status"], "running")
        self.assertIsNone(persisted["rounds"][0]["finished_at"])
        recovered = self.execute_cycle(
            cycle_id="persistent-invalid-time",
            rounds=1,
            base_seed=10,
            clock=StepClock(2, 2.1),
        )
        self.assertEqual(recovered["status"], "completed")

    def test_non_finite_and_reversing_clocks_are_operational_failures(self) -> None:
        cases = {
            "nan": StepClock(1.0, float("nan"), 1.1),
            "infinite": StepClock(1.0, float("inf"), 1.1),
            "reversing": StepClock(2.0, 1.0, 2.1),
            "overflow": StepClock(0.0, 1e308, 0.1),
        }

        for name, clock in cases.items():
            with self.subTest(name=name):
                state = self.execute_cycle(
                    cycle_id=f"clock-{name}",
                    rounds=1,
                    clock=clock,
                )
                self.assertEqual(state["status"], "stopped")
                self.assertEqual(state["rounds"][0]["status"], "failed")
                self.assertEqual(state["rounds"][0]["error_type"], "ValueError")

    def test_first_clock_exception_is_recorded_with_zero_elapsed(self) -> None:
        state = run_cycle(
            self.root,
            "clock-exception",
            "quick",
            1,
            10,
            runner_factory=RecordingFactory(),
            registry=FakeRegistry(),
            clock=RaisingClock(),
            utc_now=self.utc_now,
            commit_sha="abc123",
            repository_root=self.repository_root,
        )

        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["rounds"][0]["error_type"], "RuntimeError")
        self.assertEqual(state["rounds"][0]["elapsed_ms"], 0.0)

    def test_writes_consistent_json_and_markdown_summaries(self) -> None:
        registry = FakeRegistry({40: ("issue-1",)})
        state = self.execute_cycle(registry=registry, clock=StepClock(1, 1.1, 2, 2.2, 3, 3.3))
        summary = json.loads((self.cycle_dir() / "summary.json").read_text(encoding="utf-8"))
        markdown = (self.cycle_dir() / "summary.md").read_text(encoding="utf-8")

        self.assertEqual(summary["cycle_id"], state["cycle_id"])
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["target_rounds"], 3)
        self.assertEqual(summary["completed_rounds"], 3)
        self.assertEqual(summary["total_scenarios"], 12)
        self.assertEqual(summary["passed"], 12)
        self.assertEqual(summary["failures"], 0)
        self.assertEqual(summary["issue_ids"], ["issue-1"])
        self.assertEqual(summary["elapsed_ms"], 600.0)
        self.assertEqual(summary["average_elapsed_ms"], 200.0)
        self.assertIn(
            "| Round | Seed | Status | Total | Passed | Failures | Elapsed ms | Issues |",
            markdown,
        )
        self.assertIn("| 1 | 40 | completed | 4 | 4 | 0 | 100.0 | issue-1 |", markdown)

    def test_summary_elapsed_overflow_never_persists_completed_and_can_resume(self) -> None:
        factory = RecordingFactory()

        stopped = self.execute_cycle(
            cycle_id="summary-overflow",
            rounds=2,
            factory=factory,
            clock=StepClock(0.0, 9e304, 0.0, 9e304),
        )

        self.assertEqual(stopped["status"], "stopped")
        self.assertNotEqual(self.load_cycle("summary-overflow")["status"], "completed")
        summary_path = self.cycle_dir("summary-overflow") / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "stopped")
        self.assertTrue(math.isfinite(summary["elapsed_ms"]))
        self.assertTrue(math.isfinite(summary["average_elapsed_ms"]))
        self.assertTrue(
            any(
                record["error_type"] == "OverflowError"
                for record in stopped["rounds"]
            )
        )

        recovered = self.execute_cycle(
            cycle_id="summary-overflow",
            rounds=2,
            factory=factory,
            clock=StepClock(1.0, 1.1),
        )

        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["completed_rounds"], 2)
        self.assertEqual(len(factory.calls), 3)

    def test_stopped_cycle_summary_matches_partial_state(self) -> None:
        state = self.execute_cycle(
            factory=RecordingFactory(errors={41: OSError("disk")}),
            clock=StepClock(1, 1.1, 2, 2.2),
        )
        summary = json.loads((self.cycle_dir() / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["status"], state["status"])
        self.assertEqual(summary["completed_rounds"], 1)
        self.assertEqual(summary["total_scenarios"], 4)
        self.assertEqual(len(summary["rounds"]), 2)

    def test_strict_parameter_validation_and_reserved_cycle_ids(self) -> None:
        invalid_ids = (
            "",
            ".",
            "..",
            "sample.",
            "...",
            "trailing..",
            "a" * 81,
            "../escape",
            "a/b",
            "a\\b",
            "CON",
            "con.log",
            "LPT9.txt",
            "space id",
        )
        for cycle_id in invalid_ids:
            with self.subTest(cycle_id=cycle_id), self.assertRaises(ValueError):
                self.execute_cycle(cycle_id=cycle_id)
        for mode in ("unknown", "QUICK", ""):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.execute_cycle(mode=mode)
        for rounds in (0, -1, True, 1.5):
            with self.subTest(rounds=rounds), self.assertRaises(ValueError):
                self.execute_cycle(rounds=rounds)  # type: ignore[arg-type]
        for seed in (True, 1.5, "40"):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                self.execute_cycle(base_seed=seed)  # type: ignore[arg-type]

    def test_cycle_id_rejects_trailing_periods(self) -> None:
        for cycle_id in ("sample.", "...", "trailing.."):
            with self.subTest(cycle_id=cycle_id), self.assertRaises(ValueError):
                autonomous_cycle._validate_cycle_id(cycle_id)

    def test_rejects_parent_traversal_and_symlinked_cycle_layout(self) -> None:
        with self.assertRaises(ValueError):
            run_cycle(
                self.root / ".." / "escape",
                "safe",
                "quick",
                1,
                1,
                runner_factory=RecordingFactory(),
                registry=FakeRegistry(),
                repository_root=self.repository_root,
            )

        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        cycles = self.root / "cycles"
        cycles.parent.mkdir(parents=True)
        if not self.make_directory_link(cycles, outside):
            self.skipTest("directory links are unavailable")
        with self.assertRaisesRegex(ValueError, "link|reparse"):
            self.execute_cycle(cycle_id="safe")
        self.assertEqual(list(outside.iterdir()), [])

    def test_linked_rounds_directory_is_recorded_as_failure_without_escape(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside-rounds"
        outside.mkdir()
        rounds_dir = self.cycle_dir() / "rounds"
        rounds_dir.parent.mkdir(parents=True)
        if not self.make_directory_link(rounds_dir, outside):
            self.skipTest("directory links are unavailable")

        state = self.execute_cycle(rounds=1, clock=StepClock(1, 1.1))

        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["rounds"][0]["status"], "failed")
        self.assertEqual(list(outside.iterdir()), [])

    def test_repository_boundary_accepts_injected_artifacts_evaluation_root(self) -> None:
        state = self.execute_cycle(cycle_id="inside-repository", rounds=1)

        self.assertEqual(state["status"], "completed")
        self.assertTrue(
            (self.root / "cycles" / "inside-repository" / "cycle.json").is_file()
        )

    def test_default_repository_boundary_rejects_external_absolute_root(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside-default"

        with self.assertRaises(ValueError):
            run_cycle(
                outside,
                "outside-default",
                "quick",
                1,
                1,
                runner_factory=RecordingFactory(),
                registry=FakeRegistry(),
                clock=StepClock(1, 1.1),
                utc_now=self.utc_now,
                commit_sha="abc123",
            )

    def test_repository_boundary_rejects_external_and_unapproved_roots(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside"
        invalid_roots = (
            outside,
            Path("."),
            Path("other"),
            self.repository_root,
        )

        for evaluation_root in invalid_roots:
            with self.subTest(evaluation_root=evaluation_root):
                with self.assertRaises(ValueError):
                    run_cycle(
                        evaluation_root,
                        "boundary",
                        "quick",
                        1,
                        1,
                        runner_factory=RecordingFactory(),
                        registry=FakeRegistry(),
                        repository_root=self.repository_root,
                    )

        self.assertFalse(outside.exists())

    def test_repository_boundary_rejects_linked_evaluation_root(self) -> None:
        outside = Path(self.temporary_directory.name) / "outside-evaluation"
        outside.mkdir()
        self.root.parent.mkdir(parents=True)
        if not self.make_directory_link(self.root, outside):
            self.skipTest("directory links are unavailable")

        with self.assertRaisesRegex(ValueError, "link|reparse"):
            self.execute_cycle(cycle_id="linked-root", rounds=1)

        self.assertEqual(list(outside.iterdir()), [])

    def test_rejects_corrupt_duplicate_and_inconsistent_state(self) -> None:
        self.execute_cycle()
        path = self.cycle_dir() / "cycle.json"
        valid = self.load_cycle()
        mutations = (
            '{"schema_version":1,"schema_version":1}',
            "{not-json",
            json.dumps({**valid, "completed_rounds": 2}),
            json.dumps({**valid, "base_seed": True}),
            json.dumps({**valid, "target_rounds": True}),
            json.dumps({**valid, "status": "failed"}),
            json.dumps({**valid, "status": "stopped"}),
            json.dumps({**valid, "issue_ids": ["unexpected"]}),
            json.dumps({**valid, "rounds": [*valid["rounds"], valid["rounds"][0]]}),
            json.dumps(
                {
                    **valid,
                    "rounds": [
                        {**valid["rounds"][0], "index": 4},
                        *valid["rounds"][1:],
                    ],
                }
            ),
            json.dumps(
                {
                    **valid,
                    "rounds": [
                        {**valid["rounds"][0], "seed": 999},
                        *valid["rounds"][1:],
                    ],
                }
            ),
            json.dumps(
                {
                    **valid,
                    "rounds": [
                        {**valid["rounds"][0], "output_path": "../escape"},
                        *valid["rounds"][1:],
                    ],
                }
            ),
            json.dumps(
                {
                    **valid,
                    "rounds": [
                        {**valid["rounds"][0], "elapsed_ms": float("nan")},
                        *valid["rounds"][1:],
                    ],
                }
            ),
        )
        for content in mutations:
            with self.subTest(content=content[:80]):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.execute_cycle(factory=RecordingFactory(), registry=FakeRegistry())
        path.write_text(json.dumps(valid), encoding="utf-8")

    def test_rejects_boolean_integer_fields_even_when_values_compare_equal(self) -> None:
        self.execute_cycle(cycle_id="bool-state", rounds=1, base_seed=1)
        path = self.cycle_dir("bool-state") / "cycle.json"
        valid = json.loads(path.read_text(encoding="utf-8"))

        for field in ("base_seed", "target_rounds"):
            with self.subTest(field=field):
                path.write_text(json.dumps({**valid, field: True}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.execute_cycle(cycle_id="bool-state", rounds=1, base_seed=1)

    def test_rejects_non_prefix_round_indices_and_multiple_running_rounds(self) -> None:
        self.execute_cycle(cycle_id="round-shape")
        path = self.cycle_dir("round-shape") / "cycle.json"
        valid = json.loads(path.read_text(encoding="utf-8"))
        missing_prefix = {
            **valid,
            "status": "running",
            "completed_rounds": 2,
            "rounds": valid["rounds"][1:],
        }

        def running(record: dict[str, object]) -> dict[str, object]:
            return {
                **record,
                "status": "running",
                "total": None,
                "passed": None,
                "failures": None,
                "elapsed_ms": None,
                "issue_ids": [],
                "finished_at": None,
                "error_type": None,
                "error": None,
            }

        multiple_running = {
            **valid,
            "status": "running",
            "completed_rounds": 1,
            "rounds": [
                running(valid["rounds"][0]),
                running(valid["rounds"][1]),
                valid["rounds"][2],
            ],
        }

        for state in (missing_prefix, multiple_running):
            with self.subTest(rounds=state["rounds"]):
                path.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.execute_cycle(cycle_id="round-shape")

    def test_registry_initialization_error_is_recorded_as_failed_round(self) -> None:
        with mock.patch.object(
            autonomous_cycle,
            "IssueRegistry",
            side_effect=OSError("registry unavailable"),
        ):
            state = run_cycle(
                self.root,
                "registry-error",
                "quick",
                1,
                5,
                runner_factory=RecordingFactory(),
                clock=StepClock(1, 1.1),
                utc_now=self.utc_now,
                commit_sha="abc123",
                repository_root=self.repository_root,
            )

        self.assertEqual(state["status"], "stopped")
        self.assertEqual(state["rounds"][0]["status"], "failed")
        self.assertEqual(state["rounds"][0]["error_type"], "OSError")

    def test_cycle_lock_blocks_second_holder_and_persists(self) -> None:
        cycle_dir = self.cycle_dir("locked")
        cycle_dir.mkdir(parents=True)
        with autonomous_cycle._cycle_lock(cycle_dir):
            with mock.patch.object(autonomous_cycle.time, "sleep", return_value=None):
                with self.assertRaises(TimeoutError):
                    with autonomous_cycle._cycle_lock(cycle_dir):
                        self.fail("second lock unexpectedly acquired")

        lock_path = cycle_dir / ".cycle.lock"
        self.assertTrue(lock_path.is_file())
        with autonomous_cycle._cycle_lock(cycle_dir):
            self.assertTrue(lock_path.is_file())

    def test_atomic_replace_failure_preserves_old_cycle_file(self) -> None:
        self.cycle_dir().mkdir(parents=True)
        path = self.cycle_dir() / "cycle.json"
        old = '{"old":true}\n'
        path.write_text(old, encoding="utf-8")

        with mock.patch.object(
            autonomous_cycle.os,
            "replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaises(OSError):
                autonomous_cycle._atomic_write_json(path, {"new": True})

        self.assertEqual(path.read_text(encoding="utf-8"), old)
        self.assertEqual(list(self.cycle_dir().glob("*.tmp")), [])

    def test_atomic_write_rejects_symlink_destination(self) -> None:
        self.cycle_dir().mkdir(parents=True)
        outside = Path(self.temporary_directory.name) / "outside-state.json"
        outside.write_text('{"outside":true}\n', encoding="utf-8")
        destination = self.cycle_dir() / "cycle.json"
        try:
            destination.symlink_to(outside)
        except OSError:
            self.skipTest("file links are unavailable")

        with self.assertRaisesRegex(ValueError, "link|reparse"):
            autonomous_cycle._atomic_write_json(destination, {"new": True})

        self.assertTrue(destination.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), '{"outside":true}\n')


class AutonomousCycleCliTests(unittest.TestCase):
    @staticmethod
    def completed_state(*, issues: bool = False) -> dict[str, object]:
        return {
            "cycle_id": "quick-7-2",
            "status": "completed",
            "completed_rounds": 2,
            "target_rounds": 2,
            "issue_ids": ["issue-1"] if issues else [],
        }

    def test_cli_defaults_cycle_id_and_returns_zero(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(*args: object, **kwargs: object) -> dict[str, object]:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return self.completed_state()

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            repository_root = Path(directory) / "repo"
            repository_root.mkdir()
            with (
                mock.patch.object(cli_module, "_REPO_ROOT", repository_root),
                redirect_stdout(output),
            ):
                code = main(
                    ["--rounds", "2", "--seed", "7"], cycle_runner=fake_run
                )

        self.assertEqual(code, 0)
        self.assertEqual(
            captured["args"],
            (
                repository_root / "artifacts" / "evaluation",
                "quick-7-2",
                "quick",
                2,
                7,
            ),
        )
        self.assertFalse(captured["kwargs"]["continue_on_error"])
        self.assertIn("status=completed", output.getvalue())

    def test_cli_returns_one_for_completed_cycle_with_issues(self) -> None:
        with redirect_stdout(io.StringIO()):
            code = main([], cycle_runner=lambda *args, **kwargs: self.completed_state(issues=True))
        self.assertEqual(code, 1)

    def test_cli_returns_two_for_failed_state_or_operational_error(self) -> None:
        failed = {**self.completed_state(), "status": "failed"}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main([], cycle_runner=lambda *args, **kwargs: failed), 2)

        error = io.StringIO()
        with redirect_stderr(error):
            code = main([], cycle_runner=mock.Mock(side_effect=ValueError("bad state")))
        self.assertEqual(code, 2)
        self.assertIn("cycle error: bad state", error.getvalue())

        runtime_error = io.StringIO()
        with redirect_stderr(runtime_error):
            code = main(
                [],
                cycle_runner=mock.Mock(side_effect=RuntimeError("clock exploded")),
            )
        self.assertEqual(code, 2)
        self.assertIn("cycle error: clock exploded", runtime_error.getvalue())

    def test_cli_invalid_rounds_uses_argparse_system_exit(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--rounds", "0"])
        self.assertEqual(raised.exception.code, 2)

    def test_cli_does_not_expose_root_override(self) -> None:
        cycle_runner = mock.Mock(return_value=self.completed_state())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--root", "C:/outside"], cycle_runner=cycle_runner)

        self.assertEqual(raised.exception.code, 2)
        cycle_runner.assert_not_called()


class ManageEvaluationIssueCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository_root = Path(self.temporary_directory.name) / "repo"
        self.repository_root.mkdir()
        self.evaluation_root = self.repository_root / "artifacts" / "evaluation"

    def create_issue(self) -> tuple[IssueRegistry, str]:
        scenario = Scenario(
            "scenario-cli",
            HealthPersona("persona-cli", "single_condition"),
            ("Plan dinner without peanuts.",),
            MenuExpectation(forbidden_terms=("peanut",)),
            17,
            "hard_constraint",
        )
        failure = FailureRecord(
            scenario.scenario_id,
            scenario.seed,
            "abc123",
            scenario.messages,
            ("No peanuts.",),
            (
                Violation(
                    "constraint.forbidden_term",
                    "blocking",
                    "Blocked output.",
                    {"dish_ids": [7]},
                ),
            ),
            1.0,
        )
        registry = IssueRegistry(self.evaluation_root)
        issue_ids = registry.ingest(
            EvaluationReport(1, 0, (failure,), {}, {}, {}),
            {
                scenario.scenario_id: {
                    "holdout": False,
                    "health_bucket": scenario.persona.primary_bucket,
                    "intent": scenario.intent,
                    "expectation": scenario.expectation.to_dict(),
                    "scenario": scenario.to_dict(),
                }
            },
            observed_at="2026-07-16T01:02:03Z",
        )
        return registry, issue_ids[0]

    def create_cycle(self, cycle_id: str, mode: str) -> Path:
        run_cycle(
            self.evaluation_root,
            cycle_id,
            mode,
            1,
            40,
            runner_factory=RecordingFactory(),
            registry=FakeRegistry(),
            clock=StepClock(1.0, 1.125),
            utc_now=lambda: "2026-07-16T01:02:03Z",
            commit_sha="abc123",
            repository_root=self.repository_root,
        )
        return self.evaluation_root / "cycles" / cycle_id / "cycle.json"

    def test_cli_transitions_issue_with_strict_daily_cycle(self) -> None:
        registry, issue_id = self.create_issue()
        created_roots: list[Path] = []

        def registry_factory(root: Path) -> IssueRegistry:
            created_roots.append(Path(root))
            return IssueRegistry(root)

        verifying_output = io.StringIO()
        with redirect_stdout(verifying_output):
            verifying_code = manage_issue_cli.main(
                [issue_id, "--status", "verifying"],
                repository_root=self.repository_root,
                registry_factory=registry_factory,
            )
        self.create_cycle("daily-verification", "daily")
        resolved_output = io.StringIO()
        with redirect_stdout(resolved_output):
            resolved_code = manage_issue_cli.main(
                [
                    issue_id,
                    "--status",
                    "resolved",
                    "--cycle-id",
                    "daily-verification",
                ],
                repository_root=self.repository_root,
                registry_factory=registry_factory,
            )

        self.assertEqual(verifying_code, 0)
        self.assertEqual(resolved_code, 0)
        self.assertEqual(registry.load(issue_id)["status"], "resolved")
        self.assertEqual(created_roots, [self.evaluation_root, self.evaluation_root])
        self.assertIn(
            str(self.evaluation_root / "issues" / "verifying" / f"{issue_id}.json"),
            verifying_output.getvalue(),
        )
        self.assertIn(
            str(self.evaluation_root / "issues" / "resolved" / f"{issue_id}.json"),
            resolved_output.getvalue(),
        )

    def test_cli_exports_regression_candidate(self) -> None:
        _, issue_id = self.create_issue()
        output = io.StringIO()

        with redirect_stdout(output):
            code = manage_issue_cli.main(
                [issue_id, "--export-regression"],
                repository_root=self.repository_root,
                registry_factory=IssueRegistry,
            )

        candidate = (
            self.evaluation_root
            / "candidates"
            / "regressions"
            / f"{issue_id}.json"
        )
        self.assertEqual(code, 0)
        self.assertTrue(candidate.is_file())
        self.assertIn(str(candidate), output.getvalue())

    def test_cli_resolution_requires_existing_cycle_and_rejects_quick_mode(self) -> None:
        registry, issue_id = self.create_issue()
        registry.set_status(issue_id, "verifying")

        for arguments, expected_message in (
            ([issue_id, "--status", "resolved"], "cycle"),
            (
                [
                    issue_id,
                    "--status",
                    "resolved",
                    "--cycle-id",
                    "missing-cycle",
                ],
                "missing-cycle",
            ),
        ):
            error = io.StringIO()
            with redirect_stderr(error):
                code = manage_issue_cli.main(
                    arguments,
                    repository_root=self.repository_root,
                    registry_factory=IssueRegistry,
                )
            self.assertEqual(code, 2)
            self.assertIn(expected_message, error.getvalue())
            self.assertEqual(registry.load(issue_id)["status"], "verifying")

        self.create_cycle("quick-verification", "quick")
        error = io.StringIO()
        with redirect_stderr(error):
            code = manage_issue_cli.main(
                [
                    issue_id,
                    "--status",
                    "resolved",
                    "--cycle-id",
                    "quick-verification",
                ],
                repository_root=self.repository_root,
                registry_factory=IssueRegistry,
            )
        self.assertEqual(code, 2)
        self.assertIn("daily or deep", error.getvalue())
        self.assertEqual(registry.load(issue_id)["status"], "verifying")

    def test_cli_rejects_invalid_cycle_schema_and_unsafe_cycle_id(self) -> None:
        registry, issue_id = self.create_issue()
        registry.set_status(issue_id, "verifying")
        cycle_path = self.create_cycle("invalid-state", "daily")
        state = json.loads(cycle_path.read_text(encoding="utf-8"))
        state["unexpected"] = True
        cycle_path.write_text(json.dumps(state), encoding="utf-8")

        for cycle_id in ("invalid-state", "../outside"):
            error = io.StringIO()
            with redirect_stderr(error):
                code = manage_issue_cli.main(
                    [
                        issue_id,
                        "--status",
                        "resolved",
                        "--cycle-id",
                        cycle_id,
                    ],
                    repository_root=self.repository_root,
                    registry_factory=IssueRegistry,
                )
            self.assertEqual(code, 2)
            self.assertTrue(error.getvalue())
            self.assertEqual(registry.load(issue_id)["status"], "verifying")

    def test_cli_returns_two_for_unknown_issue(self) -> None:
        IssueRegistry(self.evaluation_root)
        error = io.StringIO()
        issue_id = "issue-0123456789abcdef01234567"

        with redirect_stderr(error):
            code = manage_issue_cli.main(
                [issue_id, "--status", "verifying"],
                repository_root=self.repository_root,
                registry_factory=IssueRegistry,
            )

        self.assertEqual(code, 2)
        self.assertIn(issue_id, error.getvalue())

    def test_cli_requires_exactly_one_action_via_argparse(self) -> None:
        issue_id = "issue-0123456789abcdef01234567"
        for arguments in (
            [issue_id],
            [issue_id, "--status", "open", "--export-regression"],
        ):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                manage_issue_cli.main(arguments, repository_root=self.repository_root)
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
