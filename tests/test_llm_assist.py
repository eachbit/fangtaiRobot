from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.constraints import extract_constraints
from app.llm_assist import _should_request_llm_assist, augment_constraints_with_llm


def test_llm_patch_fills_missing_people_and_dish_count_without_overriding_hard_rules():
    constraints = extract_constraints(["四大一小吃午饭，不吃鸡蛋，整桌来五个菜"])

    def fake_provider(messages):
        return {
            "people_count": 5,
            "requested_dish_count": 5,
            "allergens": ["花生"],
            "avoid_ingredients": ["鸡蛋", "香菜"],
            "health_goals": ["降压"],
            "unknown_field": "ignored",
        }

    augmented, meta = augment_constraints_with_llm(["四大一小吃午饭，不吃鸡蛋，整桌来五个菜"], constraints, fake_provider)

    assert meta["used"] is True
    assert augmented.people_count == 5
    assert augmented.requested_dish_count == 5
    assert "降压" in augmented.health_goals
    assert "香菜" in augmented.avoid_ingredients
    assert "鸡蛋" in augmented.avoid_ingredients
    assert "鸡蛋" not in augmented.allergens
    assert "花生" in augmented.allergens


def test_llm_patch_failure_keeps_rule_constraints():
    constraints = extract_constraints(["4个人吃午餐，推荐4道菜"])

    def failing_provider(messages):
        raise TimeoutError("simulated timeout")

    augmented, meta = augment_constraints_with_llm(["4个人吃午餐，推荐4道菜"], constraints, failing_provider)

    assert meta["used"] is False
    assert meta["error"] == "TimeoutError"
    assert augmented.people_count == 4
    assert augmented.requested_dish_count == 4


def test_llm_patch_cannot_upgrade_local_dislike_to_allergy():
    constraints = extract_constraints(["我不吃鸡蛋，午餐推荐4道菜"])

    def confused_provider(messages):
        return {
            "allergens": ["鸡蛋"],
            "avoid_ingredients": ["鸡蛋"],
        }

    augmented, _meta = augment_constraints_with_llm(["我不吃鸡蛋，午餐推荐4道菜"], constraints, confused_provider)

    assert "鸡蛋" in augmented.avoid_ingredients
    assert "鸡蛋" not in augmented.allergens


def test_llm_assist_skips_complete_low_uncertainty_requests():
    constraints = extract_constraints(["4个人吃午餐，先推荐4道菜。", "我不吃鸡蛋，其他菜尽量别动。"])

    assert _should_request_llm_assist(
        ["4个人吃午餐，先推荐4道菜。", "我不吃鸡蛋，其他菜尽量别动。"],
        constraints,
    ) is False


def test_llm_assist_keeps_complex_people_expression_enabled():
    constraints = extract_constraints(["两位成年人加一个孩子吃午饭，整桌五道菜，不吃香菜"])

    assert _should_request_llm_assist(["两位成年人加一个孩子吃午饭，整桌五道菜，不吃香菜"], constraints) is True


def main():
    test_llm_patch_fills_missing_people_and_dish_count_without_overriding_hard_rules()
    test_llm_patch_failure_keeps_rule_constraints()
    test_llm_patch_cannot_upgrade_local_dislike_to_allergy()
    test_llm_assist_skips_complete_low_uncertainty_requests()
    test_llm_assist_keeps_complex_people_expression_enabled()
    print("ok: llm assist")


if __name__ == "__main__":
    main()
