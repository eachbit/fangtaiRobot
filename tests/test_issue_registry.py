from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.evaluation import issue_registry
from tests.evaluation.issue_registry import (
    HISTORY_LIMIT,
    ISSUE_ID_PATTERN,
    ISSUE_STATUSES,
    IssueRegistry,
    issue_fingerprint,
)
from tests.evaluation.schemas import (
    EvaluationReport,
    FailureRecord,
    HealthPersona,
    MenuExpectation,
    Scenario,
    Violation,
)


class IssueRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name) / "evaluation"

    @staticmethod
    def scenario(
        scenario_id: str = "scenario-001",
        *,
        seed: int = 10,
        expectation: MenuExpectation | None = None,
    ) -> Scenario:
        return Scenario(
            scenario_id=scenario_id,
            persona=HealthPersona("persona-1", "single_condition"),
            messages=("Plan dinner without peanuts.",),
            expectation=expectation or MenuExpectation(forbidden_terms=("peanut",)),
            seed=seed,
            intent="hard_constraint",
        )

    @classmethod
    def context(cls, scenario: Scenario | None = None) -> dict[str, object]:
        value = scenario or cls.scenario()
        return {
            "holdout": False,
            "health_bucket": value.persona.primary_bucket,
            "intent": value.intent,
            "expectation": value.expectation.to_dict(),
            "scenario": value.to_dict(),
        }

    @staticmethod
    def failure(
        scenario_id: str = "scenario-001",
        *,
        seed: int = 10,
        commit_sha: str = "aaa",
        original_messages: tuple[str, ...] = ("Original prompt.",),
        minimized_messages: tuple[str, ...] = ("No peanuts.",),
        code: str = "constraint.forbidden_term",
        severity: str = "blocking",
        message: str = "Blocked output.",
        evidence: object = None,
        elapsed_ms: float = 1.0,
    ) -> FailureRecord:
        return FailureRecord(
            scenario_id,
            seed,
            commit_sha,
            original_messages,
            minimized_messages,
            (Violation(code, severity, message, evidence),),
            elapsed_ms,
        )

    @staticmethod
    def report(*failures: FailureRecord) -> EvaluationReport:
        return EvaluationReport(len(failures), 0, tuple(failures), {}, {}, {})

    @staticmethod
    def issue_path(root: Path, issue_id: str, status: str = "open") -> Path:
        return root / "issues" / status / f"{issue_id}.json"

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

    @staticmethod
    def completed_cycle(issue_ids: list[str] | None = None) -> dict[str, object]:
        return {
            "status": "completed",
            "mode": "daily",
            "issue_ids": issue_ids or [],
        }

    def ingest_one(
        self,
        registry: IssueRegistry,
        failure: FailureRecord | None = None,
        *,
        context: dict[str, object] | None = None,
        observed_at: str = "2026-07-16T00:00:00Z",
    ) -> str:
        value = failure or self.failure()
        touched = registry.ingest(
            self.report(value),
            {value.scenario_id: context or self.context()},
            observed_at=observed_at,
        )
        self.assertEqual(len(touched), 1)
        return touched[0]

    def test_constants_and_issue_identifier_format(self) -> None:
        self.assertEqual(ISSUE_STATUSES, frozenset({"open", "verifying", "resolved"}))
        self.assertEqual(HISTORY_LIMIT, 256)
        self.assertIsNotNone(ISSUE_ID_PATTERN.fullmatch("issue-0123456789abcdef01234567"))
        self.assertIsNone(ISSUE_ID_PATTERN.fullmatch("issue-0123456789ABCDEF01234567"))

    def test_same_root_cause_merges_across_seed_commit_timing_and_error_text(self) -> None:
        registry = IssueRegistry(self.root)
        first = self.failure(evidence={"detail": "first"})
        second = self.failure(
            "scenario-002",
            seed=20,
            commit_sha="bbb",
            original_messages=("Different original.",),
            message="Different wording.",
            evidence={"detail": "second"},
            elapsed_ms=999.0,
        )
        first_context = self.context(self.scenario("scenario-001", seed=10))
        second_context = self.context(self.scenario("scenario-002", seed=20))

        first_ids = registry.ingest(
            self.report(first),
            {first.scenario_id: first_context},
            observed_at="2026-07-16T00:00:00Z",
        )
        second_ids = registry.ingest(
            self.report(second),
            {second.scenario_id: second_context},
            observed_at="2026-07-16T01:00:00Z",
        )

        self.assertEqual(first_ids, second_ids)
        self.assertEqual(
            issue_fingerprint(first, first.violations[0], first_context),
            issue_fingerprint(second, second.violations[0], second_context),
        )
        issue = registry.load(first_ids[0])
        self.assertEqual(issue["occurrences"], 2)
        self.assertEqual(issue["scenario_ids"], ["scenario-001", "scenario-002"])
        self.assertEqual(issue["seeds"], [10, 20])
        self.assertEqual(issue["first_seen_commit"], "aaa")
        self.assertEqual(issue["last_seen_commit"], "bbb")
        self.assertEqual(issue["latest_evidence"], {"detail": "second"})

    def test_different_violation_code_or_expectation_does_not_merge(self) -> None:
        registry = IssueRegistry(self.root)
        base = self.failure("scenario-a")
        different_code = self.failure("scenario-b", code="schema.invalid")
        different_expectation = self.failure("scenario-c")
        contexts = {
            "scenario-a": self.context(self.scenario("scenario-a")),
            "scenario-b": self.context(self.scenario("scenario-b")),
            "scenario-c": self.context(
                self.scenario(
                    "scenario-c",
                    expectation=MenuExpectation(forbidden_terms=("shellfish",)),
                )
            ),
        }

        touched = registry.ingest(
            self.report(base, different_code, different_expectation),
            contexts,
            observed_at="2026-07-16T00:00:00Z",
        )

        self.assertEqual(len(touched), 3)
        self.assertEqual(len(set(touched)), 3)

    def test_each_blocking_violation_creates_an_issue_and_nonblocking_is_ignored(self) -> None:
        failure = FailureRecord(
            "scenario-001",
            10,
            "aaa",
            ("Original prompt.",),
            ("No peanuts.",),
            (
                Violation("blocking.one", "blocking", "one", None),
                Violation("blocking.two", "blocking", "two", None),
                Violation("known.one", "known_gap", "known", None),
                Violation("soft.one", "soft_review", "soft", None),
            ),
            1.0,
        )
        registry = IssueRegistry(self.root)

        touched = registry.ingest(
            self.report(failure),
            {failure.scenario_id: self.context()},
            observed_at="2026-07-16T00:00:00Z",
        )

        self.assertEqual(len(touched), 2)
        self.assertEqual(
            {registry.load(issue_id)["violation_code"] for issue_id in touched},
            {"blocking.one", "blocking.two"},
        )

    def test_public_issue_persists_reproduction_and_strict_regression_source(self) -> None:
        scenario = self.scenario()
        failure = self.failure(evidence={"dish_ids": [7]})
        registry = IssueRegistry(self.root)

        issue_id = self.ingest_one(registry, failure, context=self.context(scenario))
        issue = registry.load(issue_id)

        self.assertEqual(issue["original_messages"], ["Original prompt."])
        self.assertEqual(issue["minimized_messages"], ["No peanuts."])
        self.assertEqual(issue["expected"], scenario.expectation.to_dict())
        self.assertEqual(issue["latest_evidence"], {"dish_ids": [7]})
        self.assertEqual(Scenario.from_dict(issue["regression_source"]), scenario)

    def test_resolved_issue_recurrence_reopens_and_updates_index(self) -> None:
        registry = IssueRegistry(self.root)
        failure = self.failure()
        issue_id = self.ingest_one(registry, failure)
        registry.set_status(issue_id, "verifying")
        registry.set_status(
            issue_id,
            "resolved",
            verification_cycle=self.completed_cycle(),
        )

        touched = registry.ingest(
            self.report(failure),
            {failure.scenario_id: self.context()},
            observed_at="2026-07-17T00:00:00Z",
        )

        self.assertEqual(touched, (issue_id,))
        issue = registry.load(issue_id)
        self.assertEqual(issue["status"], "open")
        self.assertEqual(issue["occurrences"], 2)
        self.assertTrue(self.issue_path(self.root, issue_id, "open").is_file())
        self.assertFalse(self.issue_path(self.root, issue_id, "resolved").exists())
        index = json.loads((self.root / "issues" / "index.json").read_text("utf-8"))
        self.assertEqual(index["issues"][issue_id]["path"], f"open/{issue_id}.json")

    def test_scenario_and_seed_histories_are_sorted_deduplicated_and_capped(self) -> None:
        failures = []
        contexts = {}
        for value in range(HISTORY_LIMIT + 4):
            scenario_id = f"scenario-{value:03d}"
            failures.append(self.failure(scenario_id, seed=value))
            contexts[scenario_id] = self.context(self.scenario(scenario_id, seed=value))
        registry = IssueRegistry(self.root)

        touched = registry.ingest(
            self.report(*failures, failures[-1]),
            contexts,
            observed_at="2026-07-16T00:00:00Z",
        )

        self.assertEqual(len(touched), 1)
        issue = registry.load(touched[0])
        self.assertEqual(issue["occurrences"], HISTORY_LIMIT + 5)
        self.assertEqual(issue["scenario_ids"], [f"scenario-{i:03d}" for i in range(4, 260)])
        self.assertEqual(issue["seeds"], list(range(4, 260)))

    def test_holdout_issue_does_not_persist_private_fields_or_values(self) -> None:
        secret = "PRIVATE-HOLDOUT-CONTENT"
        failure = self.failure(
            "private-case",
            original_messages=(secret,),
            minimized_messages=(secret,),
            message=secret,
            evidence={"persona": secret, "expectation": secret},
        )
        context = {
            "holdout": True,
            "scenario_hash": "0123456789abcdef0123456789abcdef",
        }
        registry = IssueRegistry(self.root)

        issue_id = self.ingest_one(registry, failure, context=context)
        issue = registry.load(issue_id)
        serialized = json.dumps(issue, sort_keys=True)

        self.assertTrue(issue["holdout"])
        self.assertEqual(issue["scenario_hash"], context["scenario_hash"])
        self.assertNotIn(secret, serialized)
        for forbidden in (
            "original_messages",
            "minimized_messages",
            "persona",
            "expectation",
            "expected",
            "scenario",
            "regression_source",
            "latest_evidence",
        ):
            self.assertNotIn(forbidden, issue)

    def test_holdout_fingerprint_uses_only_scenario_hash_and_violation_code(self) -> None:
        first = self.failure(
            "private-a",
            original_messages=("first private original",),
            minimized_messages=("first private minimum",),
            evidence={"private": "first"},
        )
        second = self.failure(
            "private-b",
            original_messages=("second private original",),
            minimized_messages=("second private minimum",),
            message="different private wording",
            evidence={"private": "second"},
        )
        first_context = {
            "holdout": True,
            "scenario_hash": "same-holdout-hash",
            "expectation": {"private": "first"},
            "intent": "private-first",
        }
        second_context = {
            "holdout": True,
            "scenario_hash": "same-holdout-hash",
            "expectation": {"private": "second"},
            "intent": "private-second",
        }

        self.assertEqual(
            issue_fingerprint(first, first.violations[0], first_context),
            issue_fingerprint(second, second.violations[0], second_context),
        )

    def test_invalid_issue_identifiers_are_rejected(self) -> None:
        registry = IssueRegistry(self.root)
        for issue_id in (
            "../issue-0123456789abcdef01234567",
            "issue-0123456789ABCDEF01234567",
            "issue-short",
            "index",
        ):
            with self.subTest(issue_id=issue_id):
                with self.assertRaises(ValueError):
                    registry.load(issue_id)
                with self.assertRaises(ValueError):
                    registry.set_status(issue_id, "open")

    def test_evaluation_root_and_issue_directories_reject_links_or_reparse_points(self) -> None:
        cases = ("evaluation_root", "issues", "open", "verifying", "resolved")
        link_supported = False
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                target = base / "target"
                target.mkdir()
                evaluation_root = base / "evaluation"
                if case == "evaluation_root":
                    link = evaluation_root
                elif case == "issues":
                    evaluation_root.mkdir()
                    link = evaluation_root / "issues"
                else:
                    (evaluation_root / "issues").mkdir(parents=True)
                    link = evaluation_root / "issues" / case
                if not self.make_directory_link(link, target):
                    continue
                link_supported = True

                with self.assertRaisesRegex(ValueError, "links or reparse points"):
                    IssueRegistry(evaluation_root)

                self.assertEqual(list(target.iterdir()), [])
        if not link_supported:
            self.skipTest("directory symlinks and junctions are unavailable")

    def test_evaluation_root_rejects_existing_link_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "target"
            target.mkdir()
            linked_ancestor = base / "linked-parent"
            if not self.make_directory_link(linked_ancestor, target):
                self.skipTest("directory symlinks and junctions are unavailable")

            with self.assertRaisesRegex(ValueError, "links or reparse points"):
                IssueRegistry(linked_ancestor / "nested" / "evaluation")

            self.assertEqual(list(target.iterdir()), [])

    def test_evaluation_root_rejects_parent_traversal_parts(self) -> None:
        path = self.root.parent / "intermediate" / ".." / "evaluation"

        with self.assertRaisesRegex(ValueError, "parent traversal"):
            IssueRegistry(path)

    def test_index_replace_failure_leaves_old_index_parseable(self) -> None:
        registry = IssueRegistry(self.root)
        index_path = self.root / "issues" / "index.json"
        old_index = index_path.read_text(encoding="utf-8")
        real_replace = os.replace

        def fail_index_replace(source: object, destination: object) -> None:
            if Path(destination) == index_path:
                raise OSError("simulated index replace failure")
            real_replace(source, destination)

        with mock.patch.object(issue_registry.os, "replace", side_effect=fail_index_replace):
            with self.assertRaisesRegex(OSError, "simulated index replace failure"):
                self.ingest_one(registry)

        self.assertEqual(index_path.read_text(encoding="utf-8"), old_index)
        self.assertIsInstance(json.loads(old_index), dict)

    def test_load_uses_strict_json_and_returns_a_defensive_object(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        first = registry.load(issue_id)
        first["status"] = "tampered"
        first["scenario_ids"].append("tampered")

        second = registry.load(issue_id)

        self.assertEqual(second["status"], "open")
        self.assertNotIn("tampered", second["scenario_ids"])

        path = self.issue_path(self.root, issue_id)
        path.write_text('{"status":"open","status":"resolved"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            registry.load(issue_id)

    def test_schema_version_rejects_boolean_true(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        path = self.issue_path(self.root, issue_id)
        issue = json.loads(path.read_text(encoding="utf-8"))
        issue["schema_version"] = True
        path.write_text(json.dumps(issue), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "schema version"):
            registry.load(issue_id)

    def test_second_instance_times_out_while_first_holds_lock(self) -> None:
        first = IssueRegistry(self.root)
        second = IssueRegistry(self.root)

        with first._locked():
            with mock.patch.object(issue_registry.time, "sleep", return_value=None):
                with self.assertRaises(TimeoutError):
                    with second._locked():
                        self.fail("second registry unexpectedly acquired the lock")

    def test_second_instance_acquires_after_first_releases_persistent_lock(self) -> None:
        first = IssueRegistry(self.root)
        second = IssueRegistry(self.root)

        with first._locked():
            pass

        self.assertTrue(first._lock_path.is_file())
        with second._locked():
            self.assertTrue(second._lock_path.is_file())

    def test_preexisting_empty_persistent_lock_file_can_be_acquired(self) -> None:
        issues_root = self.root / "issues"
        issues_root.mkdir(parents=True)
        lock_path = issues_root / ".registry.lock"
        lock_path.touch()

        with mock.patch.object(issue_registry.time, "sleep", return_value=None):
            registry = IssueRegistry(self.root)

        self.assertGreaterEqual(lock_path.stat().st_size, 1)
        with registry._locked():
            pass

    def test_closing_held_lock_descriptor_allows_reacquisition(self) -> None:
        first = IssueRegistry(self.root)
        second = IssueRegistry(self.root)
        opened_descriptors: list[int] = []
        real_open = os.open

        def capture_open(*args: object, **kwargs: object) -> int:
            descriptor = real_open(*args, **kwargs)
            opened_descriptors.append(descriptor)
            return descriptor

        lock_context = first._locked()
        with mock.patch.object(issue_registry.os, "open", side_effect=capture_open):
            lock_context.__enter__()
        descriptor = opened_descriptors[-1]
        os.fstat(descriptor)
        os.close(descriptor)
        try:
            with second._locked():
                pass
        finally:
            lock_context.__exit__(None, None, None)

    def test_status_transitions_accept_only_the_defined_flow(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)

        with self.assertRaises(ValueError):
            registry.set_status(issue_id, "resolved")
        verifying = registry.set_status(issue_id, "verifying")
        self.assertEqual(verifying["status"], "verifying")
        reopened = registry.set_status(issue_id, "open")
        self.assertEqual(reopened["status"], "open")
        registry.set_status(issue_id, "verifying")

        invalid_cycles = (
            None,
            {"status": "running", "mode": "daily", "issue_ids": []},
            {"status": "completed", "mode": "quick", "issue_ids": []},
            self.completed_cycle([issue_id]),
        )
        for cycle in invalid_cycles:
            with self.subTest(cycle=cycle):
                with self.assertRaises(ValueError):
                    registry.set_status(
                        issue_id,
                        "resolved",
                        verification_cycle=cycle,
                    )

        resolved = registry.set_status(
            issue_id,
            "resolved",
            verification_cycle=self.completed_cycle(),
        )
        self.assertEqual(resolved["status"], "resolved")
        reopened = registry.set_status(issue_id, "open")
        self.assertEqual(reopened["status"], "open")

        with self.assertRaises(ValueError):
            registry.set_status(issue_id, "open")
        with self.assertRaises(ValueError):
            registry.set_status(issue_id, "unknown")


if __name__ == "__main__":
    unittest.main()
