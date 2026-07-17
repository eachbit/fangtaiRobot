from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import import_evaluation_failures as cli
from tests.evaluation.issue_registry import IssueRegistry
from tests.evaluation.report import write_report
from tests.evaluation.schemas import (
    EvaluationReport,
    FailureRecord,
    HealthPersona,
    MenuExpectation,
    Scenario,
    Violation,
)


class ImportEvaluationFailuresCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.repository_root = Path(self.temporary_directory.name) / "repo"
        self.evaluation_root = self.repository_root / "artifacts" / "evaluation"
        self.evaluation_root.mkdir(parents=True)

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

    def write_failures(
        self,
        output: Path,
        *scenario_ids: str,
    ) -> list[Path]:
        failures = []
        contexts = {}
        for index, scenario_id in enumerate(scenario_ids):
            scenario = Scenario(
                scenario_id,
                HealthPersona(f"persona-{index}", "healthy"),
                (f"Plan dinner {index}.",),
                MenuExpectation(dish_count=2 + index),
                10 + index,
                "hard_constraint",
            )
            failures.append(
                FailureRecord(
                    scenario.scenario_id,
                    scenario.seed,
                    "abc123",
                    scenario.messages,
                    (f"Dinner {index}.",),
                    (
                        Violation(
                            f"blocking.{scenario_id}",
                            "blocking",
                            "failed",
                            None,
                        ),
                    ),
                    1.0,
                )
            )
            contexts[scenario.scenario_id] = {
                "holdout": False,
                "health_bucket": scenario.persona.primary_bucket,
                "intent": scenario.intent,
                "expectation": scenario.expectation.to_dict(),
                "scenario": scenario.to_dict(),
            }
        write_report(
            EvaluationReport(len(failures), 0, tuple(failures), {}, {}, {}),
            output,
            scenario_context=contexts,
        )
        return sorted((output / "failures").glob("*.json"))

    def invoke(
        self,
        arguments: list[str],
        *,
        registry: IssueRegistry | None = None,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(
                arguments,
                repository_root=self.repository_root,
                registry=registry,
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_file_import_is_idempotent_and_prints_touched_ids(self) -> None:
        path = self.write_failures(self.evaluation_root / "run-one", "scenario-a")[0]
        registry = IssueRegistry(self.evaluation_root)

        first_code, first_output, first_error = self.invoke(
            [str(path)],
            registry=registry,
        )
        second_code, second_output, second_error = self.invoke(
            [str(path)],
            registry=registry,
        )

        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual(first_error + second_error, "")
        self.assertEqual(first_output, second_output)
        issue_id = first_output.strip()
        self.assertRegex(issue_id, r"^issue-[0-9a-f]{24}$")
        self.assertEqual(registry.load(issue_id)["occurrences"], 1)

    def test_directory_imports_direct_json_and_recursive_is_explicit(self) -> None:
        direct = self.evaluation_root / "manual" / "failures"
        direct.mkdir(parents=True)
        generated = self.write_failures(
            self.evaluation_root / "generated",
            "scenario-a",
            "scenario-b",
        )
        for path in generated:
            path.replace(direct / path.name)
        nested = direct / "nested"
        nested_path = self.write_failures(nested / "run", "scenario-c")[0]
        registry = IssueRegistry(self.evaluation_root)

        direct_code, direct_output, _ = self.invoke([str(direct)], registry=registry)
        recursive_code, recursive_output, _ = self.invoke(
            ["--recursive", str(direct)],
            registry=registry,
        )

        self.assertEqual((direct_code, recursive_code), (0, 0))
        self.assertEqual(len(direct_output.splitlines()), 2)
        self.assertEqual(len(recursive_output.splitlines()), 2)
        self.assertTrue(nested_path.is_file())

    def test_recursive_parent_imports_only_exact_round_failure_structure(self) -> None:
        parent = self.evaluation_root / "batch"
        first_cycle = parent / "cycles" / "daily-one"
        second_cycle = parent / "archive" / "daily-two"
        self.write_failures(
            first_cycle / "rounds" / "0001-40",
            "valid-first",
        )
        self.write_failures(
            second_cycle / "rounds" / "0002-41",
            "valid-second",
        )
        self.write_failures(parent / "unrelated", "invalid-unrelated")
        self.write_failures(
            first_cycle / "rounds" / "0001-40" / "nested",
            "invalid-round-nested",
        )
        self.write_failures(
            first_cycle / "rounds" / "0001-40" / "failures" / "child",
            "invalid-failure-child",
        )
        registry = IssueRegistry(self.evaluation_root)

        code, output, error = self.invoke(
            ["--recursive", str(parent)],
            registry=registry,
        )
        issue_ids = output.splitlines()

        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertEqual(len(issue_ids), 2)
        self.assertEqual(issue_ids, sorted(issue_ids))
        self.assertEqual(
            len(list(registry.status_directories["open"].glob("*.json"))),
            2,
        )

    def test_recursive_cycle_imports_only_round_failure_json(self) -> None:
        cycle = self.evaluation_root / "cycles" / "daily-one"
        cycle.mkdir(parents=True)
        (cycle / "cycle.json").write_text('{"status":"completed"}', encoding="utf-8")
        (cycle / "summary.json").write_text('{"completed_rounds":1}', encoding="utf-8")
        failure_path = self.write_failures(
            cycle / "rounds" / "0001-40",
            "scenario-cycle",
        )[0]
        registry = IssueRegistry(self.evaluation_root)

        code, output, error = self.invoke(
            ["--recursive", str(cycle)],
            registry=registry,
        )

        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertEqual(len(output.splitlines()), 1)
        self.assertTrue(failure_path.is_file())

    def test_recursive_cycle_imports_multiple_rounds_once_in_stable_order(self) -> None:
        cycle = self.evaluation_root / "cycles" / "daily-multiple"
        cycle.mkdir(parents=True)
        (cycle / "cycle.json").write_text('{"status":"completed"}', encoding="utf-8")
        (cycle / "summary.json").write_text('{"completed_rounds":2}', encoding="utf-8")
        self.write_failures(cycle / "rounds" / "0001-40", "scenario-b")
        self.write_failures(cycle / "rounds" / "0002-41", "scenario-a")
        registry = IssueRegistry(self.evaluation_root)

        code, output, error = self.invoke(
            ["--recursive", str(cycle), str(cycle / "rounds")],
            registry=registry,
        )
        issue_ids = output.splitlines()

        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertEqual(len(issue_ids), 2)
        self.assertEqual(issue_ids, sorted(issue_ids))
        self.assertTrue(all(registry.load(issue_id)["occurrences"] == 1 for issue_id in issue_ids))

    def test_recursive_directory_without_failure_json_returns_two(self) -> None:
        cycle = self.evaluation_root / "cycles" / "empty-cycle"
        round_directory = cycle / "rounds" / "0001-40"
        round_directory.mkdir(parents=True)
        (cycle / "cycle.json").write_text('{"status":"completed"}', encoding="utf-8")
        (cycle / "summary.json").write_text('{"completed_rounds":1}', encoding="utf-8")
        (round_directory / "summary.json").write_text('{"total":2000}', encoding="utf-8")

        code, output, error = self.invoke(["--recursive", str(cycle)])

        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("no failure JSON", error)

    def test_rejects_external_missing_and_linked_paths_with_exit_two(self) -> None:
        outside = self.repository_root.parent / "outside"
        outside_path = self.write_failures(outside, "outside-case")[0]
        missing = self.evaluation_root / "missing.json"
        linked = self.evaluation_root / "linked"
        linked_available = self.make_directory_link(linked, outside_path.parent)

        paths = [outside_path, missing]
        if linked_available:
            paths.append(linked / outside_path.name)
        for path in paths:
            with self.subTest(path=path):
                code, output, error = self.invoke([str(path)])
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertIn("import error", error)

    def test_rejects_holdout_legacy_malformed_and_duplicate_json(self) -> None:
        files = {
            "holdout.json": {
                "holdout": True,
                "scenario_hash": "a" * 64,
                "violation_codes": {"private.failure": 1},
            },
            "legacy.json": FailureRecord(
                "legacy",
                1,
                "abc",
                ("old",),
                ("old",),
                (Violation("old.failure", "blocking", "old", None),),
                1.0,
            ).to_dict(),
        }
        for name, payload in files.items():
            (self.evaluation_root / name).write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
        (self.evaluation_root / "malformed.json").write_text("{", encoding="utf-8")
        (self.evaluation_root / "duplicate.json").write_text(
            '{"scenario_id":"a","scenario_id":"b"}',
            encoding="utf-8",
        )

        for path in sorted(self.evaluation_root.glob("*.json")):
            with self.subTest(path=path.name):
                code, output, error = self.invoke([str(path)])
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertIn("import error", error)

    def test_does_not_catch_keyboard_interrupt_or_argument_system_exit(self) -> None:
        path = self.write_failures(self.evaluation_root / "run", "scenario-a")[0]

        class InterruptingRegistry:
            def ingest_failure_file(self, *args: object, **kwargs: object) -> tuple[str, ...]:
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            cli.main(
                [str(path)],
                repository_root=self.repository_root,
                registry=InterruptingRegistry(),
            )
        with self.assertRaises(SystemExit):
            cli.main([], repository_root=self.repository_root)


if __name__ == "__main__":
    unittest.main()
