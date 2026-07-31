from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.audit_jobs import AuditJobManager
from app.audit_runner import run_audit
from app.scenario_agents import agent_candidates_to_audit_scenarios, generate_reviewed_candidates


PASSING_SCENARIO = {
    "name": "硬约束-花生过敏",
    "user_id": None,
    "messages": ["我对花生过敏，今晚想吃高蛋白，帮我推荐3道菜"],
    "forbid": ["花生"],
    "expect_count": 3,
}

FAILING_SCENARIO = {
    "name": "故意失败-数量不符",
    "user_id": None,
    "messages": ["推荐2道晚饭"],
    "expect_count": 5,
}


def test_run_audit_reports_pass_and_failure_details():
    report = run_audit([PASSING_SCENARIO, FAILING_SCENARIO])

    assert report["summary"]["total"] == 2
    assert report["summary"]["passed"] == 1
    assert report["summary"]["failed"] == 1
    assert report["summary"]["duration_ms"] >= 0

    passing = report["records"][0]
    assert passing["name"] == "硬约束-花生过敏"
    assert passing["status"] == "passed"
    assert passing["messages"] == PASSING_SCENARIO["messages"]
    assert passing["answer"]
    assert len(passing["menu"]) == 3
    assert passing["issues"] == []
    assert passing["elapsed_ms"] >= 0
    assert passing["result"]["score_card"]["official_recipe"] is True

    failing = report["records"][1]
    assert failing["status"] == "failed"
    assert any("数量不符" in issue for issue in failing["issues"])
    assert failing["debug"]["expected_count"] == 5
    assert failing["debug"]["actual_count"] == len(failing["menu"])


def test_run_audit_includes_nutrition_evaluation_details():
    scenario = {
        **PASSING_SCENARIO,
        "structured_ground_truth": {
            "nutrition_targets": {
                "min_balance_level": "high",
                "min_confidence_level": "high",
                "max_sodium_mg_per_person": 1,
            }
        },
    }

    report = run_audit([scenario])
    record = report["records"][0]

    assert "nutrition_evaluation" in record["debug"]
    evaluation = record["debug"]["nutrition_evaluation"]
    assert evaluation["actual_balance_level"] in {"high", "medium", "low"}
    assert evaluation["actual_confidence_level"] in {"high", "medium", "low"}
    assert "per_person" in evaluation
    assert any("nutrition" in issue for issue in record["issues"])


def test_agent_generated_nutrition_targets_are_advisory_not_blocking():
    scenario = {
        **PASSING_SCENARIO,
        "source": "agent_generated",
        "agent_review": {"review_agent": "local-deterministic", "naturalness": 4, "clarity": 5},
        "structured_ground_truth": {
            "nutrition_targets": {
                "min_balance_level": "high",
                "min_confidence_level": "high",
                "max_sodium_mg_per_person": 1,
            }
        },
    }

    report = run_audit([scenario])
    record = report["records"][0]

    assert record["status"] == "passed"
    assert record["issues"] == []
    assert "nutrition_evaluation" in record["debug"]
    assert any("nutrition" in item for item in record["debug"]["nutrition_advisories"])


def test_agent_generated_batch_reports_hard_constraint_passes_separately_from_advisories():
    scenarios = agent_candidates_to_audit_scenarios(generate_reviewed_candidates(seed=20260725, count=50))
    report = run_audit(scenarios)

    assert report["summary"]["total"] == 50
    assert report["summary"]["failed"] == 0
    assert report["summary"]["passed"] == 50
    assert any(record["debug"].get("nutrition_advisories") for record in report["records"])


def test_audit_job_manager_runs_background_job_to_completion():
    manager = AuditJobManager(max_jobs=4)
    job = manager.start_job([PASSING_SCENARIO, FAILING_SCENARIO])

    assert job["status"] in {"queued", "running", "completed"}
    assert job["progress"]["total"] == 2
    assert job["summary"]["total"] == 2

    deadline = time.time() + 10
    current = job
    while time.time() < deadline:
        current = manager.get_job(job["job_id"])
        if current["status"] == "completed":
            break
        time.sleep(0.05)

    assert current["status"] == "completed"
    assert current["progress"]["completed"] == 2
    assert current["summary"]["passed"] == 1
    assert current["summary"]["failed"] == 1
    assert len(current["records"]) == 2

    jobs = manager.list_jobs()
    assert jobs and jobs[0]["job_id"] == job["job_id"]
    assert "records" not in jobs[0]


def main():
    test_run_audit_reports_pass_and_failure_details()
    test_run_audit_includes_nutrition_evaluation_details()
    test_agent_generated_nutrition_targets_are_advisory_not_blocking()
    test_agent_generated_batch_reports_hard_constraint_passes_separately_from_advisories()
    test_audit_job_manager_runs_background_job_to_completion()
    print("ok: audit runner and background jobs")


if __name__ == "__main__":
    main()
