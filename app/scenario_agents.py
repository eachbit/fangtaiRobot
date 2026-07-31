from __future__ import annotations

import random
import re
from collections.abc import Mapping
from typing import Any


PRIMARY_BUCKETS = {
    "healthy",
    "single_condition",
    "multi_condition",
    "special_group",
    "high_risk",
}
ALLOWED_FIELDS = {
    "candidate_id",
    "health_bucket",
    "messages",
    "structured_ground_truth",
    "agent_review",
}
SAFE_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")


class CustomerScenarioAgent:
    """Development-time user-dialogue generator for auditable scenarios."""

    def __init__(self, seed: int = 20260725):
        self.seed = int(seed)

    def generate(self, index: int) -> dict[str, Any]:
        rng = random.Random(self.seed * 1009 + index * 9176)
        template = TEMPLATES[index % len(TEMPLATES)]
        dish_count = template.get("dish_count") or rng.choice([2, 3, 4, 6])
        meal = template.get("meal") or rng.choice(["早餐", "午餐", "晚餐"])
        allergen = template.get("allergen") or rng.choice(["花生", "虾", "海蛎子", "鸡蛋", "牛奶"])
        avoid = template.get("avoid") or rng.choice(["辣", "甜", "油", "香菜"])
        health_goal = template.get("health_goal") or rng.choice(["减脂", "增肌", "降压", "控糖"])
        people_count = template.get("people_count") or rng.choice([1, 2, 4, 6])

        messages = template["messages"](
            dish_count=dish_count,
            meal=meal,
            allergen=allergen,
            avoid=avoid,
            health_goal=health_goal,
            people_count=people_count,
        )
        messages = _ensure_health_goal_visible(messages, health_goal)
        truth: dict[str, Any] = {
            "dish_count": dish_count,
            "meal": meal,
            "people_count": people_count,
            "health_goals": [health_goal],
            "nutrition_targets": _nutrition_targets(health_goal, meal),
        }
        if template.get("uses_allergen", True):
            truth["forbidden_terms"] = [allergen]
            truth["allergens"] = [allergen]
        if template.get("uses_avoid"):
            truth.setdefault("forbidden_terms", []).append(avoid)
            truth["avoid_terms"] = [avoid]
        if template.get("preserve_unaffected"):
            truth["preserve_unaffected"] = True

        return {
            "candidate_id": f"customer-seed-{self.seed}-index-{index:04d}-{template['intent']}",
            "health_bucket": template["bucket"],
            "messages": messages,
            "structured_ground_truth": truth,
            "agent_review": {
                "generator_agent": "local-deterministic",
                "intent": template["intent"],
            },
        }


class CandidateReviewAgent:
    """Soft judge for naturalness and clarity; never changes hard truth."""

    def review(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        validated = validate_candidate(candidate)
        messages = validated["messages"]
        review = dict(validated.get("agent_review") or {})
        review.update(
            {
                "review_agent": "local-deterministic",
                "naturalness": _score_naturalness(messages),
                "clarity": _score_clarity(messages),
                "notes": "软评审仅供开发期查看，硬判分以 structured_ground_truth 和本地规则为准。",
            }
        )
        return {
            **validated,
            "agent_review": review,
        }


def generate_reviewed_candidates(seed: int, count: int) -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("count must be positive")
    if count > 1000:
        raise ValueError("count must be at most 1000 for interactive jobs")
    generator = CustomerScenarioAgent(seed)
    reviewer = CandidateReviewAgent()
    return [reviewer.review(generator.generate(index)) for index in range(count)]


def agent_candidates_to_audit_scenarios(candidates: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for item in candidates:
        candidate = validate_candidate(item)
        truth = candidate["structured_ground_truth"]
        forbidden = _string_list(truth.get("forbidden_terms"))
        scenarios.append(
            {
                "name": f"Agent生成-{candidate['candidate_id']}",
                "source": "agent_generated",
                "candidate_id": candidate["candidate_id"],
                "user_id": None,
                "messages": candidate["messages"],
                "forbid": forbidden,
                "expect_count": truth.get("dish_count"),
                "agent_review": candidate.get("agent_review", {}),
                "structured_ground_truth": truth,
            }
        )
    return scenarios


def validate_candidate(data: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError("candidate must be an object")
    unknown = set(data) - ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"unknown candidate fields: {sorted(unknown)}")

    candidate_id = data.get("candidate_id")
    if type(candidate_id) is not str or SAFE_ID.fullmatch(candidate_id) is None:
        raise ValueError("candidate_id must be a safe ASCII identifier")

    health_bucket = data.get("health_bucket")
    if health_bucket is not None and health_bucket not in PRIMARY_BUCKETS:
        raise ValueError("unknown health_bucket")

    messages = data.get("messages")
    if type(messages) is not list or not messages:
        raise ValueError("messages must be a non-empty JSON array")
    if len(messages) > 12:
        raise ValueError("messages must contain at most 12 turns")
    if any(type(message) is not str or len(message) > 500 for message in messages):
        raise ValueError("messages must contain short strings")

    truth = data.get("structured_ground_truth")
    if type(truth) is not dict:
        raise ValueError("structured_ground_truth must be an object")
    _assert_json_safe(truth, "structured_ground_truth")

    review = data.get("agent_review", {})
    if type(review) is not dict:
        raise ValueError("agent_review must be an object")
    _assert_json_safe(review, "agent_review")

    result = {
        "candidate_id": candidate_id,
        "messages": list(messages),
        "structured_ground_truth": dict(truth),
    }
    if health_bucket is not None:
        result["health_bucket"] = health_bucket
    if review:
        result["agent_review"] = dict(review)
    return result


def _assert_json_safe(value: Any, path: str) -> None:
    if value is None or type(value) in {str, int, float, bool}:
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _assert_json_safe(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} contains a non-string key")
            _assert_json_safe(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains unsupported JSON value")


def _score_naturalness(messages: list[str]) -> int:
    joined = "".join(messages)
    if len(messages) >= 2 and any(word in joined for word in ["其他", "不变", "尽量别动"]):
        return 5
    if any(word in joined for word in ["帮我", "想", "不要", "安排"]):
        return 4
    return 3


def _score_clarity(messages: list[str]) -> int:
    joined = "".join(messages)
    score = 3
    if any(char.isdigit() for char in joined) or any(word in joined for word in ["两", "三", "四", "六"]):
        score += 1
    if any(word in joined for word in ["过敏", "不吃", "不要", "忌口"]):
        score += 1
    return min(score, 5)


def _string_list(value: Any) -> list[str]:
    if type(value) is not list:
        return []
    return [item for item in value if type(item) is str and item]


def _ensure_health_goal_visible(messages: list[str], health_goal: str) -> list[str]:
    if not health_goal or health_goal in "".join(messages):
        return messages
    updated = list(messages)
    updated[0] = f"{updated[0]} 同时兼顾{health_goal}。"
    return updated


def _nutrition_targets(health_goal: str, meal: str) -> dict[str, Any]:
    targets: dict[str, Any] = {
        "min_balance_level": "medium",
        "min_confidence_level": "low",
    }
    targets["kcal_range_per_person"] = [250, 550] if meal == "早餐" else [350, 900]
    if health_goal == "降压":
        targets["max_sodium_mg_per_person"] = 800
    if health_goal == "控糖":
        targets["max_sugar_g_per_person"] = 35
    if health_goal == "增肌":
        targets["min_protein_g_per_person"] = 20
    if health_goal == "减脂":
        targets["max_fat_g_per_person"] = 35
    return targets


def _single_allergy(**values: Any) -> list[str]:
    return [f"我对{values['allergen']}过敏，{values['meal']}想要{values['dish_count']}道菜，尽量{values['health_goal']}。"]


def _multi_turn_revision(**values: Any) -> list[str]:
    return [
        f"{values['people_count']}个人吃{values['meal']}，先推荐{values['dish_count']}道菜。",
        f"我不吃{values['allergen']}，其他菜尽量别动。",
    ]


def _avoid_taste(**values: Any) -> list[str]:
    return [f"{values['meal']}推荐{values['dish_count']}道菜，不要{values['avoid']}，还要兼顾{values['health_goal']}。"]


def _multi_person(**values: Any) -> list[str]:
    return [
        f"{values['people_count']}个人聚餐，安排{values['dish_count']}道{values['meal']}。",
        f"其中一个人对{values['allergen']}过敏，另一个人不要{values['avoid']}，请同时满足。",
    ]


def _negative_expression(**values: Any) -> list[str]:
    return [f"我不是{values['allergen']}过敏，只是这顿不想吃{values['allergen']}，{values['meal']}来{values['dish_count']}道。"]


TEMPLATES: list[dict[str, Any]] = [
    {"intent": "allergy", "bucket": "single_condition", "messages": _single_allergy, "uses_allergen": True},
    {
        "intent": "relative_revision",
        "bucket": "single_condition",
        "messages": _multi_turn_revision,
        "uses_allergen": True,
        "preserve_unaffected": True,
    },
    {"intent": "avoid_taste", "bucket": "healthy", "messages": _avoid_taste, "uses_allergen": False, "uses_avoid": True},
    {"intent": "multi_person_conflict", "bucket": "multi_condition", "messages": _multi_person, "uses_allergen": True, "uses_avoid": True, "dish_count": 6, "people_count": 6},
    {"intent": "negative_expression", "bucket": "healthy", "messages": _negative_expression, "uses_allergen": True},
]
