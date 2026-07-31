from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.scenario_agents import (
    CandidateReviewAgent,
    CustomerScenarioAgent,
    agent_candidates_to_audit_scenarios,
    generate_reviewed_candidates,
    validate_candidate,
)


def test_customer_and_review_agents_preserve_ground_truth():
    generator = CustomerScenarioAgent(seed=17)
    reviewer = CandidateReviewAgent()

    candidate = generator.generate(0)
    reviewed = reviewer.review(candidate)

    assert candidate["messages"]
    assert reviewed["messages"] == candidate["messages"]
    assert reviewed["structured_ground_truth"] == candidate["structured_ground_truth"]
    assert reviewed["agent_review"]["review_agent"] == "local-deterministic"
    assert reviewed["agent_review"]["naturalness"] in {3, 4, 5}

    validated = validate_candidate(reviewed)
    assert validated["candidate_id"] == reviewed["candidate_id"]
    assert validated["structured_ground_truth"] == candidate["structured_ground_truth"]


def test_reviewed_candidates_convert_to_auditable_scenarios_reproducibly():
    first = generate_reviewed_candidates(seed=23, count=12)
    second = generate_reviewed_candidates(seed=23, count=12)
    other = generate_reviewed_candidates(seed=24, count=12)

    assert first == second
    assert first != other

    scenarios = agent_candidates_to_audit_scenarios(first)
    assert len(scenarios) == 12
    assert all(item["source"] == "agent_generated" for item in scenarios)
    assert all(item["messages"] for item in scenarios)
    assert any(item.get("forbid") for item in scenarios)
    assert any((item.get("expect_count") or 0) >= 4 for item in scenarios)
    assert all("agent_review" in item for item in scenarios)
    assert any(item["structured_ground_truth"].get("nutrition_targets") for item in scenarios)
    assert all("nutrition_targets" in item["structured_ground_truth"] for item in scenarios)


def test_generated_health_goals_are_visible_in_dialog_text():
    candidates = generate_reviewed_candidates(seed=20260725, count=50)

    for candidate in candidates:
        joined = "".join(candidate["messages"])
        for goal in candidate["structured_ground_truth"].get("health_goals", []):
            assert goal in joined, f"{candidate['candidate_id']} hides health goal {goal}"


def test_generated_candidates_support_large_scale_batches():
    candidates = generate_reviewed_candidates(seed=20260725, count=250)

    assert len(candidates) == 250
    assert candidates[0]["candidate_id"] != candidates[-1]["candidate_id"]


def main():
    test_customer_and_review_agents_preserve_ground_truth()
    test_reviewed_candidates_convert_to_auditable_scenarios_reproducibly()
    test_generated_health_goals_are_visible_in_dialog_text()
    test_generated_candidates_support_large_scale_batches()
    print("ok: scenario generation and review agents")


if __name__ == "__main__":
    main()
