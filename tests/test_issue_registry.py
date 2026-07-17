from __future__ import annotations

import errno
import json
import hashlib
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
from tests.evaluation.report import write_report
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
    def completed_cycle(
        issue_ids: list[str] | None = None,
        *,
        mode: str = "daily",
    ) -> dict[str, object]:
        cycle_issue_ids = issue_ids or []
        expected_total = {"daily": 2000, "deep": 10000}[mode]
        return {
            "schema_version": 1,
            "cycle_id": "registry-verification",
            "status": "completed",
            "mode": mode,
            "base_seed": 40,
            "target_rounds": 1,
            "commit_sha": "abc123",
            "created_at": "2026-07-16T01:02:03Z",
            "updated_at": "2026-07-16T01:02:03Z",
            "completed_rounds": 1,
            "issue_ids": cycle_issue_ids,
            "rounds": [
                {
                    "index": 0,
                    "seed": 40,
                    "status": "completed",
                    "total": expected_total,
                    "passed": expected_total,
                    "failures": 0,
                    "elapsed_ms": 1.0,
                    "output_path": "rounds/0001-40",
                    "issue_ids": cycle_issue_ids,
                    "started_at": "2026-07-16T01:02:03Z",
                    "finished_at": "2026-07-16T01:02:03Z",
                    "error_type": None,
                    "error": None,
                }
            ],
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

    def write_public_failure_file(
        self,
        directory: Path,
        failure: FailureRecord | None = None,
        *,
        context: dict[str, object] | None = None,
    ) -> Path:
        value = failure or self.failure()
        output = directory / f"output-{value.scenario_id}"
        write_report(
            self.report(value),
            output,
            scenario_context={
                value.scenario_id: context
                or self.context(self.scenario(value.scenario_id, seed=value.seed))
            },
        )
        return next((output / "failures").glob("*.json"))

    def test_ingest_failure_file_imports_public_artifacts_idempotently(self) -> None:
        registry = IssueRegistry(self.root)
        first_path = self.write_public_failure_file(self.root.parent)

        first = registry.ingest_failure_file(
            first_path,
            observed_at="2026-07-16T00:00:00Z",
            observation_id="failure-file:first",
        )
        repeated = registry.ingest_failure_file(
            first_path,
            observed_at="2026-07-16T00:00:00Z",
            observation_id="failure-file:first",
        )

        self.assertEqual(first, repeated)
        self.assertEqual(registry.load(first[0])["occurrences"], 1)

        second_failure = self.failure(
            "scenario-002",
            seed=20,
            code="schema.invalid",
        )
        second_path = self.write_public_failure_file(
            self.root.parent,
            second_failure,
        )
        second = registry.ingest_failure_file(
            second_path,
            observed_at="2026-07-17T00:00:00Z",
            observation_id="failure-file:second",
        )
        self.assertNotEqual(first, second)

    def test_ingest_failure_file_rejects_holdout_and_legacy_artifacts(self) -> None:
        registry = IssueRegistry(self.root)
        secret = "PRIVATE-HOLDOUT-MESSAGE"
        holdout_path = self.root.parent / "holdout.json"
        holdout_path.write_text(
            json.dumps(
                {
                    "holdout": True,
                    "scenario_hash": "a" * 64,
                    "violation_codes": {"private.failure": 1},
                    "private": secret,
                }
            ),
            encoding="utf-8",
        )
        legacy_path = self.root.parent / "legacy.json"
        legacy_path.write_text(json.dumps(self.failure().to_dict()), encoding="utf-8")

        for path in (holdout_path, legacy_path):
            with self.subTest(path=path.name):
                with self.assertRaises(ValueError) as raised:
                    registry.ingest_failure_file(
                        path,
                        observed_at="2026-07-16T00:00:00Z",
                    )
                self.assertNotIn(secret, str(raised.exception))
        self.assertEqual(list(registry.status_directories["open"].glob("*.json")), [])

    def test_ingest_failure_file_rejects_malformed_and_unsafe_inputs(self) -> None:
        registry = IssueRegistry(self.root)
        valid_path = self.write_public_failure_file(self.root.parent)
        valid = json.loads(valid_path.read_text(encoding="utf-8"))
        cases = {
            "malformed.json": "{",
            "duplicate.json": '{"scenario_id":"a","scenario_id":"b"}',
            "nonfinite.json": '{"elapsed_ms":NaN}',
            "array.json": "[]",
            "extra.json": json.dumps({**valid, "messages": ["dangerous"]}),
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                path = self.root.parent / name
                path.write_text(content, encoding="utf-8")
                with self.assertRaises((ValueError, UnicodeError)):
                    registry.ingest_failure_file(
                        path,
                        observed_at="2026-07-16T00:00:00Z",
                    )

        with self.assertRaises(ValueError):
            registry.ingest_failure_file(
                self.root.parent,
                observed_at="2026-07-16T00:00:00Z",
            )

        link = self.root.parent / "linked-failure.json"
        try:
            link.symlink_to(valid_path)
        except OSError:
            pass
        else:
            with self.assertRaisesRegex(ValueError, "link|reparse"):
                registry.ingest_failure_file(
                    link,
                    observed_at="2026-07-16T00:00:00Z",
                )

    def test_constants_and_issue_identifier_format(self) -> None:
        self.assertEqual(ISSUE_STATUSES, frozenset({"open", "verifying", "resolved"}))
        self.assertEqual(HISTORY_LIMIT, 256)
        self.assertIsNotNone(ISSUE_ID_PATTERN.fullmatch("issue-0123456789abcdef01234567"))
        self.assertIsNone(ISSUE_ID_PATTERN.fullmatch("issue-0123456789ABCDEF01234567"))

    def test_observation_id_is_idempotent_and_marker_contains_no_private_data(self) -> None:
        secret = "PRIVATE-OBSERVATION-CONTENT"
        registry = IssueRegistry(self.root)
        failure = self.failure(
            original_messages=(secret,),
            minimized_messages=(secret,),
            evidence={"private": secret},
        )
        observation_id = "cycle-a:round:0"

        first_ids = registry.ingest(
            self.report(failure),
            {failure.scenario_id: self.context()},
            observed_at="2026-07-16T00:00:00Z",
            observation_id=observation_id,
        )
        issue_before = registry.load(first_ids[0])
        second_ids = registry.ingest(
            self.report(failure),
            {failure.scenario_id: self.context()},
            observed_at="2026-07-17T00:00:00Z",
            observation_id=observation_id,
        )

        self.assertEqual(second_ids, first_ids)
        self.assertEqual(registry.load(first_ids[0]), issue_before)
        self.assertEqual(issue_before["occurrences"], 1)
        marker_path = registry._observation_path(observation_id)
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertEqual(
            marker,
            {
                "schema_version": 1,
                "observation_hash": hashlib.sha256(
                    observation_id.encode("utf-8")
                ).hexdigest(),
                "issue_ids": list(first_ids),
            },
        )
        self.assertNotIn(secret, marker_path.read_text(encoding="utf-8"))

    def test_observation_id_reuse_with_different_issue_set_is_rejected_unchanged(self) -> None:
        registry = IssueRegistry(self.root)
        first = self.failure(code="blocking.first")
        second = self.failure(code="blocking.second")
        observation_id = "cycle-a:round:1"
        first_ids = registry.ingest(
            self.report(first),
            {first.scenario_id: self.context()},
            observed_at="2026-07-16T00:00:00Z",
            observation_id=observation_id,
        )
        issue_path = self.issue_path(self.root, first_ids[0])
        marker_path = registry._observation_path(observation_id)
        index_path = self.root / "issues" / "index.json"
        before = {
            path: path.read_text(encoding="utf-8")
            for path in (issue_path, marker_path, index_path)
        }

        with self.assertRaisesRegex(ValueError, "observation_id|issue set"):
            registry.ingest(
                self.report(second),
                {second.scenario_id: self.context()},
                observed_at="2026-07-17T00:00:00Z",
                observation_id=observation_id,
            )

        self.assertEqual(
            {path: path.read_text(encoding="utf-8") for path in before},
            before,
        )
        self.assertEqual(registry.load(first_ids[0])["occurrences"], 1)

    def test_ingest_without_observation_id_preserves_occurrence_behavior(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)

        second_ids = registry.ingest(
            self.report(self.failure()),
            {"scenario-001": self.context()},
            observed_at="2026-07-17T00:00:00Z",
        )

        self.assertEqual(second_ids, (issue_id,))
        self.assertEqual(registry.load(issue_id)["occurrences"], 2)

    def test_empty_observation_marker_is_idempotent(self) -> None:
        registry = IssueRegistry(self.root)
        report = EvaluationReport(1, 1, (), {}, {}, {})

        self.assertEqual(
            registry.ingest(
                report,
                {},
                observed_at="2026-07-16T00:00:00Z",
                observation_id="cycle-empty:round:0",
            ),
            (),
        )
        marker_path = registry._observation_path("cycle-empty:round:0")
        self.assertTrue(marker_path.is_file())
        self.assertEqual(
            registry.ingest(
                report,
                {},
                observed_at="2026-07-17T00:00:00Z",
                observation_id="cycle-empty:round:0",
            ),
            (),
        )

    def test_atomic_write_syncs_parent_after_replace_and_propagates_sync_error(self) -> None:
        self.root.mkdir(parents=True)
        path = self.root / "atomic.json"
        events: list[tuple[str, Path]] = []
        real_replace = issue_registry.os.replace

        def replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
            events.append(("replace", Path(destination)))
            real_replace(source, destination)

        def sync_parent(parent: Path) -> None:
            events.append(("sync", Path(parent)))

        with (
            mock.patch.object(issue_registry.os, "replace", side_effect=replace),
            mock.patch.object(
                issue_registry,
                "_fsync_directory",
                side_effect=sync_parent,
                create=True,
            ),
        ):
            issue_registry._atomic_write_json(path, {"value": 1})

        self.assertEqual(events, [("replace", path), ("sync", path.parent)])

        with mock.patch.object(
            issue_registry,
            "_fsync_directory",
            side_effect=OSError(errno.EIO, "sync failed"),
            create=True,
        ):
            with self.assertRaisesRegex(OSError, "sync failed"):
                issue_registry._atomic_write_json(path, {"value": 2})

    def test_atomic_create_syncs_parent_after_link_and_propagates_sync_error(self) -> None:
        self.root.mkdir(parents=True)
        path = self.root / "created.json"
        events: list[tuple[str, Path]] = []
        real_link = issue_registry.os.link

        def link(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
            events.append(("link", Path(destination)))
            real_link(source, destination)

        def sync_parent(parent: Path) -> None:
            events.append(("sync", Path(parent)))

        with (
            mock.patch.object(issue_registry.os, "link", side_effect=link),
            mock.patch.object(issue_registry, "_fsync_directory", side_effect=sync_parent),
        ):
            self.assertTrue(issue_registry._atomic_create_json(path, {"value": 1}))

        self.assertEqual(
            events,
            [("link", path), ("sync", path.parent), ("sync", path.parent)],
        )

        failing_path = self.root / "sync-failure.json"
        with (
            mock.patch.object(
                issue_registry,
                "_fsync_directory",
                side_effect=[OSError(errno.EIO, "sync failed"), None],
            ),
            self.assertRaisesRegex(OSError, "sync failed"),
        ):
            issue_registry._atomic_create_json(failing_path, {"value": 2})

    def test_transaction_deletions_use_durable_unlink_for_journal_and_source(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        source = self.issue_path(self.root, issue_id, "open")
        calls: list[
            tuple[Path, tuple[int, int, Path] | None, tuple[int, int] | None]
        ] = []

        def durable_unlink(
            path: Path,
            *,
            expected_parent_identity: tuple[int, int, Path] | None = None,
            expected_file_identity: tuple[int, int] | None = None,
        ) -> None:
            calls.append((Path(path), expected_parent_identity, expected_file_identity))
            Path(path).unlink()

        with mock.patch.object(
            issue_registry,
            "_durable_unlink",
            side_effect=durable_unlink,
            create=True,
        ):
            registry.set_status(issue_id, "verifying")

        paths = [path for path, _, _ in calls]
        self.assertIn(source, paths)
        self.assertIn(registry._journal_path, paths)
        self.assertLess(paths.index(source), paths.index(registry._journal_path))
        self.assertIsNotNone(calls[paths.index(source)][1])
        self.assertIsNotNone(calls[paths.index(source)][2])
        self.assertIsNotNone(calls[paths.index(registry._journal_path)][1])

    @unittest.skipUnless(os.name == "nt", "Windows handle deletion only")
    def test_windows_delete_failure_never_falls_back_to_path_unlink(self) -> None:
        self.root.mkdir(parents=True)
        path = self.root / "windows-delete.json"
        path.write_text("delete me", encoding="utf-8")
        parent_identity = issue_registry._directory_identity(path.parent)

        with (
            mock.patch.object(
                issue_registry,
                "_windows_delete_by_handle",
                side_effect=OSError(errno.EIO, "WinAPI delete failure"),
                create=True,
            ) as delete_by_handle,
            self.assertRaisesRegex(OSError, "WinAPI delete failure"),
        ):
            issue_registry._durable_unlink(
                path,
                expected_parent_identity=parent_identity,
            )

        delete_by_handle.assert_called_once()
        self.assertTrue(path.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows handle deletion only")
    def test_windows_delete_handle_closes_when_identity_read_fails(self) -> None:
        delete_by_handle = getattr(issue_registry, "_windows_delete_by_handle", None)
        self.assertIsNotNone(delete_by_handle)
        with (
            mock.patch.object(
                issue_registry,
                "_windows_open_delete_handle",
                return_value=91,
                create=True,
            ),
            mock.patch.object(
                issue_registry,
                "_windows_handle_identity",
                side_effect=OSError(errno.EIO, "identity failure"),
                create=True,
            ),
            mock.patch.object(
                issue_registry,
                "_windows_close_handle",
                create=True,
            ) as close_handle,
            self.assertRaisesRegex(OSError, "identity failure"),
        ):
            delete_by_handle(self.root / "source.json", (1, 2))

        close_handle.assert_called_once_with(91)

    def test_directory_fsync_ignores_only_supported_windows_errors(self) -> None:
        with (
            mock.patch.object(issue_registry.os, "name", "nt"),
            mock.patch.object(
                issue_registry.os,
                "open",
                side_effect=OSError(errno.EACCES, "directory handles unsupported"),
            ),
        ):
            issue_registry._fsync_directory(self.root)

        with (
            mock.patch.object(issue_registry.os, "name", "nt"),
            mock.patch.object(issue_registry.os, "open", return_value=91),
            mock.patch.object(
                issue_registry.os,
                "fsync",
                side_effect=OSError(errno.EIO, "disk failure"),
            ),
            mock.patch.object(issue_registry.os, "close") as close,
            self.assertRaisesRegex(OSError, "disk failure"),
        ):
            issue_registry._fsync_directory(self.root)
        close.assert_called_once_with(91)

    def test_observation_marker_write_failure_rolls_back_issue_and_index(self) -> None:
        registry = IssueRegistry(self.root)
        old_index = registry.index_path.read_text(encoding="utf-8")
        real_atomic_write = issue_registry._atomic_write_json

        def fail_marker(path: Path, value: object) -> None:
            if path.parent == registry.observations_root:
                raise OSError("simulated marker write failure")
            real_atomic_write(path, value)

        with mock.patch.object(
            issue_registry,
            "_atomic_write_json",
            side_effect=fail_marker,
        ):
            with self.assertRaisesRegex(OSError, "marker write failure"):
                registry.ingest(
                    self.report(self.failure()),
                    {"scenario-001": self.context()},
                    observed_at="2026-07-16T00:00:00Z",
                    observation_id="cycle-failure:round:0",
                )

        self.assertEqual(registry.index_path.read_text(encoding="utf-8"), old_index)
        self.assertEqual(list(registry.status_directories["open"].glob("*.json")), [])
        self.assertEqual(list(registry.observations_root.glob("*.json")), [])
        recovered = IssueRegistry(self.root)
        self.assertEqual(list(recovered.observations_root.glob("*.json")), [])

    def test_observation_index_failure_rolls_back_marker_and_issue(self) -> None:
        registry = IssueRegistry(self.root)
        old_index = registry.index_path.read_text(encoding="utf-8")
        real_replace = os.replace
        failed = False

        def fail_index_once(source: object, destination: object) -> None:
            nonlocal failed
            if Path(destination) == registry.index_path and not failed:
                failed = True
                raise OSError("simulated observation index failure")
            real_replace(source, destination)

        with mock.patch.object(
            issue_registry.os,
            "replace",
            side_effect=fail_index_once,
        ):
            with self.assertRaisesRegex(OSError, "observation index failure"):
                registry.ingest(
                    self.report(self.failure()),
                    {"scenario-001": self.context()},
                    observed_at="2026-07-16T00:00:00Z",
                    observation_id="cycle-failure:round:1",
                )

        self.assertEqual(registry.index_path.read_text(encoding="utf-8"), old_index)
        self.assertEqual(list(registry.status_directories["open"].glob("*.json")), [])
        self.assertEqual(list(registry.observations_root.glob("*.json")), [])
        IssueRegistry(self.root)

    def test_corrupt_observation_marker_is_rejected(self) -> None:
        registry = IssueRegistry(self.root)
        observation_id = "cycle-corrupt:round:0"
        registry.ingest(
            self.report(self.failure()),
            {"scenario-001": self.context()},
            observed_at="2026-07-16T00:00:00Z",
            observation_id=observation_id,
        )
        marker_path = registry._observation_path(observation_id)
        marker_path.write_text('{"schema_version":1}', encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "observation"):
            IssueRegistry(self.root)

    def test_symlinked_observation_marker_is_rejected(self) -> None:
        registry = IssueRegistry(self.root)
        observation_id = "cycle-link:round:0"
        registry.ingest(
            self.report(self.failure()),
            {"scenario-001": self.context()},
            observed_at="2026-07-16T00:00:00Z",
            observation_id=observation_id,
        )
        marker_path = registry._observation_path(observation_id)
        marker_content = marker_path.read_text(encoding="utf-8")
        outside = Path(self.temporary_directory.name) / "outside-marker.json"
        outside.write_text(marker_content, encoding="utf-8")
        marker_path.unlink()
        try:
            marker_path.symlink_to(outside)
        except OSError:
            self.skipTest("file links are unavailable")

        with self.assertRaisesRegex(ValueError, "link|reparse"):
            IssueRegistry(self.root)

    def test_journal_rejects_non_null_observation_before_image(self) -> None:
        registry = IssueRegistry(self.root)
        observation_id = "cycle-journal:round:0"
        registry.ingest(
            self.report(self.failure()),
            {"scenario-001": self.context()},
            observed_at="2026-07-16T00:00:00Z",
            observation_id=observation_id,
        )
        marker_path = registry._observation_path(observation_id)
        index = json.loads(registry.index_path.read_text(encoding="utf-8"))
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        issue_registry._atomic_write_json(
            registry._journal_path,
            {
                "schema_version": 1,
                "files": [
                    {"path": "index.json", "before": index},
                    {
                        "path": f"observations/{marker_path.name}",
                        "before": marker,
                    },
                ],
            },
        )

        with self.assertRaisesRegex(ValueError, "observation.*before-image"):
            IssueRegistry(self.root)

        self.assertTrue(registry._journal_path.is_file())

    def _begin_empty_observation_transaction(
        self,
        registry: IssueRegistry,
        observation_id: str,
    ) -> tuple[Path, dict[str, object]]:
        observation_hash = hashlib.sha256(observation_id.encode("utf-8")).hexdigest()
        marker_path = registry._observation_path(observation_id)
        marker = {
            "schema_version": 1,
            "observation_hash": observation_hash,
            "issue_ids": [],
        }
        registry._begin_transaction((registry.index_path, marker_path))
        issue_registry._atomic_write_json(marker_path, marker)
        return marker_path, marker

    def test_recovery_removes_exact_observation_atomic_temp_file(self) -> None:
        registry = IssueRegistry(self.root)
        marker_path, _ = self._begin_empty_observation_transaction(
            registry,
            "cycle-temp:round:0",
        )
        temporary_path = registry.observations_root / (
            f".{marker_path.name}.deadbeef.tmp"
        )
        temporary_path.write_text("partial marker", encoding="utf-8")

        recovered = IssueRegistry(self.root)

        self.assertFalse(temporary_path.exists())
        self.assertFalse(marker_path.exists())
        self.assertFalse(recovered._journal_path.exists())
        issue_id = self.ingest_one(recovered)
        self.assertEqual(recovered.load(issue_id)["occurrences"], 1)

    def test_recovery_preserves_matching_temp_for_marker_absent_from_journal(self) -> None:
        registry = IssueRegistry(self.root)
        self._begin_empty_observation_transaction(
            registry,
            "cycle-temp:allowlisted",
        )
        unrelated_marker = registry._observation_path("cycle-temp:unrelated")
        unrelated_temp = registry.observations_root / (
            f".{unrelated_marker.name}.unknown.tmp"
        )
        original = b"unrelated marker temp bytes\x00\xff"
        unrelated_temp.write_bytes(original)

        with self.assertRaisesRegex(ValueError, "invalid observation marker filename"):
            IssueRegistry(self.root)

        self.assertEqual(unrelated_temp.read_bytes(), original)
        self.assertTrue(registry._journal_path.is_file())

    def test_recovery_does_not_delete_unknown_temp_file(self) -> None:
        registry = IssueRegistry(self.root)
        self._begin_empty_observation_transaction(
            registry,
            "cycle-temp:round:1",
        )
        unknown = registry.observations_root / "unknown.tmp"
        unknown.write_text("do not delete", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "invalid observation marker filename"):
            IssueRegistry(self.root)

        self.assertEqual(unknown.read_text(encoding="utf-8"), "do not delete")
        self.assertTrue(registry._journal_path.is_file())

    def test_recovery_rejects_matching_observation_temp_symlink(self) -> None:
        registry = IssueRegistry(self.root)
        marker_path, _ = self._begin_empty_observation_transaction(
            registry,
            "cycle-temp:round:2",
        )
        outside = Path(self.temporary_directory.name) / "outside-temp-target"
        outside.write_text("outside unchanged", encoding="utf-8")
        temporary_path = registry.observations_root / (
            f".{marker_path.name}.symlink.tmp"
        )
        try:
            temporary_path.symlink_to(outside)
        except OSError:
            self.skipTest("file links are unavailable")

        with self.assertRaisesRegex(ValueError, "link|reparse"):
            IssueRegistry(self.root)

        self.assertTrue(temporary_path.is_symlink())
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside unchanged")
        self.assertTrue(registry._journal_path.is_file())

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

    def test_new_issue_rejects_context_source_id_or_seed_without_writes(self) -> None:
        for index, field in enumerate(("scenario_id", "seed")):
            with self.subTest(field=field):
                root = self.root.parent / f"forged-new-{index}"
                registry = IssueRegistry(root)
                failure = self.failure()
                context = self.context(self.scenario())
                source = dict(context["scenario"])
                source[field] = "scenario-forged" if field == "scenario_id" else 999
                context["scenario"] = source
                old_index = registry.index_path.read_bytes()

                with self.assertRaisesRegex(ValueError, field):
                    registry.ingest(
                        self.report(failure),
                        {failure.scenario_id: context},
                        observed_at="2026-07-16T00:00:00Z",
                    )

                self.assertEqual(registry.index_path.read_bytes(), old_index)
                self.assertEqual(list(registry.status_directories["open"].glob("*.json")), [])

    def test_merge_rejects_context_source_id_or_seed_without_writes(self) -> None:
        for index, field in enumerate(("scenario_id", "seed")):
            with self.subTest(field=field):
                root = self.root.parent / f"forged-merge-{index}"
                registry = IssueRegistry(root)
                failure = self.failure()
                issue_id = self.ingest_one(registry, failure)
                issue_path = self.issue_path(root, issue_id)
                old_issue = issue_path.read_bytes()
                old_index = registry.index_path.read_bytes()
                context = self.context(self.scenario())
                source = dict(context["scenario"])
                source[field] = "scenario-forged" if field == "scenario_id" else 999
                context["scenario"] = source

                with self.assertRaisesRegex(ValueError, field):
                    registry.ingest(
                        self.report(failure),
                        {failure.scenario_id: context},
                        observed_at="2026-07-17T00:00:00Z",
                    )

                self.assertEqual(issue_path.read_bytes(), old_issue)
                self.assertEqual(registry.index_path.read_bytes(), old_index)

    def test_public_issue_exports_strict_regression_candidate_idempotently(self) -> None:
        expectation = MenuExpectation(
            forbidden_terms=("peanut",),
            dish_count=3,
        )
        scenario = Scenario(
            scenario_id="scenario-multi-turn",
            persona=HealthPersona("persona-1", "single_condition"),
            messages=("Original scenario message.",),
            expectation=expectation,
            seed=41,
            intent="hard_constraint",
            dialogue_mode="multi_turn",
        )
        failure = self.failure(
            "scenario-multi-turn",
            seed=41,
            minimized_messages=("No peanuts at dinner.",),
            evidence={
                "expectation": {"forbidden_terms": ["evidence-must-not-win"]},
                "required_terms": ["also-not-an-expectation"],
            },
        )
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry, failure, context=self.context(scenario))

        first_path = registry.export_regression_candidate(issue_id)
        first_bytes = first_path.read_bytes()
        second_path = registry.export_regression_candidate(issue_id)
        candidate = json.loads(first_path.read_text(encoding="utf-8"))

        self.assertEqual(first_path, second_path)
        self.assertEqual(first_path.read_bytes(), first_bytes)
        self.assertEqual(
            first_path,
            self.root / "candidates" / "regressions" / f"{issue_id}.json",
        )
        self.assertEqual(candidate["scenario_id"], f"regression-{issue_id[6:]}")
        self.assertEqual(candidate["messages"], ["No peanuts at dinner."])
        self.assertEqual(candidate["persona"], scenario.persona.to_dict())
        self.assertEqual(candidate["intent"], scenario.intent)
        self.assertEqual(candidate["dialogue_mode"], scenario.dialogue_mode)
        self.assertEqual(candidate["seed"], scenario.seed)
        self.assertEqual(candidate["expectation"], expectation.to_dict())
        self.assertEqual(Scenario.from_dict(candidate).to_dict(), candidate)

    def test_export_regression_candidate_rejects_holdout_without_writing(self) -> None:
        failure = self.failure("private-case")
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(
            registry,
            failure,
            context={"holdout": True, "scenario_hash": "a" * 64},
        )

        with self.assertRaisesRegex(ValueError, "holdout"):
            registry.export_regression_candidate(issue_id)

        self.assertEqual(list((self.root / "candidates" / "regressions").iterdir()), [])

    def test_export_regression_candidate_strictly_validates_generated_scenario(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        real_from_dict = Scenario.from_dict

        def reject_generated(payload: dict[str, object]) -> Scenario:
            if str(payload.get("scenario_id", "")).startswith("regression-"):
                raise ValueError("generated candidate is invalid")
            return real_from_dict(payload)

        with (
            mock.patch.object(
                issue_registry.Scenario,
                "from_dict",
                side_effect=reject_generated,
            ),
            self.assertRaisesRegex(ValueError, "generated candidate is invalid"),
        ):
            registry.export_regression_candidate(issue_id)

        self.assertEqual(list((self.root / "candidates" / "regressions").iterdir()), [])

    def test_export_regression_candidate_rejects_different_existing_content(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        candidate_path = self.root / "candidates" / "regressions" / f"{issue_id}.json"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text('{"existing":true}\n', encoding="utf-8")

        with self.assertRaisesRegex(FileExistsError, "different"):
            registry.export_regression_candidate(issue_id)

        self.assertEqual(candidate_path.read_text(encoding="utf-8"), '{"existing":true}\n')

    def test_export_regression_candidate_rejects_linked_paths(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        outside = self.root.parent / "outside-candidates"
        outside.mkdir()
        regressions_root = self.root / "candidates" / "regressions"
        regressions_root.rmdir()
        if not self.make_directory_link(regressions_root, outside):
            self.skipTest("directory symlinks and junctions are unavailable")

        with self.assertRaisesRegex(ValueError, "link|reparse"):
            registry.export_regression_candidate(issue_id)

        self.assertEqual(list(outside.iterdir()), [])

    def test_export_regression_candidate_rejects_linked_destination_file(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        destination = self.root / "candidates" / "regressions" / f"{issue_id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        outside = self.root.parent / "outside-candidate.json"
        outside.write_text('{"outside":true}\n', encoding="utf-8")
        try:
            destination.symlink_to(outside)
        except OSError:
            self.skipTest("file links are unavailable")

        with self.assertRaisesRegex(ValueError, "link|reparse"):
            registry.export_regression_candidate(issue_id)

        self.assertEqual(outside.read_text(encoding="utf-8"), '{"outside":true}\n')

    def test_existing_candidate_read_rejects_link_swap_after_path_checks(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        destination = registry.export_regression_candidate(issue_id)
        original = destination.with_name(f"{destination.stem}-original.json")
        outside = self.root.parent / "outside-matching-candidate.json"
        outside.write_bytes(destination.read_bytes())
        real_open = os.open
        swapped = False

        def open_then_swap(
            path: str | os.PathLike[str],
            flags: int,
            mode: int = 0o777,
        ) -> int:
            nonlocal swapped
            if Path(path) == destination and not swapped:
                destination.rename(original)
                try:
                    destination.symlink_to(outside)
                except OSError:
                    original.rename(destination)
                    self.skipTest("file links are unavailable")
                swapped = True
            return real_open(path, flags, mode)

        try:
            with (
                mock.patch.object(issue_registry.os, "open", side_effect=open_then_swap),
                self.assertRaisesRegex(ValueError, "changed|link|reparse"),
            ):
                registry.export_regression_candidate(issue_id)
        finally:
            if swapped:
                destination.unlink()
                original.rename(destination)

        self.assertEqual(outside.read_bytes(), destination.read_bytes())

    def test_export_rejects_regression_source_not_tracked_by_issue(self) -> None:
        mutations = {
            "scenario_id": lambda source: source.update(scenario_id="scenario-untracked"),
            "seed": lambda source: source.update(seed=999),
            "intent": lambda source: source.update(intent="untracked-intent"),
            "health_bucket": lambda source: source.update(
                persona=HealthPersona("persona-other", "healthy").to_dict()
            ),
            "expectation": lambda source: source.update(
                expectation=MenuExpectation(dish_count=9).to_dict()
            ),
        }

        for index, (field, mutate) in enumerate(mutations.items()):
            with self.subTest(field=field):
                root = self.root.parent / f"evaluation-source-{index}"
                registry = IssueRegistry(root)
                issue_id = self.ingest_one(registry)
                issue_path = self.issue_path(root, issue_id)
                issue = json.loads(issue_path.read_text(encoding="utf-8"))
                mutate(issue["regression_source"])
                issue_registry._atomic_write_json(issue_path, issue)

                with self.assertRaisesRegex(ValueError, "regression source"):
                    registry.export_regression_candidate(issue_id)

                self.assertEqual(list((root / "candidates" / "regressions").iterdir()), [])

    def test_export_rejects_empty_or_blank_minimized_messages(self) -> None:
        for index, minimized_messages in enumerate(([], [""])):
            with self.subTest(minimized_messages=minimized_messages):
                root = self.root.parent / f"evaluation-messages-{index}"
                registry = IssueRegistry(root)
                issue_id = self.ingest_one(registry)
                issue_path = self.issue_path(root, issue_id)
                issue = json.loads(issue_path.read_text(encoding="utf-8"))
                issue["minimized_messages"] = minimized_messages
                issue_registry._atomic_write_json(issue_path, issue)

                with self.assertRaisesRegex(ValueError, "minimized_messages"):
                    registry.export_regression_candidate(issue_id)

                self.assertEqual(list((root / "candidates" / "regressions").iterdir()), [])

    def test_empty_minimized_messages_are_recorded_but_cannot_be_exported(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(
            registry,
            self.failure(minimized_messages=()),
        )

        self.assertEqual(registry.load(issue_id)["minimized_messages"], [])
        with self.assertRaisesRegex(ValueError, "minimized_messages"):
            registry.export_regression_candidate(issue_id)

    def test_candidate_layout_is_persistent_and_checked_on_every_lock(self) -> None:
        registry = IssueRegistry(self.root)
        candidates_root = self.root / "candidates"
        regressions_root = candidates_root / "regressions"

        self.assertTrue(candidates_root.is_dir())
        self.assertTrue(regressions_root.is_dir())

        outside = self.root.parent / "outside-persistent-candidates"
        outside.mkdir()
        regressions_root.rmdir()
        if not self.make_directory_link(regressions_root, outside):
            self.skipTest("directory symlinks and junctions are unavailable")

        with self.assertRaisesRegex(ValueError, "link|reparse"):
            registry.load("issue-0123456789abcdef01234567")

    def test_candidate_atomic_create_rejects_directory_swap_before_temp_open(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        regressions_root = self.root / "candidates" / "regressions"
        original_root = self.root / "candidates" / "regressions-original"
        outside = self.root.parent / "outside-race-candidates"
        outside.mkdir()
        real_named_temporary_file = issue_registry.tempfile.NamedTemporaryFile
        swapped = False

        def swap_then_open(*args: object, **kwargs: object):
            nonlocal swapped
            if not swapped:
                regressions_root.rename(original_root)
                if not self.make_directory_link(regressions_root, outside):
                    original_root.rename(regressions_root)
                    self.skipTest("directory symlinks and junctions are unavailable")
                swapped = True
            return real_named_temporary_file(*args, **kwargs)

        try:
            with (
                mock.patch.object(
                    issue_registry.tempfile,
                    "NamedTemporaryFile",
                    side_effect=swap_then_open,
                ),
                self.assertRaisesRegex(ValueError, "changed|link|reparse"),
            ):
                registry.export_regression_candidate(issue_id)
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            if swapped:
                if regressions_root.is_symlink():
                    regressions_root.unlink()
                else:
                    os.rmdir(regressions_root)
                original_root.rename(regressions_root)

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

    def test_capped_histories_retain_latest_regression_source_for_export(self) -> None:
        failures = []
        contexts = {}
        for value in range(1, HISTORY_LIMIT + 2):
            scenario_id = f"scenario-{value:04d}"
            failures.append(self.failure(scenario_id, seed=value))
            contexts[scenario_id] = self.context(
                self.scenario(scenario_id, seed=value)
            )
        latest = self.failure("scenario-0000", seed=0)
        contexts[latest.scenario_id] = self.context(
            self.scenario(latest.scenario_id, seed=latest.seed)
        )
        registry = IssueRegistry(self.root)

        touched = registry.ingest(
            self.report(*failures, latest, latest),
            contexts,
            observed_at="2026-07-17T00:00:00Z",
        )

        self.assertEqual(len(touched), 1)
        issue_id = touched[0]
        issue = registry.load(issue_id)
        self.assertLessEqual(len(issue["scenario_ids"]), HISTORY_LIMIT)
        self.assertLessEqual(len(issue["seeds"]), HISTORY_LIMIT)
        self.assertEqual(issue["scenario_ids"], sorted(set(issue["scenario_ids"])))
        self.assertEqual(issue["seeds"], sorted(set(issue["seeds"])))
        self.assertEqual(issue["regression_source"]["scenario_id"], "scenario-0000")
        self.assertEqual(issue["regression_source"]["seed"], 0)
        self.assertIn("scenario-0000", issue["scenario_ids"])
        self.assertIn(0, issue["seeds"])
        self.assertEqual(
            issue["scenario_ids"],
            ["scenario-0000", *(f"scenario-{value:04d}" for value in range(3, 258))],
        )
        self.assertEqual(issue["seeds"], [0, *range(3, 258)])

        candidate_path = registry.export_regression_candidate(issue_id)
        candidate_bytes = candidate_path.read_bytes()
        candidate = json.loads(candidate_bytes)
        self.assertEqual(candidate["seed"], 0)

        old_scenario = self.scenario("scenario-9999", seed=9999)
        registry.ingest(
            self.report(self.failure(old_scenario.scenario_id, seed=old_scenario.seed)),
            {old_scenario.scenario_id: self.context(old_scenario)},
            observed_at="2026-07-16T00:00:00Z",
        )

        after_old = registry.load(issue_id)
        self.assertEqual(after_old["regression_source"], issue["regression_source"])
        self.assertIn("scenario-0000", after_old["scenario_ids"])
        self.assertIn(0, after_old["seeds"])
        self.assertEqual(after_old["scenario_ids"], sorted(set(after_old["scenario_ids"])))
        self.assertEqual(after_old["seeds"], sorted(set(after_old["seeds"])))
        self.assertLessEqual(len(after_old["scenario_ids"]), HISTORY_LIMIT)
        self.assertLessEqual(len(after_old["seeds"]), HISTORY_LIMIT)
        self.assertEqual(registry.export_regression_candidate(issue_id), candidate_path)
        self.assertEqual(candidate_path.read_bytes(), candidate_bytes)

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
            "scenario_hash": "a" * 64,
            "expectation": {"private": "first"},
            "intent": "private-first",
        }
        second_context = {
            "holdout": True,
            "scenario_hash": "a" * 64,
            "expectation": {"private": "second"},
            "intent": "private-second",
        }

        self.assertEqual(
            issue_fingerprint(first, first.violations[0], first_context),
            issue_fingerprint(second, second.violations[0], second_context),
        )

    def test_holdout_scenario_hash_rejects_private_plaintext(self) -> None:
        failure = self.failure("private-case")
        context = {
            "holdout": True,
            "scenario_hash": "private scenario description",
        }

        with self.assertRaisesRegex(ValueError, "hexadecimal digest"):
            issue_fingerprint(failure, failure.violations[0], context)

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

    def test_batch_ingest_second_issue_write_failure_rolls_back_all_files(self) -> None:
        registry = IssueRegistry(self.root)
        index_path = self.root / "issues" / "index.json"
        old_index = index_path.read_text(encoding="utf-8")
        first = self.failure("scenario-a", code="blocking.a")
        second = self.failure("scenario-b", code="blocking.b")
        contexts = {
            "scenario-a": self.context(self.scenario("scenario-a")),
            "scenario-b": self.context(self.scenario("scenario-b")),
        }
        real_atomic_write = issue_registry._atomic_write_json
        issue_writes = 0

        def fail_second_issue_write(path: Path, value: object) -> None:
            nonlocal issue_writes
            if path.parent == registry.status_directories["open"]:
                issue_writes += 1
                if issue_writes == 2:
                    raise OSError("simulated second issue write failure")
            real_atomic_write(path, value)

        with mock.patch.object(
            issue_registry,
            "_atomic_write_json",
            side_effect=fail_second_issue_write,
        ):
            with self.assertRaisesRegex(OSError, "second issue write failure"):
                registry.ingest(
                    self.report(first, second),
                    contexts,
                    observed_at="2026-07-16T00:00:00Z",
                )

        self.assertEqual(list(registry.status_directories["open"].glob("*.json")), [])
        self.assertEqual(index_path.read_text(encoding="utf-8"), old_index)
        recovered = IssueRegistry(self.root)
        self.assertEqual(list(recovered.status_directories["open"].glob("*.json")), [])

    def test_existing_issue_index_failure_restores_issue_and_index_exactly(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        issue_path = self.issue_path(self.root, issue_id)
        index_path = self.root / "issues" / "index.json"
        old_issue = issue_path.read_text(encoding="utf-8")
        old_index = index_path.read_text(encoding="utf-8")
        real_replace = os.replace
        failed = False

        def fail_first_index_replace(source: object, destination: object) -> None:
            nonlocal failed
            if Path(destination) == index_path and not failed:
                failed = True
                raise OSError("simulated existing issue index failure")
            real_replace(source, destination)

        with mock.patch.object(
            issue_registry.os,
            "replace",
            side_effect=fail_first_index_replace,
        ):
            with self.assertRaisesRegex(OSError, "existing issue index failure"):
                self.ingest_one(
                    registry,
                    observed_at="2026-07-17T00:00:00Z",
                )

        self.assertEqual(issue_path.read_text(encoding="utf-8"), old_issue)
        self.assertEqual(index_path.read_text(encoding="utf-8"), old_index)
        recovered = IssueRegistry(self.root)
        self.assertEqual(recovered.load(issue_id)["occurrences"], 1)

    def test_set_status_source_unlink_failure_restores_original_state(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        source = self.issue_path(self.root, issue_id, "open")
        destination = self.issue_path(self.root, issue_id, "verifying")
        index_path = self.root / "issues" / "index.json"
        old_issue = source.read_text(encoding="utf-8")
        old_index = index_path.read_text(encoding="utf-8")
        path_type = type(source)
        real_unlink = path_type.unlink
        real_mark_for_deletion = getattr(
            issue_registry,
            "_windows_mark_handle_for_deletion",
            None,
        )
        failed = False

        def fail_source_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == source:
                raise PermissionError("simulated source unlink failure")
            real_unlink(path, *args, **kwargs)

        def fail_first_disposition(handle: int) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise PermissionError("simulated source unlink failure")
            if real_mark_for_deletion is None:
                self.fail("Windows handle deletion helper is unavailable")
            real_mark_for_deletion(handle)

        unlink_failure = (
            mock.patch.object(
                issue_registry,
                "_windows_mark_handle_for_deletion",
                side_effect=fail_first_disposition,
            )
            if os.name == "nt"
            else mock.patch.object(path_type, "unlink", new=fail_source_unlink)
        )
        with unlink_failure:
            with self.assertRaisesRegex(PermissionError, "source unlink failure"):
                registry.set_status(issue_id, "verifying")

        self.assertEqual(source.read_text(encoding="utf-8"), old_issue)
        self.assertFalse(destination.exists())
        self.assertEqual(index_path.read_text(encoding="utf-8"), old_index)
        recovered = IssueRegistry(self.root)
        self.assertEqual(recovered.load(issue_id)["status"], "open")

    @unittest.skipUnless(os.name == "nt", "Windows handle deletion race")
    def test_set_status_rejects_source_parent_swap_before_unlink(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        source = self.issue_path(self.root, issue_id, "open")
        destination = self.issue_path(self.root, issue_id, "verifying")
        source_directory = source.parent
        original_directory = source_directory.with_name("open-original")
        outside = self.root.parent / "outside-open-swap"
        outside.mkdir()
        outside_source = outside / source.name
        outside_bytes = b"outside source must remain unchanged"
        outside_source.write_bytes(outside_bytes)
        old_issue = source.read_bytes()
        old_index = registry.index_path.read_bytes()
        path_type = type(source)
        real_unlink = path_type.unlink
        real_mark_for_deletion = getattr(
            issue_registry,
            "_windows_mark_handle_for_deletion",
            None,
        )
        swapped = False

        def swap_source_parent() -> None:
            nonlocal swapped
            source_directory.rename(original_directory)
            if not self.make_directory_link(source_directory, outside):
                original_directory.rename(source_directory)
                self.skipTest("directory links are unavailable")
            swapped = True

        def unlink_after_check(path: Path, *args: object, **kwargs: object) -> None:
            if path == source and not swapped:
                swap_source_parent()
            real_unlink(path, *args, **kwargs)

        def mark_after_check(handle: int) -> None:
            if not swapped:
                swap_source_parent()
            if real_mark_for_deletion is None:
                self.fail("Windows handle deletion helper is unavailable")
            real_mark_for_deletion(handle)

        try:
            with (
                mock.patch.object(
                    path_type,
                    "unlink",
                    new=unlink_after_check,
                ),
                mock.patch.object(
                    issue_registry,
                    "_windows_mark_handle_for_deletion",
                    side_effect=mark_after_check,
                    create=True,
                ),
                self.assertRaisesRegex(ValueError, "changed|link|reparse"),
            ):
                registry.set_status(issue_id, "verifying")
        finally:
            if swapped:
                if source_directory.is_symlink():
                    source_directory.unlink()
                else:
                    os.rmdir(source_directory)
                original_directory.rename(source_directory)

        self.assertEqual(outside_source.read_bytes(), outside_bytes)
        recovered = IssueRegistry(self.root)
        self.assertEqual(source.read_bytes(), old_issue)
        self.assertFalse(destination.exists())
        self.assertEqual(recovered.index_path.read_bytes(), old_index)
        self.assertEqual(recovered.load(issue_id)["status"], "open")

    def test_reopen_source_unlink_failure_restores_resolved_issue(self) -> None:
        registry = IssueRegistry(self.root)
        failure = self.failure()
        issue_id = self.ingest_one(registry, failure)
        registry.set_status(issue_id, "verifying")
        registry.set_status(
            issue_id,
            "resolved",
            verification_cycle=self.completed_cycle(),
        )
        source = self.issue_path(self.root, issue_id, "resolved")
        destination = self.issue_path(self.root, issue_id, "open")
        index_path = self.root / "issues" / "index.json"
        old_issue = source.read_text(encoding="utf-8")
        old_index = index_path.read_text(encoding="utf-8")
        path_type = type(source)
        real_unlink = path_type.unlink
        real_mark_for_deletion = getattr(
            issue_registry,
            "_windows_mark_handle_for_deletion",
            None,
        )
        failed = False

        def fail_source_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path == source:
                raise PermissionError("simulated reopen unlink failure")
            real_unlink(path, *args, **kwargs)

        def fail_first_disposition(handle: int) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise PermissionError("simulated reopen unlink failure")
            if real_mark_for_deletion is None:
                self.fail("Windows handle deletion helper is unavailable")
            real_mark_for_deletion(handle)

        unlink_failure = (
            mock.patch.object(
                issue_registry,
                "_windows_mark_handle_for_deletion",
                side_effect=fail_first_disposition,
            )
            if os.name == "nt"
            else mock.patch.object(path_type, "unlink", new=fail_source_unlink)
        )
        with unlink_failure:
            with self.assertRaisesRegex(PermissionError, "reopen unlink failure"):
                registry.ingest(
                    self.report(failure),
                    {failure.scenario_id: self.context()},
                    observed_at="2026-07-17T00:00:00Z",
                )

        self.assertEqual(source.read_text(encoding="utf-8"), old_issue)
        self.assertFalse(destination.exists())
        self.assertEqual(index_path.read_text(encoding="utf-8"), old_index)
        recovered = IssueRegistry(self.root)
        issue = recovered.load(issue_id)
        self.assertEqual(issue["status"], "resolved")
        self.assertEqual(issue["occurrences"], 1)

    def test_reopen_rejects_source_parent_swap_before_unlink(self) -> None:
        registry = IssueRegistry(self.root)
        failure = self.failure()
        issue_id = self.ingest_one(registry, failure)
        registry.set_status(issue_id, "verifying")
        registry.set_status(
            issue_id,
            "resolved",
            verification_cycle=self.completed_cycle(),
        )
        source = self.issue_path(self.root, issue_id, "resolved")
        destination = self.issue_path(self.root, issue_id, "open")
        source_directory = source.parent
        original_directory = source_directory.with_name("resolved-original")
        outside = self.root.parent / "outside-resolved-swap"
        outside.mkdir()
        outside_source = outside / source.name
        outside_bytes = b"outside resolved source must remain unchanged"
        outside_source.write_bytes(outside_bytes)
        old_issue = source.read_bytes()
        old_index = registry.index_path.read_bytes()
        real_atomic_write = issue_registry._atomic_write_json
        swapped = False

        def write_then_swap(path: Path, value: object) -> None:
            nonlocal swapped
            real_atomic_write(path, value)
            if path == destination and not swapped:
                source_directory.rename(original_directory)
                if not self.make_directory_link(source_directory, outside):
                    original_directory.rename(source_directory)
                    self.skipTest("directory links are unavailable")
                swapped = True

        try:
            with (
                mock.patch.object(
                    issue_registry,
                    "_atomic_write_json",
                    side_effect=write_then_swap,
                ),
                self.assertRaisesRegex(ValueError, "changed|link|reparse"),
            ):
                registry.ingest(
                    self.report(failure),
                    {failure.scenario_id: self.context()},
                    observed_at="2026-07-17T00:00:00Z",
                )
        finally:
            if swapped:
                if source_directory.is_symlink():
                    source_directory.unlink()
                else:
                    os.rmdir(source_directory)
                original_directory.rename(source_directory)

        self.assertEqual(outside_source.read_bytes(), outside_bytes)
        recovered = IssueRegistry(self.root)
        self.assertEqual(source.read_bytes(), old_issue)
        self.assertFalse(destination.exists())
        self.assertEqual(recovered.index_path.read_bytes(), old_index)
        issue = recovered.load(issue_id)
        self.assertEqual(issue["status"], "resolved")
        self.assertEqual(issue["occurrences"], 1)

    def test_constructor_recovers_journal_with_source_and_destination_duplicates(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        source = self.issue_path(self.root, issue_id, "open")
        destination = self.issue_path(self.root, issue_id, "verifying")
        index_path = self.root / "issues" / "index.json"
        issue = registry.load(issue_id)
        moved = dict(issue)
        moved["status"] = "verifying"

        with registry._locked():
            registry._begin_transaction((source, destination, index_path))
            issue_registry._atomic_write_json(destination, moved)

        self.assertTrue(source.is_file())
        self.assertTrue(destination.is_file())
        old_index = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertEqual(old_index["issues"][issue_id]["status"], "open")

        recovered = IssueRegistry(self.root)

        self.assertEqual(recovered.load(issue_id)["status"], "open")
        self.assertTrue(source.is_file())
        self.assertFalse(destination.exists())
        self.assertFalse(recovered._journal_path.exists())

    def test_transaction_snapshot_rejects_source_file_identity_change(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        source = self.issue_path(self.root, issue_id, "open")
        destination = self.issue_path(self.root, issue_id, "verifying")
        original = source.with_name(f"{source.stem}-original.json")
        source_bytes = source.read_bytes()
        real_read_snapshot = registry._read_transaction_snapshot
        swapped = False

        def read_then_replace(relative_path: str, path: Path):
            nonlocal swapped
            snapshot = real_read_snapshot(relative_path, path)
            if path == source and not swapped:
                source.rename(original)
                source.write_bytes(source_bytes)
                swapped = True
            return snapshot

        try:
            with (
                registry._locked(),
                mock.patch.object(
                    registry,
                    "_read_transaction_snapshot",
                    side_effect=read_then_replace,
                ),
                self.assertRaisesRegex(ValueError, "changed"),
            ):
                registry._begin_transaction(
                    (registry.index_path, source, destination)
                )
        finally:
            if swapped:
                source.unlink()
                original.rename(source)
            if registry._journal_path.exists():
                registry._journal_path.unlink()

        self.assertEqual(source.read_bytes(), source_bytes)

    def test_journal_null_index_before_is_rejected_without_modifying_files(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        issue_path = self.issue_path(self.root, issue_id)
        index_path = self.root / "issues" / "index.json"
        old_issue = issue_path.read_bytes()
        old_index = index_path.read_bytes()
        issue_registry._atomic_write_json(
            registry._journal_path,
            {
                "schema_version": 1,
                "files": [
                    {"path": "index.json", "before": None},
                    {"path": f"open/{issue_id}.json", "before": None},
                ],
            },
        )

        with self.assertRaisesRegex(ValueError, "transaction journal"):
            IssueRegistry(self.root)

        self.assertEqual(index_path.read_bytes(), old_index)
        self.assertEqual(issue_path.read_bytes(), old_issue)
        self.assertTrue(registry._journal_path.is_file())

    def test_journal_index_reference_requires_non_null_issue_before(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        issue_path = self.issue_path(self.root, issue_id)
        index_path = self.root / "issues" / "index.json"
        old_issue = issue_path.read_bytes()
        old_index = index_path.read_bytes()
        index = json.loads(old_index)
        issue_registry._atomic_write_json(
            registry._journal_path,
            {
                "schema_version": 1,
                "files": [
                    {"path": "index.json", "before": index},
                    {"path": f"open/{issue_id}.json", "before": None},
                ],
            },
        )

        with self.assertRaisesRegex(ValueError, "transaction journal"):
            IssueRegistry(self.root)

        self.assertEqual(index_path.read_bytes(), old_index)
        self.assertEqual(issue_path.read_bytes(), old_issue)
        self.assertTrue(registry._journal_path.is_file())

    def test_journal_non_null_issue_before_must_be_referenced_by_old_index(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        issue_path = self.issue_path(self.root, issue_id)
        index_path = self.root / "issues" / "index.json"
        old_issue = issue_path.read_bytes()
        old_index = index_path.read_bytes()
        issue = json.loads(old_issue)
        index = json.loads(old_index)
        index["issues"].pop(issue_id)
        issue_registry._atomic_write_json(
            registry._journal_path,
            {
                "schema_version": 1,
                "files": [
                    {"path": "index.json", "before": index},
                    {"path": f"open/{issue_id}.json", "before": issue},
                ],
            },
        )

        with self.assertRaisesRegex(ValueError, "transaction journal"):
            IssueRegistry(self.root)

        self.assertEqual(index_path.read_bytes(), old_index)
        self.assertEqual(issue_path.read_bytes(), old_issue)
        self.assertTrue(registry._journal_path.is_file())

    def test_journal_issue_status_path_must_match_old_index(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        source = self.issue_path(self.root, issue_id, "open")
        destination = self.issue_path(self.root, issue_id, "verifying")
        index_path = self.root / "issues" / "index.json"
        old_issue = source.read_bytes()
        old_index = index_path.read_bytes()
        moved = json.loads(old_issue)
        moved["status"] = "verifying"
        issue_registry._atomic_write_json(
            registry._journal_path,
            {
                "schema_version": 1,
                "files": [
                    {"path": "index.json", "before": json.loads(old_index)},
                    {"path": f"verifying/{issue_id}.json", "before": moved},
                ],
            },
        )

        with self.assertRaisesRegex(ValueError, "transaction journal"):
            IssueRegistry(self.root)

        self.assertEqual(index_path.read_bytes(), old_index)
        self.assertEqual(source.read_bytes(), old_issue)
        self.assertFalse(destination.exists())
        self.assertTrue(registry._journal_path.is_file())

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

    def test_release_error_is_ignored_when_close_releases_descriptor(self) -> None:
        registry = IssueRegistry(self.root)
        second = IssueRegistry(self.root)
        opened: list[int] = []
        real_open = registry._open_lock_descriptor

        def capture_open() -> int:
            descriptor = real_open()
            opened.append(descriptor)
            return descriptor

        def fail_release(descriptor: int) -> None:
            raise OSError(errno.EIO, "simulated release failure")

        with (
            mock.patch.object(registry, "_open_lock_descriptor", side_effect=capture_open),
            mock.patch.object(
                registry,
                "_release_advisory_lock",
                side_effect=fail_release,
            ),
            mock.patch.object(issue_registry.os, "close", wraps=os.close) as close,
        ):
            with registry._locked():
                pass

        descriptor = opened[-1]
        try:
            close.assert_any_call(descriptor)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
            with second._locked():
                pass
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def test_release_error_with_close_failure_raises_close_error(self) -> None:
        registry = IssueRegistry(self.root)
        opened: list[int] = []
        real_open = registry._open_lock_descriptor
        real_close = os.close

        def capture_open() -> int:
            descriptor = real_open()
            opened.append(descriptor)
            return descriptor

        def fail_release(descriptor: int) -> None:
            raise OSError(errno.EIO, "simulated release failure")

        def close_then_fail(descriptor: int) -> None:
            real_close(descriptor)
            raise OSError(errno.EIO, "simulated close failure")

        with (
            mock.patch.object(registry, "_open_lock_descriptor", side_effect=capture_open),
            mock.patch.object(
                registry,
                "_release_advisory_lock",
                side_effect=fail_release,
            ),
            mock.patch.object(issue_registry.os, "close", side_effect=close_then_fail),
            self.assertRaisesRegex(OSError, "close failure"),
        ):
            with registry._locked():
                pass

        with self.assertRaises(OSError):
            os.fstat(opened[-1])

    def test_release_error_does_not_mask_business_error_and_still_closes(self) -> None:
        registry = IssueRegistry(self.root)
        opened: list[int] = []
        real_open = registry._open_lock_descriptor
        real_release = registry._release_advisory_lock
        business_error = RuntimeError("business failure")

        def capture_open() -> int:
            descriptor = real_open()
            opened.append(descriptor)
            return descriptor

        def release_then_fail(descriptor: int) -> None:
            real_release(descriptor)
            raise OSError(errno.EIO, "simulated release failure")

        with (
            mock.patch.object(registry, "_open_lock_descriptor", side_effect=capture_open),
            mock.patch.object(
                registry,
                "_release_advisory_lock",
                side_effect=release_then_fail,
            ),
            self.assertRaises(RuntimeError) as raised,
        ):
            with registry._locked():
                raise business_error

        descriptor = opened[-1]
        self.assertIs(raised.exception, business_error)
        with self.assertRaises(OSError):
            os.fstat(descriptor)

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
            {"status": "completed", "mode": "daily", "issue_ids": "not-an-array"},
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

        deep_cycle = self.completed_cycle(mode="deep")
        resolved = registry.set_status(
            issue_id,
            "resolved",
            verification_cycle=deep_cycle,
        )
        self.assertEqual(resolved["status"], "resolved")
        reopened = registry.set_status(issue_id, "open")
        self.assertEqual(reopened["status"], "open")

        with self.assertRaises(ValueError):
            registry.set_status(issue_id, "open")
        with self.assertRaises(ValueError):
            registry.set_status(issue_id, "unknown")

    def test_resolution_rejects_self_consistent_zero_round_cycle_directly(self) -> None:
        registry = IssueRegistry(self.root)
        issue_id = self.ingest_one(registry)
        registry.set_status(issue_id, "verifying")
        cycle = self.completed_cycle()
        cycle.update(
            {
                "target_rounds": 0,
                "completed_rounds": 0,
                "rounds": [],
            }
        )

        with self.assertRaisesRegex(ValueError, "positive"):
            registry.set_status(
                issue_id,
                "resolved",
                verification_cycle=cycle,
            )
        self.assertEqual(registry.load(issue_id)["status"], "verifying")


if __name__ == "__main__":
    unittest.main()
