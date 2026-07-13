from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import recommend
from app.food_terms import contains_food_term, expand_terms
from app.recipe_features import classify_recipe


SCENARIOS = [
    {
        "name": "硬约束-花生过敏",
        "user_id": None,
        "messages": ["我对花生过敏，今晚想吃高蛋白，帮我推荐3道菜"],
        "forbid": ["花生"],
        "expect_count": 3,
    },
    {
        "name": "硬约束-不吃海蛎子",
        "user_id": None,
        "messages": ["我有高血压，想摄入高蛋白", "我不喜欢吃海蛎子", "推荐4道菜"],
        "forbid": ["海蛎子"],
        "expect_count": 4,
    },
    {
        "name": "硬约束-不吃辣",
        "user_id": None,
        "messages": ["我不能吃辣，晚饭清淡一点，推荐3道菜"],
        "forbid": ["辣"],
        "expect_count": 3,
    },
    {
        "name": "复杂组合-六人正式聚餐",
        "user_id": None,
        "messages": ["周末请六个人吃饭，稍微正式点，别太难做，推荐6道菜"],
        "expect_count": 6,
    },
    {
        "name": "多人冲突-一人吃辣一人不辣",
        "user_id": None,
        "messages": ["两个人晚饭，一个人想吃辣，一个人一点辣都不想碰，推荐4道菜"],
        "forbid": ["辣"],
        "expect_count": 4,
    },
    {
        "name": "多轮-追加忌口",
        "user_id": None,
        "messages": ["帮我安排一顿晚饭，推荐4道菜", "我对虾过敏", "别太油"],
        "forbid": ["虾"],
        "expect_count": 4,
    },
    {
        "name": "模糊需求-今晚吃啥",
        "user_id": None,
        "messages": ["今晚吃啥比较好？"],
    },
    {
        "name": "时间约束-十分钟早餐",
        "user_id": None,
        "messages": ["给我推荐个早餐吧，别太甜，最好十分钟左右就能弄好，推荐2道"],
        "expect_count": 2,
    },
]


def _as_recipe_like(item):
    from app.models import Recipe

    return Recipe(
        id=item["id"],
        name=item["name"],
        ingredients=item["ingredients"],
        steps=item.get("steps", ""),
        labels=item.get("labels", []),
    )


def main():
    failures = []
    for scenario in SCENARIOS:
        start = time.perf_counter()
        result = recommend(scenario["user_id"], scenario["messages"])
        elapsed = time.perf_counter() - start
        menu_text = " ".join(item["name"] + " " + item["ingredients"] + " " + " ".join(item["labels"]) for item in result["menu"])
        names = [item["name"] for item in result["menu"]]
        issues = []
        if scenario.get("expect_count") and len(result["menu"]) != scenario["expect_count"]:
            issues.append(f"数量不符: expected {scenario['expect_count']}, got {len(result['menu'])}")
        for forbidden in scenario.get("forbid", []):
            if contains_food_term(menu_text, forbidden):
                issues.append(f"命中禁忌: {forbidden} aliases={expand_terms([forbidden])}")
        if elapsed > 2:
            issues.append(f"响应超过2秒: {elapsed:.3f}s")
        if not result["menu"]:
            issues.append("空菜单")
        if "早餐" in scenario["name"] or any("早餐" in msg for msg in scenario["messages"]):
            breakfast_bad = ["猪排", "烧烤", "牛筋"]
            if any(term in menu_text for term in breakfast_bad):
                issues.append(f"早餐搭配不合理: {breakfast_bad}")
        if scenario["name"] == "复杂组合-六人正式聚餐" and result["menu"]:
            first_category = classify_recipe(_as_recipe_like(result["menu"][0]))
            if first_category == "dessert":
                issues.append("聚餐第一道不应是甜品/饮品")
        if issues:
            failures.append((scenario["name"], issues, names, result["constraints"], result["answer"]))
        print(f"\n## {scenario['name']} ({elapsed:.3f}s)")
        print("messages:", " | ".join(scenario["messages"]))
        print("menu:", "、".join(names))
        print("constraints:", result["constraints"])
        print("answer:", result["answer"])
        if issues:
            print("ISSUES:", issues)

    if failures:
        print("\nFAILURES")
        for name, issues, names, constraints, answer in failures:
            print(f"- {name}: {issues}; menu={names}; constraints={constraints}; answer={answer}")
        raise SystemExit(1)
    print("\nall audit scenarios passed")


if __name__ == "__main__":
    main()
