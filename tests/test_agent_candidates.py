from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.evaluation import agent_candidates
from tests.evaluation.agent_candidates import (
    AgentCandidate,
    save_unreviewed_candidate,
    validate_candidate,
)


def candidate_payload() -> dict[str, object]:
    return {
        "candidate_id": "negative-health-1",
        "health_bucket": "healthy",
        "messages": ["我没有高血压，推荐四道菜"],
        "structured_ground_truth": {"special_groups": []},
        "agent_review": {"naturalness": 4, "notes": "口语自然"},
    }


class AgentCandidateTests(unittest.TestCase):
    def test_agent_review_is_soft_and_cannot_override_ground_truth(self) -> None:
        payload = candidate_payload()
        payload.pop("health_bucket")
        payload["agent_review"] = {
            "naturalness": 4,
            "notes": "口语自然",
            "special_groups": ["高血压"],
            "structured_ground_truth": {"special_groups": ["高血压"]},
            "severity": "blocking",
        }

        value = validate_candidate(payload)

        self.assertEqual(value.structured_ground_truth["special_groups"], [])
        self.assertEqual(value.soft_review["special_groups"], ["高血压"])
        self.assertEqual(value.soft_review["severity"], "blocking")
        self.assertTrue(value.agent_review_is_soft)
        self.assertFalse(hasattr(value, "agent_review"))
        with self.assertRaises(FrozenInstanceError):
            value.candidate_id = "changed"  # type: ignore[misc]

    def test_review_and_ground_truth_do_not_share_input_objects(self) -> None:
        payload = candidate_payload()
        ground_truth = payload["structured_ground_truth"]
        review = payload["agent_review"]

        value = validate_candidate(payload)
        assert isinstance(ground_truth, dict)
        assert isinstance(review, dict)
        ground_truth["special_groups"] = ["高血压"]
        review["notes"] = "changed"

        self.assertEqual(value.structured_ground_truth["special_groups"], [])
        self.assertEqual(value.soft_review["notes"], "口语自然")

    def test_internal_ground_truth_and_review_reject_top_level_mutation(self) -> None:
        value = validate_candidate(candidate_payload())

        with self.assertRaises(TypeError):
            value._structured_ground_truth["special_groups"] = ()  # type: ignore[index]
        with self.assertRaises(TypeError):
            value._soft_review["notes"] = "changed"  # type: ignore[index]

    def test_internal_ground_truth_and_review_reject_nested_mutation(self) -> None:
        payload = candidate_payload()
        payload["structured_ground_truth"] = {
            "profile": {"special_groups": ["高血压"]}
        }
        payload["agent_review"] = {"details": {"scores": [4]}}
        value = validate_candidate(payload)

        ground_truth_profile = value._structured_ground_truth["profile"]
        review_details = value._soft_review["details"]
        assert isinstance(ground_truth_profile, Mapping)
        assert isinstance(review_details, Mapping)
        with self.assertRaises(TypeError):
            ground_truth_profile["special_groups"][0] = "糖尿病"  # type: ignore[index]
        with self.assertRaises(TypeError):
            review_details["scores"][0] = 1  # type: ignore[index]

    def test_candidate_exports_are_mutable_defensive_copies(self) -> None:
        payload = candidate_payload()
        payload["structured_ground_truth"] = {
            "profile": {"special_groups": ["高血压"]}
        }
        payload["agent_review"] = {"details": {"scores": [4]}}
        value = validate_candidate(payload)

        ground_truth = value.structured_ground_truth
        review = value.soft_review
        exported = value.to_dict()
        ground_truth["profile"]["special_groups"].append("糖尿病")  # type: ignore[index,union-attr]
        review["details"]["scores"].append(1)  # type: ignore[index,union-attr]
        exported["structured_ground_truth"]["profile"] = {}  # type: ignore[index]

        self.assertEqual(
            value.structured_ground_truth,
            {"profile": {"special_groups": ["高血压"]}},
        )
        self.assertEqual(value.soft_review, {"details": {"scores": [4]}})

    def test_known_health_buckets_are_accepted(self) -> None:
        for bucket in (
            "healthy",
            "single_condition",
            "multi_condition",
            "special_group",
            "high_risk",
        ):
            with self.subTest(bucket=bucket):
                payload = candidate_payload()
                payload["health_bucket"] = bucket
                self.assertEqual(validate_candidate(payload).health_bucket, bucket)

    def test_unknown_health_bucket_is_rejected(self) -> None:
        payload = candidate_payload()
        payload["health_bucket"] = "unknown"

        with self.assertRaisesRegex(
            ValueError, r"\$\.health_bucket: unknown value 'unknown'"
        ):
            validate_candidate(payload)

    def test_explicit_null_health_bucket_is_rejected(self) -> None:
        payload = candidate_payload()
        payload["health_bucket"] = None

        with self.assertRaisesRegex(
            ValueError, r"\$\.health_bucket: expected a string"
        ):
            validate_candidate(payload)

    def test_missing_structured_ground_truth_is_rejected(self) -> None:
        payload = candidate_payload()
        payload.pop("structured_ground_truth")

        with self.assertRaisesRegex(
            ValueError,
            r"\$\.structured_ground_truth: missing required field",
        ):
            validate_candidate(payload)

    def test_more_than_twelve_turns_is_rejected(self) -> None:
        payload = candidate_payload()
        payload["messages"] = ["继续"] * 13

        with self.assertRaisesRegex(ValueError, r"\$\.messages: at most 12 turns"):
            validate_candidate(payload)

    def test_turn_longer_than_five_hundred_characters_is_rejected(self) -> None:
        payload = candidate_payload()
        payload["messages"] = ["菜" * 501]

        with self.assertRaisesRegex(
            ValueError, r"\$\.messages\[0\]: at most 500 characters"
        ):
            validate_candidate(payload)

    def test_unknown_top_level_key_is_rejected(self) -> None:
        payload = candidate_payload()
        payload["merge_status"] = "approved"

        with self.assertRaisesRegex(
            ValueError, r"\$\.merge_status: unknown field"
        ):
            validate_candidate(payload)

    def test_non_json_arrays_and_non_finite_numbers_are_rejected(self) -> None:
        invalid_values = (
            (
                "messages tuple",
                {**candidate_payload(), "messages": ("推荐四道菜",)},
                r"\$\.messages: expected a JSON array",
            ),
            (
                "ground-truth tuple",
                {
                    **candidate_payload(),
                    "structured_ground_truth": {"special_groups": ()},
                },
                r"\$\.structured_ground_truth\.special_groups: expected a JSON array",
            ),
            (
                "review NaN",
                {
                    **candidate_payload(),
                    "agent_review": {"naturalness": math.nan},
                },
                r"\$\.agent_review\.naturalness: number must be finite",
            ),
        )
        for label, payload, message in invalid_values:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, message):
                    validate_candidate(payload)

    def test_safe_save_uses_only_ignored_candidate_subdirectory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)

            saved = save_unreviewed_candidate(
                candidate_payload(), repository_root=root
            )

            expected = (
                root
                / "artifacts"
                / "evaluation"
                / "candidates"
                / "negative-health-1.json"
            )
            self.assertEqual(saved, expected)
            stored = json.loads(saved.read_text(encoding="utf-8"))
            self.assertEqual(stored["structured_ground_truth"], {"special_groups": []})
            self.assertEqual(stored["agent_review"]["notes"], "口语自然")
            self.assertTrue(validate_candidate(stored).agent_review_is_soft)

    def test_candidate_id_cannot_escape_safe_save_directory(self) -> None:
        payload = candidate_payload()
        payload["candidate_id"] = "../../app/overwrite"

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(
                ValueError, r"\$\.candidate_id: expected a safe identifier"
            ):
                save_unreviewed_candidate(payload, repository_root=root)
            self.assertFalse((root / "app" / "overwrite.json").exists())

    def test_windows_reserved_name_stem_is_rejected_with_any_extension(self) -> None:
        for candidate_id in ("CON.txt", "NUL.foo", "COM1.log"):
            with self.subTest(candidate_id=candidate_id):
                payload = candidate_payload()
                payload["candidate_id"] = candidate_id

                with self.assertRaisesRegex(
                    ValueError, r"\$\.candidate_id: expected a safe identifier"
                ):
                    validate_candidate(payload)

    def test_preconstructed_candidate_cannot_bypass_safe_identifier(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(
                ValueError, r"\$\.candidate_id: expected a safe identifier"
            ):
                save_unreviewed_candidate(
                    AgentCandidate(
                        candidate_id="../escaped",
                        messages=("推荐四道菜",),
                        _structured_ground_truth={"special_groups": []},
                    ),
                    repository_root=root,
                )
            self.assertFalse(
                (root / "artifacts" / "evaluation" / "escaped.json").exists()
            )

    def test_malicious_candidate_subclass_cannot_bypass_validation(self) -> None:
        class MaliciousCandidate(AgentCandidate):
            def __post_init__(self) -> None:
                pass

        malicious = MaliciousCandidate(
            candidate_id="../../app/overwrite",
            messages=("推荐四道菜",),
            _structured_ground_truth={"special_groups": []},
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()

            with self.assertRaisesRegex(
                ValueError, r"\$\.candidate: expected an exact AgentCandidate"
            ):
                save_unreviewed_candidate(malicious, repository_root=root)

            self.assertFalse((root / "app" / "overwrite.json").exists())

    def test_candidate_directory_link_or_junction_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app"
            target.mkdir()
            evaluation = root / "artifacts" / "evaluation"
            evaluation.mkdir(parents=True)
            candidate_link = evaluation / "candidates"
            try:
                candidate_link.symlink_to(target, target_is_directory=True)
            except OSError as symlink_error:
                if os.name != "nt":
                    self.skipTest(f"directory symlink unavailable: {symlink_error}")
                junction = subprocess.run(
                    [
                        "cmd",
                        "/c",
                        "mklink",
                        "/J",
                        str(candidate_link),
                        str(target),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if junction.returncode != 0:
                    self.skipTest(
                        "directory symlink and junction are unavailable: "
                        f"{junction.stderr or junction.stdout}"
                    )

            with self.assertRaisesRegex(
                ValueError,
                r"candidate path must not contain links or reparse points",
            ):
                save_unreviewed_candidate(candidate_payload(), repository_root=root)

            self.assertFalse((target / "negative-health-1.json").exists())

    def test_default_repository_root_is_module_based_not_current_directory(self) -> None:
        expected_module_root = Path(agent_candidates.__file__).resolve().parents[2]
        self.assertEqual(agent_candidates._REPOSITORY_ROOT, expected_module_root)

        with TemporaryDirectory() as root_directory, TemporaryDirectory() as cwd:
            root = Path(root_directory)
            previous_cwd = Path.cwd()
            try:
                os.chdir(cwd)
                with patch.object(agent_candidates, "_REPOSITORY_ROOT", root):
                    saved = save_unreviewed_candidate(candidate_payload())
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(
                saved,
                root
                / "artifacts"
                / "evaluation"
                / "candidates"
                / "negative-health-1.json",
            )


class AgentPromptContractTests(unittest.TestCase):
    def test_each_agent_contract_emits_candidate_schema_json(self) -> None:
        document = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "evaluation"
            / "agent-prompts.md"
        ).read_text(encoding="utf-8")

        headings = (
            "## Customer Agent",
            "## Advanced-scenario Agent",
            "## Judge Agent",
            "## Red-team Agent",
        )
        for index, heading in enumerate(headings):
            with self.subTest(agent=heading):
                start = document.index(heading)
                end = (
                    document.find("\n## ", start + len(heading))
                    if index < len(headings) - 1
                    else len(document)
                )
                section = document[start:end]
                for field in (
                    '"candidate_id"',
                    '"messages"',
                    '"structured_ground_truth"',
                    '"agent_review"',
                ):
                    self.assertIn(field, section)
                example = re.search(r"```json\n(.*?)\n```", section, re.DOTALL)
                self.assertIsNotNone(example)
                assert example is not None
                value = validate_candidate(json.loads(example.group(1)))
                self.assertTrue(value.agent_review_is_soft)

    def test_prompts_forbid_agent_changes_to_authoritative_state(self) -> None:
        document = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "evaluation"
            / "agent-prompts.md"
        ).read_text(encoding="utf-8")

        for protected_term in (
            "app/",
            "硬约束期望",
            "known gaps",
            "合并状态",
        ):
            self.assertIn(protected_term, document)
        self.assertIn("只能接收已保存证据", document)
        self.assertIn("失败回归测试", document)
        self.assertIn("不能执行合并", document)
        self.assertIn("不能修改 holdout 期望", document)
        self.assertIn("不能添加生产网络调用", document)
        self.assertIn("不能把 blocking failure 标成 known gap", document)


if __name__ == "__main__":
    unittest.main()
