from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Callable
from urllib.request import Request, urlopen

from .food_terms import expand_terms
from .models import Constraints


LLM_ALLOWED_FIELDS = {
    "meal",
    "people_count",
    "requested_dish_count",
    "taste",
    "avoid_tastes",
    "health_goals",
    "allergens",
    "preferred_ingredients",
    "avoid_ingredients",
    "max_minutes",
    "difficulty",
    "scene",
}

MEALS = {"早餐", "午餐", "晚餐", "夜宵"}
TASTES = {"清淡", "偏辣", "酸甜", "甜", "咸香"}
HEALTH_GOALS = {"减脂", "增肌", "补钙", "补铁", "控糖", "降压", "降尿酸", "健胃消食"}
SCENES = {"聚餐", "便当", "夏季清爽"}

Provider = Callable[[list[str]], dict[str, Any] | None]


def augment_constraints_with_llm(
    messages: list[str],
    constraints: Constraints,
    provider: Provider | None = None,
) -> tuple[Constraints, dict[str, Any]]:
    provider = provider or request_llm_constraint_patch
    updated = deepcopy(constraints)
    try:
        patch = provider(messages)
    except Exception as exc:
        return updated, {"enabled": True, "used": False, "error": exc.__class__.__name__}
    if not patch:
        return updated, {"enabled": is_llm_enabled(), "used": False, "error": None}
    applied = _apply_patch(updated, patch)
    return updated, {"enabled": True, "used": bool(applied), "applied_fields": applied, "error": None}


def request_llm_constraint_patch(messages: list[str]) -> dict[str, Any] | None:
    if not is_llm_enabled():
        return None
    api_key = os.environ["FANGTAI_LLM_API_KEY"]
    model = os.environ.get("FANGTAI_LLM_MODEL", "gpt-4o-mini")
    timeout = float(os.environ.get("FANGTAI_LLM_TIMEOUT", "2.5"))
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是膳食规划约束抽取器。只返回JSON对象，不要解释。"
                    "允许字段: meal, people_count, requested_dish_count, taste, avoid_tastes, "
                    "health_goals, allergens, preferred_ingredients, avoid_ingredients, max_minutes, difficulty, scene。"
                    "不吃/忌口/不喜欢必须放avoid_ingredients，只有明确过敏才放allergens。"
                ),
            },
            {"role": "user", "content": "\n".join(messages)},
        ],
    }
    request = Request(
        _chat_completions_url(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
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


def is_llm_enabled() -> bool:
    return bool(os.environ.get("FANGTAI_LLM_API_KEY") and os.environ.get("FANGTAI_LLM_BASE_URL"))


def _chat_completions_url() -> str:
    base_url = os.environ["FANGTAI_LLM_BASE_URL"].rstrip("/")
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def _parse_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    value = json.loads(text[start : end + 1])
    return value if isinstance(value, dict) else None


def _apply_patch(constraints: Constraints, patch: dict[str, Any]) -> list[str]:
    applied: list[str] = []
    if _set_if_missing(constraints, "meal", _scalar_choice(patch, "meal", MEALS)):
        applied.append("meal")
    if _set_if_missing(constraints, "taste", _scalar_choice(patch, "taste", TASTES)):
        applied.append("taste")
    if _set_if_missing(constraints, "scene", _scalar_choice(patch, "scene", SCENES)):
        applied.append("scene")
    if _set_if_missing(constraints, "difficulty", _short_text(patch.get("difficulty"))):
        applied.append("difficulty")
    if _set_if_missing(constraints, "people_count", _bounded_int(patch.get("people_count"), 1, 20)):
        applied.append("people_count")
    if _set_if_missing(
        constraints,
        "requested_dish_count",
        _bounded_int(patch.get("requested_dish_count"), 1, 8),
    ):
        applied.append("requested_dish_count")
    if _set_if_missing(constraints, "max_minutes", _bounded_int(patch.get("max_minutes"), 1, 240)):
        applied.append("max_minutes")
    applied.extend(_extend_list(constraints.avoid_tastes, _string_list(patch.get("avoid_tastes"))))
    applied.extend(_extend_list(constraints.health_goals, _filtered_list(patch.get("health_goals"), HEALTH_GOALS)))
    applied.extend(_extend_allergens(constraints, expand_terms(_string_list(patch.get("allergens")))))
    applied.extend(_extend_list(constraints.preferred_ingredients, _string_list(patch.get("preferred_ingredients"))))
    applied.extend(_extend_list(constraints.avoid_ingredients, expand_terms(_string_list(patch.get("avoid_ingredients")))))
    _remove_blocked_preferences(constraints)
    return applied


def _set_if_missing(constraints: Constraints, field: str, value: Any) -> bool:
    if value is None or getattr(constraints, field) is not None:
        return False
    setattr(constraints, field, value)
    return True


def _scalar_choice(patch: dict[str, Any], field: str, allowed: set[str]) -> str | None:
    value = patch.get(field)
    if isinstance(value, str) and value in allowed:
        return value
    return None


def _short_text(value: Any) -> str | None:
    if isinstance(value, str) and 1 <= len(value) <= 12:
        return value
    return None


def _bounded_int(value: Any, minimum: int, maximum: int) -> int | None:
    if type(value) is not int:
        return None
    if minimum <= value <= maximum:
        return value
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if 1 <= len(text) <= 12 and text not in result:
                result.append(text)
    return result


def _filtered_list(value: Any, allowed: set[str]) -> list[str]:
    return [item for item in _string_list(value) if item in allowed]


def _extend_list(target: list[str], values: list[str]) -> list[str]:
    applied: list[str] = []
    for value in values:
        if value and value not in target:
            target.append(value)
            applied.append(value)
    return applied


def _extend_allergens(constraints: Constraints, values: list[str]) -> list[str]:
    existing_avoid = set(constraints.avoid_ingredients)
    safe_values = [value for value in values if value not in existing_avoid]
    return _extend_list(constraints.allergens, safe_values)


def _remove_blocked_preferences(constraints: Constraints) -> None:
    blocked = set(constraints.allergens + constraints.avoid_ingredients)
    constraints.preferred_ingredients = [
        value for value in constraints.preferred_ingredients
        if value not in blocked
    ]
