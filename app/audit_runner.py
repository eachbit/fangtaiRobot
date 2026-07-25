from __future__ import annotations

import time
from typing import Any

from .agent import recommend
from .food_terms import contains_food_term, expand_terms
from .models import Recipe
from .recipe_features import classify_recipe


DEFAULT_SCENARIOS: list[dict[str, Any]] = [
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


def run_audit(scenarios: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    records = [evaluate_scenario(scenario) for scenario in (scenarios or DEFAULT_SCENARIOS)]
    summary = _summary(records, start)
    return {"summary": summary, "records": records}


def evaluate_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        result = recommend(scenario.get("user_id"), list(scenario.get("messages") or []))
        elapsed_ms = _elapsed_ms(start)
        issues, debug = _audit_result(scenario, result, elapsed_ms)
        menu = result.get("menu") or []
        return {
            "name": scenario.get("name") or "未命名场景",
            "status": "failed" if issues else "passed",
            "messages": list(scenario.get("messages") or []),
            "user_id": scenario.get("user_id"),
            "elapsed_ms": elapsed_ms,
            "issues": issues,
            "answer": result.get("answer", ""),
            "menu": _menu_summary(menu),
            "constraints": result.get("constraints", {}),
            "debug": debug,
            "result": result,
        }
    except Exception as exc:
        return {
            "name": scenario.get("name") or "未命名场景",
            "status": "failed",
            "messages": list(scenario.get("messages") or []),
            "user_id": scenario.get("user_id"),
            "elapsed_ms": _elapsed_ms(start),
            "issues": [f"执行异常：{type(exc).__name__}: {exc}"],
            "answer": "",
            "menu": [],
            "constraints": {},
            "debug": {"exception": repr(exc)},
            "result": None,
        }


def _audit_result(scenario: dict[str, Any], result: dict[str, Any], elapsed_ms: int) -> tuple[list[str], dict[str, Any]]:
    menu = result.get("menu") or []
    menu_text = " ".join(
        f"{item.get('name', '')} {item.get('ingredients', '')} {' '.join(item.get('labels') or [])}"
        for item in menu
    )
    issues: list[str] = []
    debug: dict[str, Any] = {
        "expected_count": scenario.get("expect_count"),
        "actual_count": len(menu),
        "forbidden": list(scenario.get("forbid") or []),
        "menu_names": [item.get("name") for item in menu],
        "elapsed_ms": elapsed_ms,
    }
    if scenario.get("source"):
        debug["source"] = scenario.get("source")
    if scenario.get("agent_review"):
        debug["agent_review"] = scenario.get("agent_review")

    expected_count = scenario.get("expect_count")
    if expected_count and len(menu) != expected_count:
        issues.append(f"数量不符: expected {expected_count}, got {len(menu)}")

    for forbidden in scenario.get("forbid") or []:
        if contains_food_term(menu_text, forbidden):
            issues.append(f"命中禁忌: {forbidden} aliases={expand_terms([forbidden])}")

    if elapsed_ms > 2000:
        issues.append(f"响应超过2秒: {elapsed_ms}ms")

    if not menu:
        issues.append("空菜单")

    messages = scenario.get("messages") or []
    if "早餐" in str(scenario.get("name", "")) or any("早餐" in message for message in messages):
        breakfast_bad = ["猪排", "烧烤", "牛筋"]
        if any(term in menu_text for term in breakfast_bad):
            issues.append(f"早餐搭配不合理: {breakfast_bad}")
            debug["breakfast_bad_terms"] = breakfast_bad

    if scenario.get("name") == "复杂组合-六人正式聚餐" and menu:
        first_category = classify_recipe(_as_recipe_like(menu[0]))
        debug["first_category"] = first_category
        if first_category == "dessert":
            issues.append("聚餐第一道不应是甜品/饮品")

    nutrition_targets = ((scenario.get("structured_ground_truth") or {}).get("nutrition_targets") or {})
    if nutrition_targets:
        nutrition_issues, nutrition_debug = _evaluate_nutrition_targets(
            result.get("nutrition") or {},
            nutrition_targets,
        )
        issues.extend(nutrition_issues)
        debug["nutrition_evaluation"] = nutrition_debug

    return issues, debug


def _evaluate_nutrition_targets(nutrition: dict[str, Any], targets: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    per_person = nutrition.get("per_person") or {}
    balance_level = nutrition.get("balance_level", "low")
    confidence = nutrition.get("confidence") or {}
    confidence_level = confidence.get("level", "low")
    debug = {
        "targets": dict(targets),
        "actual_balance_level": balance_level,
        "actual_confidence_level": confidence_level,
        "per_person": dict(per_person),
    }
    issues: list[str] = []

    min_balance = targets.get("min_balance_level")
    if min_balance and _level_rank(balance_level) < _level_rank(min_balance):
        issues.append(f"nutrition balance below target: expected>={min_balance}, got {balance_level}")

    min_confidence = targets.get("min_confidence_level")
    if min_confidence and _level_rank(confidence_level) < _level_rank(min_confidence):
        issues.append(f"nutrition confidence below target: expected>={min_confidence}, got {confidence_level}")

    kcal_range = targets.get("kcal_range_per_person")
    if isinstance(kcal_range, list) and len(kcal_range) == 2:
        kcal = _number(per_person.get("kcal"))
        if kcal is None or kcal < float(kcal_range[0]) or kcal > float(kcal_range[1]):
            issues.append(f"nutrition kcal outside target: expected {kcal_range}, got {kcal}")

    max_sodium = _number(targets.get("max_sodium_mg_per_person"))
    sodium = _number(per_person.get("sodium_mg"))
    if max_sodium is not None and (sodium is None or sodium > max_sodium):
        issues.append(f"nutrition sodium above target: expected<={max_sodium}, got {sodium}")

    max_sugar = _number(targets.get("max_sugar_g_per_person"))
    sugar = _number(per_person.get("sugar_g"))
    if max_sugar is not None and (sugar is None or sugar > max_sugar):
        issues.append(f"nutrition sugar above target: expected<={max_sugar}, got {sugar}")

    min_protein = _number(targets.get("min_protein_g_per_person"))
    protein = _number(per_person.get("protein_g"))
    if min_protein is not None and (protein is None or protein < min_protein):
        issues.append(f"nutrition protein below target: expected>={min_protein}, got {protein}")

    max_fat = _number(targets.get("max_fat_g_per_person"))
    fat = _number(per_person.get("fat_g"))
    if max_fat is not None and (fat is None or fat > max_fat):
        issues.append(f"nutrition fat above target: expected<={max_fat}, got {fat}")

    return issues, debug


def _level_rank(level: Any) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(level), 0)


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _summary(records: list[dict[str, Any]], start: float) -> dict[str, Any]:
    failed = sum(1 for record in records if record["status"] == "failed")
    total = len(records)
    return {
        "total": total,
        "passed": total - failed,
        "failed": failed,
        "duration_ms": _elapsed_ms(start),
    }


def _menu_summary(menu: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "ingredients": item.get("ingredients", ""),
            "labels": list(item.get("labels") or []),
            "reason": item.get("reason", ""),
        }
        for item in menu
    ]


def _as_recipe_like(item: dict[str, Any]) -> Recipe:
    return Recipe(
        id=int(item.get("id") or 0),
        name=item.get("name", ""),
        ingredients=item.get("ingredients", ""),
        steps=item.get("steps", ""),
        labels=list(item.get("labels") or []),
    )


def _elapsed_ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))
