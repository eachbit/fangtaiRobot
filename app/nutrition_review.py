from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.request import Request, urlopen

from .llm_assist import _chat_completions_url, _parse_json_object, is_llm_enabled
from .models import Constraints


ALLOWED_RISK_FLAGS = {
    "sodium_high",
    "sugar_high",
    "fat_high",
    "kcal_low",
    "kcal_high",
    "protein_low",
    "fiber_low",
    "nutrition_data_low_confidence",
    "health_goal_attention",
}
CONFIDENCE_LEVELS = {"low", "medium", "high"}

Provider = Callable[[dict[str, Any]], dict[str, Any] | None]


def build_nutrition_review(
    menu: list[dict[str, Any]],
    nutrition: dict[str, Any],
    constraints: Constraints,
    warnings: list[str] | None = None,
    provider: Provider | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    local_review = _local_review(nutrition, constraints, warnings or [])
    llm_enabled = is_nutrition_review_enabled() if enabled is None else enabled
    if not llm_enabled:
        return {
            **local_review,
            "source": "local",
            "llm_assist": {"enabled": False, "used": False, "error": None},
        }
    if not _should_request_llm_review(local_review, nutrition, constraints):
        return {
            **local_review,
            "source": "local",
            "llm_assist": {"enabled": True, "used": False, "error": None, "skipped": "low_risk"},
        }

    provider = provider or request_nutrition_review_patch
    try:
        patch = provider(_review_payload(menu, nutrition, constraints, local_review, warnings or []))
    except Exception as exc:
        return {
            **local_review,
            "source": "local",
            "llm_assist": {"enabled": True, "used": False, "error": exc.__class__.__name__},
        }

    enhanced, applied = _apply_llm_patch(local_review, patch or {})
    return {
        **enhanced,
        "source": "llm_assisted" if applied else "local",
        "llm_assist": {"enabled": True, "used": bool(applied), "applied_fields": applied, "error": None},
    }


def is_nutrition_review_enabled() -> bool:
    flag = os.environ.get("FANGTAI_LLM_NUTRITION_REVIEW", "")
    return is_llm_enabled() and flag.strip().lower() in {"1", "true", "yes", "on"}


def request_nutrition_review_patch(payload: dict[str, Any]) -> dict[str, Any] | None:
    api_key = os.environ["FANGTAI_LLM_API_KEY"]
    model = os.environ.get("FANGTAI_LLM_MODEL", "gpt-5.4-mini")
    timeout = _nutrition_timeout()
    request_payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是膳食营养评审助手。只返回JSON对象，不要解释。"
                    "只能基于输入中的官方菜单和本地营养数值做评审，禁止新增菜名、替换菜单、"
                    "修改营养数字或放宽过敏忌口。允许字段: summary, risk_flags, suggestions, confidence。"
                    "risk_flags只能从 sodium_high, sugar_high, fat_high, kcal_low, kcal_high, protein_low, "
                    "fiber_low, nutrition_data_low_confidence, health_goal_attention 中选择。"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    }
    request = Request(
        _chat_completions_url(),
        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 fangtai-robot/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    return _parse_json_object(content)


def _nutrition_timeout() -> float:
    return float(os.environ.get("FANGTAI_LLM_NUTRITION_TIMEOUT", os.environ.get("FANGTAI_LLM_TIMEOUT", "2.5")))


def _should_request_llm_review(
    local_review: dict[str, Any],
    nutrition: dict[str, Any],
    constraints: Constraints,
) -> bool:
    if constraints.health_goals:
        return True
    severe_flags = set(local_review.get("risk_flags") or []) - {"fiber_low"}
    return bool(severe_flags)


def _local_review(nutrition: dict[str, Any], constraints: Constraints, warnings: list[str]) -> dict[str, Any]:
    per_person = nutrition.get("per_person") or {}
    confidence = nutrition.get("confidence") or {}
    flags: list[str] = []
    suggestions: list[str] = []

    kcal = float(per_person.get("kcal") or 0)
    protein = float(per_person.get("protein_g") or 0)
    fat = float(per_person.get("fat_g") or 0)
    fiber = float(per_person.get("fiber_g") or 0)
    sugar = float(per_person.get("sugar_g") or 0)
    sodium = float(per_person.get("sodium_mg") or 0)

    kcal_low, kcal_high = (250, 550) if constraints.meal == "早餐" else (350, 900)
    if kcal and kcal < kcal_low:
        flags.append("kcal_low")
        suggestions.append("当前人均热量偏低，可适当增加主食或优质蛋白。")
    if kcal > kcal_high:
        flags.append("kcal_high")
        suggestions.append("当前人均热量偏高，建议控制油脂和主食份量。")
    if protein < (20 if "增肌" in constraints.health_goals else 12):
        flags.append("protein_low")
        suggestions.append("蛋白质略不足，可优先选择鱼虾、禽肉、豆制品等官方菜谱。")
    if fat > (33 if "减脂" in constraints.health_goals else 38):
        flags.append("fat_high")
        suggestions.append("脂肪偏高，建议烹饪时少油，并减少肥肉类菜品摄入量。")
    sodium_limit = 800 if "降压" in constraints.health_goals else 1200
    if sodium > sodium_limit:
        flags.append("sodium_high")
        suggestions.append("钠含量偏高，建议烹饪时减少盐、生抽、蚝油等高钠调味品。")
    if "控糖" in constraints.health_goals and sugar > 35:
        flags.append("sugar_high")
        suggestions.append("控糖目标下糖分偏高，建议减少糖、甜酱和甜味饮品搭配。")
    if fiber and fiber < 4:
        flags.append("fiber_low")
        suggestions.append("膳食纤维偏低，可搭配更多蔬菜类官方菜谱。")
    if confidence.get("level") == "low":
        flags.append("nutrition_data_low_confidence")
        suggestions.append("部分食材营养数据覆盖不足，评估结果需结合实际烹饪用量理解。")
    if warnings:
        flags.append("health_goal_attention")

    flags = _dedupe([flag for flag in flags if flag in ALLOWED_RISK_FLAGS])
    suggestions = _dedupe(suggestions)[:4]
    summary = _local_summary(per_person, constraints, flags, nutrition.get("balance_level", "low"))
    return {
        "summary": summary,
        "risk_flags": flags,
        "suggestions": suggestions,
        "confidence": _confidence(confidence.get("level")),
    }


def _local_summary(
    per_person: dict[str, Any],
    constraints: Constraints,
    flags: list[str],
    balance_level: str,
) -> str:
    sodium = per_person.get("sodium_mg", 0)
    kcal = per_person.get("kcal", 0)
    protein = per_person.get("protein_g", 0)
    if "sodium_high" in flags:
        return f"本地营养估算显示人均钠约{sodium}mg，{_goal_text(constraints)}建议少盐少酱油。"
    if "kcal_high" in flags or "fat_high" in flags:
        return f"本地营养估算显示人均约{kcal}kcal，油脂或热量需适当控制。"
    if "protein_low" in flags:
        return f"本地营养估算显示人均蛋白质约{protein}g，可适当补充优质蛋白。"
    return f"本地营养估算均衡度为{balance_level}，人均约{kcal}kcal、蛋白质约{protein}g。"


def _goal_text(constraints: Constraints) -> str:
    if constraints.health_goals:
        return "、".join(constraints.health_goals) + "目标下"
    return ""


def _review_payload(
    menu: list[dict[str, Any]],
    nutrition: dict[str, Any],
    constraints: Constraints,
    local_review: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "messages": constraints.raw_messages,
        "constraints": constraints.to_dict(),
        "menu": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "ingredients": item.get("ingredients"),
                "labels": item.get("labels", []),
            }
            for item in menu
        ],
        "nutrition": {
            "table": nutrition.get("table"),
            "per_person": nutrition.get("per_person"),
            "balance_level": nutrition.get("balance_level"),
            "confidence": nutrition.get("confidence"),
        },
        "local_review": local_review,
        "warnings": warnings,
    }


def _apply_llm_patch(local_review: dict[str, Any], patch: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    review = dict(local_review)
    applied: list[str] = []

    summary = _short_text(patch.get("summary"), 160)
    if summary:
        review["summary"] = summary
        applied.append("summary")

    flags = [flag for flag in _string_list(patch.get("risk_flags")) if flag in ALLOWED_RISK_FLAGS]
    if flags:
        review["risk_flags"] = _dedupe(review.get("risk_flags", []) + flags)
        applied.append("risk_flags")

    suggestions = _string_list(patch.get("suggestions"), max_item_length=80)[:4]
    if suggestions:
        review["suggestions"] = suggestions
        applied.append("suggestions")

    confidence = _confidence_or_none(patch.get("confidence"))
    if confidence:
        review["confidence"] = confidence
        applied.append("confidence")

    return review, applied


def _short_text(value: Any, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if 1 <= len(text) <= max_length:
        return text
    return None


def _string_list(value: Any, max_item_length: int = 32) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if 1 <= len(text) <= max_item_length and text not in result:
            result.append(text)
    return result


def _confidence(value: Any) -> str:
    return value if isinstance(value, str) and value in CONFIDENCE_LEVELS else "medium"


def _confidence_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value in CONFIDENCE_LEVELS:
        return value
    return None


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
