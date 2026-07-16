from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts.run_autonomous_cycle import main
from tests.evaluation import autonomous_cycle
from tests.evaluation.autonomous_cycle import run_cycle
from tests.evaluation.schemas import EvaluationReport


class StepClock:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


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
    ) -> None:
        self.reports = reports or {}
        self.errors = errors or {}
        self.calls: list[tuple[Path, int, str]] = []

    def __call__(self, output_dir: Path, *, seed: int, mode: str) -> FakeRunner:
        self.calls.append((Path(output_dir), seed, mode))
        if seed in self.errors:
            raise self.errors[seed]
        report = self.reports.get(seed, EvaluationReport(4, 4, (), {}, {}, {}))
        return FakeRunner(report, {f"scenario-{seed}": {"seed": seed}})


class FakeRegistry:
    def __init__(self, issue_ids: dict[int, tuple[str, ...]] | None = None) -> None:
        self.issue_ids = issue_ids or {}
        self.calls: list[tuple[EvaluationReport, dict[str, object], str]] = []

    def ingest(
        self,
        report: EvaluationReport,
        scenario_context: dict[str, object],
        *,
        observed_at: str,
    ) -> tuple[str, ...]:
        self.calls.append((report, scenario_context, observed_at))
        seed = int(next(iter(scenario_context.values()))["seed"])
        return self.issue_ids.get(seed, ())


class AutonomousCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name) / "evaluation"

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
        )

    def cycle_dir(self, cycle_id: str = "daily.2026-07-16") -> Path:
        return self.root / "cycles" / cycle_id

    def load_cycle(self, cycle_id: str = "daily.2026-07-16") -> dict[str, object]:
        return json.loads(
            (self.cycle_dir(cycle_id) / "cycle.json").read_text(encoding="utf-8")
        )

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
        with redirect_stdout(output):
            code = main(["--rounds", "2", "--seed", "7"], cycle_runner=fake_run)

        self.assertEqual(code, 0)
        self.assertEqual(
            captured["args"],
            (Path("artifacts/evaluation"), "quick-7-2", "quick", 2, 7),
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

    def test_cli_invalid_rounds_uses_argparse_system_exit(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--rounds", "0"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
