from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import get_dialog_cases, get_recipes, get_users, recommend


def main():
    recipes = get_recipes()
    users = get_users()
    cases = get_dialog_cases()
    assert len(recipes) == 2000, f"expected 2000 recipes, got {len(recipes)}"
    assert len(users) == 50, f"expected 50 users, got {len(users)}"
    assert cases, "dialog cases should not be empty"

    for case in cases:
        result = recommend(1, case["user_messages"])
        assert result["menu"], f"case {case['id']} returned empty menu"
        assert result["score_card"]["official_recipe"] is True
        assert result["score_card"]["allergy_passed"] is True
        assert isinstance(result["answer"], str) and result["answer"]

    allergy_result = recommend(1, ["今晚想吃海鲜，越辣越好"])
    names = " ".join(item["name"] + item["ingredients"] for item in allergy_result["menu"])
    assert "海鲜" not in names, "allergy check should avoid seafood for user 1"

    multi_turn = recommend(3, ["中午这顿饭你帮我安排一下。", "两个人吃，最近在减脂。"])
    assert multi_turn["constraints"]["meal"] == "午餐"
    assert multi_turn["constraints"]["people_count"] == 2
    assert "减脂" in multi_turn["constraints"]["health_goals"]

    print(f"ok: {len(recipes)} recipes, {len(users)} users, {len(cases)} dialog cases")


if __name__ == "__main__":
    main()
