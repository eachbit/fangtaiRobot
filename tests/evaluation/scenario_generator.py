from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
import json
import random
from typing import Any

from tests.evaluation.dialogue_state_machine import (
    build_dialogue_plan,
    persona_disclosure,
)
from tests.evaluation.language_mutator import variants_for_intent
from tests.evaluation.persona_factory import (
    ALLERGEN_OPTIONS,
    CONDITION_GROUPS,
    build_personas,
)
from tests.evaluation.schemas import PRIMARY_BUCKETS, MenuExpectation, Scenario


MANDATORY_INTENTS = (
    "hard_constraint",
    "health_profile",
    "structure_ratio",
    "relative_revision",
    "cooking_diversity",
    "nutrition_tradeoff",
    "ambiguous_request",
    "negative_expression",
    "multi_person_conflict",
)

_RELATIVE_OPERATIONS = (
    "append_constraint",
    "retract_preference",
    "request_position_change",
    "request_structure_change",
    "confirm_clarification",
)
_PAIR_DIMENSIONS = (
    ("intent", "dialogue"),
    ("primary_bucket", "dialogue"),
    ("primary_bucket", "intent"),
)


def _choose_text(intent: str, rng: random.Random) -> str:
    variants = variants_for_intent(intent)
    return variants[rng.randrange(len(variants))].text


def _hard_constraint(persona: Any, rng: random.Random) -> tuple[tuple[str, ...], MenuExpectation]:
    forbidden = tuple(persona.allergens) or ("花生",)
    primary = forbidden[0]
    phrasings = (
        f"我对{primary}过敏，菜单里避开{primary}"
        if persona.allergens
        else f"本次忌口{primary}",
        f"不要放{primary}",
    )
    phrasing = phrasings[rng.randrange(len(phrasings))]
    message = f"{phrasing}。本次明确避开{'、'.join(forbidden)}，请安排六道菜。"
    return (message,), MenuExpectation(forbidden_terms=forbidden)


def _health_profile(persona: Any, rng: random.Random) -> tuple[tuple[str, ...], MenuExpectation]:
    message = f"{persona_disclosure(persona)}{_choose_text('health_profile', rng)}，请安排六道菜。"
    return (message,), MenuExpectation()


def _structure_ratio(persona: Any, rng: random.Random) -> tuple[tuple[str, ...], MenuExpectation]:
    phrasing = _choose_text("structure_ratio", rng)
    message = f"{phrasing}。请明确安排六道菜，按两荤四素执行。"
    return (
        (message,),
        MenuExpectation(dish_count=6, meat_count=2, vegetable_count=4),
    )


def _cooking_diversity(persona: Any, rng: random.Random) -> tuple[tuple[str, ...], MenuExpectation]:
    phrasing = _choose_text("cooking_diversity", rng)
    message = f"{phrasing}。六道菜至少采用三种不同烹饪方法。"
    return (
        (message,),
        MenuExpectation(dish_count=6, minimum_cooking_methods=3),
    )


def _nutrition_tradeoff(persona: Any, rng: random.Random) -> tuple[tuple[str, ...], MenuExpectation]:
    message = f"{persona_disclosure(persona)}{_choose_text('nutrition_tradeoff', rng)}，请安排六道菜。"
    return (message,), MenuExpectation()


def _safe_negative_message(persona: Any, rng: random.Random) -> str:
    missing_conditions = tuple(sorted(set(CONDITION_GROUPS) - set(persona.special_groups)))
    missing_allergens = tuple(
        item for item in ALLERGEN_OPTIONS if item not in persona.allergens
    )
    choices: list[str] = []
    choices.extend(f"我没有{condition}" for condition in missing_conditions)
    choices.extend(f"我不对{allergen}过敏" for allergen in missing_allergens)
    statement = choices[rng.randrange(len(choices))]
    return f"{statement}。请不要据此改变我已提供的健康情况、过敏和目标。"


def _multi_person_conflict(persona: Any) -> tuple[tuple[str, ...], MenuExpectation]:
    own_constraint = persona.allergens[0] if persona.allergens else "花生"
    other_constraint = "牛肉" if own_constraint != "牛肉" else "鸡蛋"
    message = (
        f"{persona_disclosure(persona)}"
        f"我需要避开{own_constraint}，另一位用餐者需要避开{other_constraint}，"
        "两人的约束不同，请先澄清这份菜单服务谁或是否同时满足。"
    )
    return (message,), MenuExpectation(clarification_required=True)


def _scenario_id(seed: int, index: int, intent: str, operation: str | None) -> str:
    base = f"seed-{seed}-index-{index:04d}-{intent}"
    return base if operation is None else f"{base}-operation-{operation}"


def generate_scenarios(seed: int, count: int) -> tuple[Scenario, ...]:
    if count < 10:
        raise ValueError("count must be at least 10")

    rng = random.Random(seed)
    personas = build_personas(seed, count)
    intent_indexes: dict[str, int] = defaultdict(int)
    scenarios: list[Scenario] = []

    for index, persona in enumerate(personas):
        intent = MANDATORY_INTENTS[index % len(MANDATORY_INTENTS)]
        intent_index = intent_indexes[intent]
        intent_indexes[intent] += 1
        operation: str | None = None
        dialogue_mode = "single_turn"

        if intent == "hard_constraint":
            messages, expectation = _hard_constraint(persona, rng)
        elif intent == "health_profile":
            messages, expectation = _health_profile(persona, rng)
        elif intent == "structure_ratio":
            messages, expectation = _structure_ratio(persona, rng)
        elif intent == "relative_revision":
            operation = _RELATIVE_OPERATIONS[intent_index % len(_RELATIVE_OPERATIONS)]
            plan = build_dialogue_plan(persona, operation, seed=rng.randrange(2**63))
            messages = plan.messages
            expectation = MenuExpectation(
                dish_count=plan.expectation.dish_count,
                meat_count=plan.expectation.meat_count,
                vegetable_count=plan.expectation.vegetable_count,
                minimum_cooking_methods=plan.expectation.minimum_cooking_methods,
                forbidden_terms=plan.expectation.forbidden_terms,
                clarification_required=plan.expectation.clarification_required,
                preserve_unaffected=True,
            )
            dialogue_mode = "multi_turn"
        elif intent == "cooking_diversity":
            messages, expectation = _cooking_diversity(persona, rng)
        elif intent == "nutrition_tradeoff":
            messages, expectation = _nutrition_tradeoff(persona, rng)
        elif intent == "ambiguous_request":
            operation = "ambiguous_change"
            plan = build_dialogue_plan(persona, operation)
            messages = plan.messages
            expectation = plan.expectation
            dialogue_mode = "multi_turn"
        elif intent == "negative_expression":
            messages = (_safe_negative_message(persona, rng),)
            expectation = MenuExpectation()
        else:
            messages, expectation = _multi_person_conflict(persona)

        scenarios.append(
            Scenario(
                scenario_id=_scenario_id(seed, index, intent, operation),
                persona=persona,
                messages=messages,
                expectation=expectation,
                seed=seed,
                intent=intent,
                dialogue_mode=dialogue_mode,
            )
        )

    return tuple(scenarios)


def _operation_from_scenario(scenario: Scenario) -> str | None:
    marker = "-operation-"
    if marker not in scenario.scenario_id:
        return None
    return scenario.scenario_id.rsplit(marker, 1)[1]


def validate_coverage(scenarios: Iterable[Scenario]) -> None:
    values = tuple(scenarios)
    present_buckets = {item.persona.primary_bucket for item in values}
    present_intents = {item.intent for item in values}
    missing_buckets = sorted(PRIMARY_BUCKETS - present_buckets)
    missing_intents = sorted(set(MANDATORY_INTENTS) - present_intents)
    if missing_buckets or missing_intents:
        raise ValueError(
            "coverage validation failed: "
            f"missing primary_bucket={missing_buckets}; missing intent={missing_intents}"
        )


def _sorted_counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def summarize_coverage(scenarios: Iterable[Scenario]) -> dict[str, dict[str, Any]]:
    values = tuple(scenarios)
    validate_coverage(values)
    dimensions = {
        "primary_bucket": tuple(item.persona.primary_bucket for item in values),
        "intent": tuple(item.intent for item in values),
        "dialogue": tuple(item.dialogue_mode for item in values),
    }
    operations = tuple(
        operation
        for item in values
        if (operation := _operation_from_scenario(item)) is not None
    )
    pairs: dict[str, dict[str, int]] = {}
    for left, right in _PAIR_DIMENSIONS:
        counts = Counter(
            f"{left}={left_value}|{right}={right_value}"
            for left_value, right_value in zip(
                dimensions[left], dimensions[right], strict=True
            )
        )
        pairs[f"{left},{right}"] = dict(sorted(counts.items()))

    return {
        "primary_bucket": _sorted_counts(dimensions["primary_bucket"]),
        "intent": _sorted_counts(dimensions["intent"]),
        "dialogue": _sorted_counts(dimensions["dialogue"]),
        "operation": _sorted_counts(operations),
        "pairs": dict(sorted(pairs.items())),
    }


def scenarios_to_json(scenarios: Iterable[Scenario]) -> str:
    return json.dumps(
        [item.to_dict() for item in scenarios],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def scenario_json_bytes(scenarios: Iterable[Scenario]) -> bytes:
    return scenarios_to_json(scenarios).encode("utf-8")
