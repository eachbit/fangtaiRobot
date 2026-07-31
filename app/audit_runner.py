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
    summary = build_audit_summary(records, len(records), start)
    return {"summary": summary, "records": records}


def evaluate_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        result, turn_debug = _recommend_for_audit(scenario)
        elapsed_ms = _elapsed_ms(start)
        issues, debug = _audit_result(scenario, result, elapsed_ms)
        debug.update(turn_debug)
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


def _recommend_for_audit(scenario: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    messages = list(scenario.get("messages") or [])
    user_id = scenario.get("user_id")
    if len(messages) <= 1:
        return recommend(user_id, messages), {
            "turn_count": len(messages),
            "session_simulated": False,
        }

    session_id = None
    result: dict[str, Any] | None = None
    turns: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        result = recommend(user_id, [message], session_id=session_id)
        session_id = result.get("session_id")
        changes = result.get("changes") or {}
        turns.append(
            {
                "turn": index + 1,
                "message": message,
                "session_id": session_id,
                "menu_ids": [item.get("id") for item in result.get("menu") or []],
                "change_mode": changes.get("mode"),
                "change_count": changes.get("change_count"),
            }
        )
    assert result is not None
    return result, {
        "turn_count": len(messages),
        "session_simulated": True,
        "turns": turns,
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
        debug["nutrition_evaluation"] = nutrition_debug
        if _nutrition_targets_are_strict(scenario):
            issues.extend(nutrition_issues)
        elif nutrition_issues:
            debug["nutrition_advisories"] = nutrition_issues

    return issues, debug


def _nutrition_targets_are_strict(scenario: dict[str, Any]) -> bool:
    truth = scenario.get("structured_ground_truth") or {}
    if truth.get("nutrition_strict") is True:
        return True
    return scenario.get("source") != "agent_generated"


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


def build_audit_summary(records: list[dict[str, Any]], total: int, start: float) -> dict[str, Any]:
    failed = sum(1 for record in records if record["status"] == "failed")
    completed = len(records)
    return {
        "total": total,
        "passed": completed - failed,
        "failed": failed,
        "duration_ms": _elapsed_ms(start),
        "official_report": _official_report(records, total),
    }


def _official_report(records: list[dict[str, Any]], total: int) -> dict[str, Any]:
    sections = {
        "basic_recommendation": _basic_recommendation_section(records),
        "complex_scenario": _complex_scenario_section(records),
        "multi_turn_interaction": _multi_turn_section(records),
        "performance_efficiency": _performance_section(records),
    }
    total_score = sum(section["score"] for section in sections.values())
    top_issues = _top_issues(records)
    recommendations = _official_recommendations(sections, top_issues)
    return {
        "total_score": round(total_score, 1),
        "max_score": 100,
        "record_count": total,
        "completed_count": len(records),
        "sections": sections,
        "top_issues": top_issues,
        "recommendations": recommendations,
    }


def _basic_recommendation_section(records: list[dict[str, Any]]) -> dict[str, Any]:
    forbidden_violations = _count_issues(records, "命中禁忌")
    empty_menus = _count_issues(records, "空菜单")
    official_failures = sum(
        1
        for record in records
        if not (((record.get("result") or {}).get("score_card") or {}).get("official_recipe", False))
    )
    penalty = min(20.0, forbidden_violations * 5.0 + empty_menus * 5.0 + official_failures * 5.0)
    return _section(
        score=20.0 - penalty,
        max_score=20,
        metrics={
            "forbidden_violations": forbidden_violations,
            "empty_menus": empty_menus,
            "official_recipe_failures": official_failures,
        },
        samples=_sample_names(records, lambda record: _has_issue(record, "命中禁忌") or _has_issue(record, "空菜单")),
    )


def _complex_scenario_section(records: list[dict[str, Any]]) -> dict[str, Any]:
    complex_records = [record for record in records if _is_complex_record(record)]
    count_mismatches = _count_issues(complex_records, "数量不符")
    nutrition_advisory_records = sum(1 for record in complex_records if (record.get("debug") or {}).get("nutrition_advisories"))
    low_balance = sum(
        1
        for record in complex_records
        if (((record.get("result") or {}).get("nutrition") or {}).get("balance_level") == "low")
    )
    denominator = max(1, len(complex_records))
    weighted_risk = count_mismatches * 0.45 + nutrition_advisory_records * 0.20 + low_balance * 0.25
    penalty = 20.0 * min(1.0, weighted_risk / denominator)
    return _section(
        score=20.0 - penalty,
        max_score=20,
        metrics={
            "complex_records": len(complex_records),
            "count_mismatches": count_mismatches,
            "nutrition_advisory_records": nutrition_advisory_records,
            "low_balance_records": low_balance,
        },
        samples=_sample_names(
            complex_records,
            lambda record: _has_issue(record, "数量不符") or bool((record.get("debug") or {}).get("nutrition_advisories")),
        ),
    )


def _multi_turn_section(records: list[dict[str, Any]]) -> dict[str, Any]:
    multi_turn_records = [record for record in records if _is_multi_turn_record(record)]
    failed_records = sum(1 for record in multi_turn_records if record["status"] == "failed")
    minimal_change_failures = sum(
        1
        for record in multi_turn_records
        if (((record.get("result") or {}).get("score_card") or {}).get("minimal_change") is False)
    )
    low_review_quality = sum(1 for record in multi_turn_records if _review_score(record, "naturalness") < 4 or _review_score(record, "clarity") < 4)
    denominator = max(1, len(multi_turn_records))
    weighted_risk = failed_records * 0.50 + minimal_change_failures * 0.35 + low_review_quality * 0.15
    penalty = 30.0 * min(1.0, weighted_risk / denominator)
    return _section(
        score=30.0 - penalty,
        max_score=30,
        metrics={
            "multi_turn_records": len(multi_turn_records),
            "failed_records": failed_records,
            "minimal_change_failures": minimal_change_failures,
            "low_review_quality_records": low_review_quality,
        },
        samples=_sample_names(
            multi_turn_records,
            lambda record: record["status"] == "failed"
            or (((record.get("result") or {}).get("score_card") or {}).get("minimal_change") is False),
        ),
    )


def _performance_section(records: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = sorted(int(record.get("elapsed_ms") or 0) for record in records)
    p95 = _percentile(elapsed, 0.95)
    average = round(sum(elapsed) / len(elapsed), 1) if elapsed else 0.0
    over_8s = sum(1 for value in elapsed if value > 8000)
    over_15s = sum(1 for value in elapsed if value > 15000)
    penalty = 0.0
    if p95 > 2000:
        penalty += 8.0 if p95 <= 5000 else 15.0
    if average > 6000:
        penalty += 5.0 if average <= 12000 else 10.0
    penalty += min(5.0, over_8s * 1.0 + over_15s * 2.0)
    return _section(
        score=max(0.0, 30.0 - penalty),
        max_score=30,
        metrics={
            "average_ms": average,
            "p95_ms": p95,
            "over_8s_records": over_8s,
            "over_15s_records": over_15s,
        },
        samples=_sample_names(records, lambda record: int(record.get("elapsed_ms") or 0) > 8000),
    )


def _section(score: float, max_score: int, metrics: dict[str, Any], samples: list[str]) -> dict[str, Any]:
    return {
        "score": round(max(0.0, min(float(max_score), score)), 1),
        "max_score": max_score,
        "metrics": metrics,
        "samples": samples,
    }


def _is_complex_record(record: dict[str, Any]) -> bool:
    name = str(record.get("name") or "")
    constraints = record.get("constraints") or {}
    expected_count = (record.get("debug") or {}).get("expected_count")
    return (
        any(word in name for word in ["复杂", "多人", "聚餐", "正式", "六人"])
        or int(constraints.get("people_count") or 0) > 1
        or int(expected_count or 0) >= 4
    )


def _is_multi_turn_record(record: dict[str, Any]) -> bool:
    name = str(record.get("name") or "")
    messages = record.get("messages") or []
    return "多轮" in name or len(messages) > 1


def _review_score(record: dict[str, Any], key: str) -> int:
    review = ((record.get("debug") or {}).get("agent_review") or {})
    value = review.get(key)
    return int(value) if isinstance(value, int) else 5


def _count_issues(records: list[dict[str, Any]], term: str) -> int:
    return sum(1 for record in records for issue in record.get("issues", []) if term in issue)


def _has_issue(record: dict[str, Any], term: str) -> bool:
    return any(term in issue for issue in record.get("issues", []))


def _sample_names(records: list[dict[str, Any]], predicate, limit: int = 5) -> list[str]:
    return [str(record.get("name") or "未命名场景") for record in records if predicate(record)][:limit]


def _top_issues(records: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in records:
        for issue in record.get("issues", []):
            label = issue.split(":", 1)[0]
            counts[label] = counts.get(label, 0) + 1
        for advisory in (record.get("debug") or {}).get("nutrition_advisories") or []:
            label = advisory.split(":", 1)[0]
            counts[f"advisory:{label}"] = counts.get(f"advisory:{label}", 0) + 1
    if not counts:
        return [{"issue": "未发现硬失败", "count": 0}]
    return [
        {"issue": issue, "count": count}
        for issue, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _official_recommendations(sections: dict[str, dict[str, Any]], top_issues: list[dict[str, Any]]) -> list[str]:
    suggestions: list[str] = []
    if sections["basic_recommendation"]["score"] < 20:
        suggestions.append("优先修复忌口/过敏和空菜单问题，这会直接影响基础推荐得分。")
    if sections["complex_scenario"]["metrics"]["nutrition_advisory_records"]:
        suggestions.append("继续优化多人整桌营养目标，重点关注钠、糖、蛋白质和脂肪偏差。")
    if sections["multi_turn_interaction"]["metrics"]["minimal_change_failures"]:
        suggestions.append("加强多轮会话菜单保留，避免用户要求微调时大面积换菜。")
    if sections["performance_efficiency"]["score"] < 30:
        suggestions.append("检查慢场景的检索和营养换菜路径，保持 P95 小于 2 秒。")
    if not suggestions and top_issues and top_issues[0]["count"] > 0:
        suggestions.append(f"优先查看最高频问题：{top_issues[0]['issue']}，定位对应失败样例后补回归测试。")
    if not suggestions and top_issues and top_issues[0]["count"] == 0:
        suggestions.append("当前批测没有硬失败，下一步应扩大健康情况和多人冲突覆盖范围。")
    return suggestions[:4]


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * ratio))))
    return values[index]


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
