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

    session_first = recommend(1, ["我想吃高蛋白，推荐3道菜"])
    session_id = session_first["session_id"]
    assert session_id, "recommend should return a session id for multi-turn revisions"

    session_second = recommend(1, ["我不吃虾，其他尽量别动"], session_id=session_id)
    assert session_second["session_id"] == session_id
    assert len(session_second["menu"]) == len(session_first["menu"])
    first_names = [item["name"] for item in session_first["menu"]]
    second_names = [item["name"] for item in session_second["menu"]]
    shared = len(set(first_names) & set(second_names))
    assert shared >= len(first_names) - 1, "minimal revision should keep most of the prior menu"
    assert not any("虾" in (item["name"] + item["ingredients"]) for item in session_second["menu"])

    necessary_multi_replace_first = recommend(None, ["4个人吃晚餐，先推荐6道菜。 同时兼顾增肌。"])
    necessary_multi_replace_second = recommend(
        None,
        ["我不吃虾，其他菜尽量别动。"],
        session_id=necessary_multi_replace_first["session_id"],
    )
    assert necessary_multi_replace_second["changes"]["mode"] == "minimal_revision"
    assert necessary_multi_replace_second["changes"]["change_count"] > 1
    assert necessary_multi_replace_second["changes"]["kept_dishes"]
    assert necessary_multi_replace_second["score_card"]["minimal_change"] is True

    egg_dislike = recommend(None, ["4个人吃午餐，先推荐4道菜。", "我不吃鸡蛋，其他菜尽量别动。"])
    assert "鸡蛋" in egg_dislike["constraints"]["avoid_ingredients"]
    assert "鸡蛋" not in egg_dislike["constraints"]["allergens"]
    assert "已避开过敏食材" not in egg_dislike["answer"]
    assert "已避开忌口食材" in egg_dislike["answer"]

    egg_taboo = recommend(None, ["4个人吃午餐，先推荐4道菜。", "我忌口鸡蛋，其他菜尽量别动。"])
    assert "鸡蛋" in egg_taboo["constraints"]["avoid_ingredients"]
    assert "鸡蛋" not in egg_taboo["constraints"]["allergens"]

    egg_allergy = recommend(None, ["4个人吃午餐，先推荐4道菜。", "我对鸡蛋过敏，其他菜尽量别动。"])
    assert "鸡蛋" in egg_allergy["constraints"]["allergens"]

    nutrition_result = recommend(None, ["四个人晚饭，推荐4道菜"])
    nutrition = nutrition_result["nutrition"]
    assert nutrition["table"]["dish_count"] == len(nutrition_result["menu"])
    assert nutrition["table"]["people_count"] == 4
    assert nutrition["table"]["totals"]["kcal"] > 0
    assert nutrition["per_person"]["kcal"] == round(nutrition["table"]["totals"]["kcal"] / 4, 1)
    assert 0 <= nutrition["confidence"]["coverage_ratio"] <= 1
    assert nutrition_result["score_card"]["nutrition_balance"] in {"high", "medium", "low"}

    nutrition_targeted = recommend(None, ["我有高血压，也想减脂，推荐4道菜，尽量清淡一点"])
    targeted = nutrition_targeted["nutrition"]["per_person"]
    assert len(nutrition_targeted["menu"]) == 4
    assert "降压" in nutrition_targeted["constraints"]["health_goals"]
    assert "减脂" in nutrition_targeted["constraints"]["health_goals"]
    assert 350 <= targeted["kcal"] <= 900
    assert targeted["sodium_mg"] <= 800
    assert targeted["fat_g"] <= 35

    replay_messages = ["4个人吃午餐，推荐4道菜", "我不吃鸡蛋，其他菜尽量别动"]
    replayed = recommend(None, replay_messages)
    replay_first = recommend(None, [replay_messages[0]])
    replay_second = recommend(None, [replay_messages[1]], session_id=replay_first["session_id"])
    assert [item["id"] for item in replayed["menu"]] == [item["id"] for item in replay_second["menu"]]
    assert replayed["changes"] == replay_second["changes"]
    assert replayed["menu_version"] == 2
    assert replayed["messages"] == replay_messages
    assert [item["version"] for item in replayed["history"]] == [1, 2]

    rollback_first = recommend(None, ["4个人吃午餐，推荐4道菜"])
    rollback_second = recommend(
        None,
        ["我不吃鸡蛋，其他菜尽量别动"],
        session_id=rollback_first["session_id"],
    )
    rollback_result = recommend(
        None,
        [],
        session_id=rollback_second["session_id"],
        rollback_to=1,
    )
    assert rollback_result["changes"]["mode"] == "rollback"
    assert rollback_result["changes"]["source_version"] == 1
    assert rollback_result["menu_version"] == 3
    assert len(rollback_result["history"]) == 3

    rollback_next = recommend(
        None,
        ["我不吃虾，其他菜尽量别动"],
        session_id=rollback_result["session_id"],
    )
    assert rollback_next["menu_version"] == 4
    assert rollback_next["changes"]["mode"] == "minimal_revision"

    undo_first = recommend(None, ["推荐3道晚餐"])
    undo_second = recommend(
        None,
        ["我不吃虾，其他菜尽量别动"],
        session_id=undo_first["session_id"],
    )
    undo_result = recommend(
        None,
        ["撤销刚才修改"],
        session_id=undo_second["session_id"],
    )
    assert undo_result["changes"]["mode"] == "rollback"
    assert undo_result["changes"]["source_version"] == 1

    print(f"ok: {len(recipes)} recipes, {len(users)} users, {len(cases)} dialog cases")


if __name__ == "__main__":
    main()
