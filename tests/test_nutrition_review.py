from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import Constraints
from app.nutrition_review import _nutrition_timeout, build_nutrition_review


def _sample_nutrition():
    return {
        "table": {
            "dish_count": 4,
            "people_count": 4,
            "totals": {
                "kcal": 2400,
                "protein_g": 96,
                "fat_g": 120,
                "carbohydrate_g": 280,
                "fiber_g": 16,
                "sugar_g": 42,
                "sodium_mg": 3800,
            },
        },
        "per_person": {
            "kcal": 600,
            "protein_g": 24,
            "fat_g": 30,
            "carbohydrate_g": 70,
            "fiber_g": 4,
            "sugar_g": 10.5,
            "sodium_mg": 950,
        },
        "balance_level": "medium",
        "confidence": {
            "level": "medium",
            "coverage_ratio": 0.72,
            "missing_ingredients": ["葱姜"],
        },
    }


def _sample_menu():
    return [
        {"id": 1, "name": "蒸米饭", "ingredients": "粳米300克"},
        {"id": 2, "name": "瓦罐牛肉汤", "ingredients": "牛肉300克，盐5克"},
    ]


def test_local_nutrition_review_flags_health_goal_risks_without_llm():
    constraints = Constraints(meal="午餐", people_count=4, health_goals=["降压"])

    review = build_nutrition_review(_sample_menu(), _sample_nutrition(), constraints, enabled=False)

    assert review["llm_assist"]["enabled"] is False
    assert review["llm_assist"]["used"] is False
    assert "sodium_high" in review["risk_flags"]
    assert review["confidence"] == "medium"
    assert "950" in review["summary"]
    assert review["source"] == "local"


def test_llm_nutrition_review_can_enhance_text_but_not_override_numbers_or_schema():
    constraints = Constraints(meal="午餐", people_count=4, health_goals=["降压"])

    def fake_provider(payload):
        assert payload["nutrition"]["per_person"]["sodium_mg"] == 950
        return {
            "summary": "这桌人均钠约950mg，降压目标下建议少盐少酱油。",
            "risk_flags": ["sodium_high", "not_allowed"],
            "suggestions": ["保留本地菜单，烹饪时减少盐和酱油。"],
            "confidence": "high",
            "per_person": {"sodium_mg": 1},
            "new_menu": ["不存在的菜"],
        }

    review = build_nutrition_review(
        _sample_menu(),
        _sample_nutrition(),
        constraints,
        provider=fake_provider,
        enabled=True,
    )

    assert review["llm_assist"]["enabled"] is True
    assert review["llm_assist"]["used"] is True
    assert review["source"] == "llm_assisted"
    assert review["summary"] == "这桌人均钠约950mg，降压目标下建议少盐少酱油。"
    assert review["risk_flags"] == ["sodium_high"]
    assert "per_person" not in review
    assert "new_menu" not in review


def test_llm_nutrition_review_failure_keeps_local_review():
    constraints = Constraints(meal="午餐", people_count=4, health_goals=["降压"])

    def failing_provider(payload):
        raise TimeoutError("simulated")

    review = build_nutrition_review(
        _sample_menu(),
        _sample_nutrition(),
        constraints,
        provider=failing_provider,
        enabled=True,
    )

    assert review["llm_assist"]["enabled"] is True
    assert review["llm_assist"]["used"] is False
    assert review["llm_assist"]["error"] == "TimeoutError"
    assert review["source"] == "local"
    assert "sodium_high" in review["risk_flags"]


def test_llm_nutrition_review_only_marks_fields_returned_by_model():
    constraints = Constraints(meal="午餐", people_count=4, health_goals=["降压"])

    def summary_only_provider(payload):
        return {"summary": "人均钠偏高，建议少盐。"}

    review = build_nutrition_review(
        _sample_menu(),
        _sample_nutrition(),
        constraints,
        provider=summary_only_provider,
        enabled=True,
    )

    assert review["llm_assist"]["applied_fields"] == ["summary"]


def test_llm_nutrition_review_skips_external_call_for_low_risk_menu():
    constraints = Constraints(meal="午餐", people_count=4)
    nutrition = _sample_nutrition()
    nutrition["balance_level"] = "high"
    nutrition["per_person"]["sodium_mg"] = 700
    nutrition["per_person"]["fiber_g"] = 6

    def unexpected_provider(payload):
        raise AssertionError("provider should not be called")

    review = build_nutrition_review(
        _sample_menu(),
        nutrition,
        constraints,
        provider=unexpected_provider,
        enabled=True,
    )

    assert review["source"] == "local"
    assert review["llm_assist"]["enabled"] is True
    assert review["llm_assist"]["used"] is False
    assert review["llm_assist"]["skipped"] == "low_risk"


def test_nutrition_review_timeout_can_use_dedicated_env_var():
    import os

    old_general = os.environ.get("FANGTAI_LLM_TIMEOUT")
    old_nutrition = os.environ.get("FANGTAI_LLM_NUTRITION_TIMEOUT")
    os.environ["FANGTAI_LLM_TIMEOUT"] = "2.5"
    os.environ["FANGTAI_LLM_NUTRITION_TIMEOUT"] = "6"
    try:
        assert _nutrition_timeout() == 6.0
    finally:
        if old_general is None:
            os.environ.pop("FANGTAI_LLM_TIMEOUT", None)
        else:
            os.environ["FANGTAI_LLM_TIMEOUT"] = old_general
        if old_nutrition is None:
            os.environ.pop("FANGTAI_LLM_NUTRITION_TIMEOUT", None)
        else:
            os.environ["FANGTAI_LLM_NUTRITION_TIMEOUT"] = old_nutrition


def main():
    test_local_nutrition_review_flags_health_goal_risks_without_llm()
    test_llm_nutrition_review_can_enhance_text_but_not_override_numbers_or_schema()
    test_llm_nutrition_review_failure_keeps_local_review()
    test_llm_nutrition_review_only_marks_fields_returned_by_model()
    test_llm_nutrition_review_skips_external_call_for_low_risk_menu()
    test_nutrition_review_timeout_can_use_dedicated_env_var()
    print("ok: nutrition review")


if __name__ == "__main__":
    main()
