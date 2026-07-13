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

    inferred = recommend(
        None,
        [
            "我是男生，45岁，有高血压，对海鲜过敏。",
            "晚上想吃清淡一点，还想减脂。",
        ],
    )
    inferred_profile = inferred["constraints"]["inferred_profile"]
    assert inferred_profile["gender"] == "男"
    assert inferred_profile["age"] == 45
    assert "高血压" in inferred_profile["special_groups"]
    assert "海鲜" in inferred_profile["allergens"]
    assert "减脂" in inferred["constraints"]["health_goals"]
    inferred_names = " ".join(item["name"] + item["ingredients"] for item in inferred["menu"])
    assert "海鲜" not in inferred_names, "dialog-inferred allergy should avoid seafood"

    disliked_oyster = recommend(
        None,
        [
            "我是一名健身需求的大学生，我有高血压的病症，我喜欢肉类",
            "我要摄入高蛋白",
            "我不喜欢吃海蛎子",
        ],
    )
    disliked_text = " ".join(item["name"] + item["ingredients"] for item in disliked_oyster["menu"])
    blocked_terms = ["海蛎", "海蛎子", "牡蛎", "生蚝"]
    assert not any(term in disliked_text for term in blocked_terms), "explicit dislike should filter oyster aliases"
    assert "海蛎子" in disliked_oyster["constraints"]["avoid_ingredients"]
    assert "海蛎子" not in disliked_oyster["constraints"]["allergens"]

    four_dishes = recommend(
        None,
        [
            "我是一名健身需求的大学生，我有高血压的病症，我喜欢肉类",
            "我要摄入高蛋白",
            "我不喜欢吃海蛎子",
            "帮我推荐4道菜",
        ],
    )
    assert four_dishes["constraints"]["requested_dish_count"] == 4
    assert len(four_dishes["menu"]) == 4

    no_spicy = recommend(None, ["我不能吃辣，晚饭清淡一点，推荐3道菜"])
    spicy_text = " ".join(item["name"] + item["ingredients"] + " ".join(item["labels"]) for item in no_spicy["menu"])
    assert "辣" in no_spicy["constraints"]["avoid_tastes"]
    assert "辣" not in spicy_text and "重口味" not in spicy_text

    not_too_sweet = recommend(None, ["给我推荐个早餐吧，别太甜，最好十分钟左右就能弄好，推荐2道"])
    sweet_text = " ".join(item["name"] + item["ingredients"] + " ".join(item["labels"]) for item in not_too_sweet["menu"])
    assert "甜" in not_too_sweet["constraints"]["avoid_tastes"]
    assert "甜" not in sweet_text

    print(f"ok: {len(recipes)} recipes, {len(users)} users, {len(cases)} dialog cases")


if __name__ == "__main__":
    main()
