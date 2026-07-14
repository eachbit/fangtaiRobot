from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
import json
import random
import re
from typing import Any

from tests.evaluation.dialogue_state_machine import (
    DIALOGUE_OPERATIONS,
    build_dialogue_plan,
    operation_matches_plan,
    persona_disclosure,
)
from tests.evaluation.language_mutator import LanguageVariant, variants_for_intent
from tests.evaluation.persona_factory import (
    ALLERGEN_OPTIONS,
    CONDITION_GROUPS,
    build_personas,
)
from tests.evaluation.schemas import (
    DIALOGUE_MODES,
    PRIMARY_BUCKETS,
    HealthPersona,
    MenuExpectation,
    Scenario,
)


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
_RELATIVE_INTENT_INDEX = MANDATORY_INTENTS.index("relative_revision")
MIN_FULL_OPERATION_COVERAGE = (
    _RELATIVE_INTENT_INDEX
    + 1
    + (len(_RELATIVE_OPERATIONS) - 1) * len(MANDATORY_INTENTS)
)
if MANDATORY_INTENTS.index("ambiguous_request") + 1 > MIN_FULL_OPERATION_COVERAGE:
    raise RuntimeError("ambiguous_change must occur before full operation coverage")


def _choose_text(intent: str, rng: random.Random) -> str:
    variants = variants_for_intent(intent)
    return variants[rng.randrange(len(variants))].text


def _choose_variant(intent: str, rng: random.Random) -> LanguageVariant:
    variants = variants_for_intent(intent)
    return variants[rng.randrange(len(variants))]


def _initial_context(persona: HealthPersona) -> str:
    return persona_disclosure(persona) + "请按这些信息安排六道晚餐。"


def _base_expectation(
    persona: HealthPersona,
    *,
    clarification_required: bool | None = None,
) -> MenuExpectation:
    clarification = (
        persona.primary_bucket == "high_risk"
        if clarification_required is None
        else clarification_required
    )
    return MenuExpectation(
        dish_count=6,
        forbidden_terms=tuple(persona.allergens),
        clarification_required=clarification,
    )


def _hard_constraint(
    persona: HealthPersona,
    rng: random.Random,
) -> tuple[tuple[str, ...], MenuExpectation]:
    options = tuple(
        item
        for item in ("花生", "香菜", "芹菜", "动物内脏")
        if item not in persona.allergens
    )
    added = "花生" if not persona.allergens else options[rng.randrange(len(options))]
    message = f"{_initial_context(persona)}另外本次新增忌口：不要放{added}。"
    return (
        (message,),
        MenuExpectation(
            dish_count=6,
            forbidden_terms=(*persona.allergens, added),
            clarification_required=persona.primary_bucket == "high_risk",
        ),
    )


def _health_profile(
    persona: HealthPersona,
    rng: random.Random,
) -> tuple[tuple[str, ...], MenuExpectation]:
    del rng
    message = f"{_initial_context(persona)}请综合这些健康情况选择菜品。"
    return (message,), _base_expectation(persona)


def _structure_ratio(
    persona: HealthPersona,
    rng: random.Random,
) -> tuple[tuple[str, ...], MenuExpectation]:
    variant = _choose_variant("structure_ratio", rng)
    truth = dict(variant.ground_truth)
    exact_ratio = truth.get("ratio_meat") == 1 and truth.get("ratio_vegetable") == 2
    message = f"{_initial_context(persona)}{variant.text}。"
    return (
        (message,),
        MenuExpectation(
            dish_count=6,
            meat_count=2 if exact_ratio else None,
            vegetable_count=4 if exact_ratio else None,
            forbidden_terms=tuple(persona.allergens),
            clarification_required=(
                persona.primary_bucket == "high_risk" or not exact_ratio
            ),
        ),
    )


def _cooking_diversity(
    persona: HealthPersona,
    rng: random.Random,
) -> tuple[tuple[str, ...], MenuExpectation]:
    variants = tuple(
        item
        for item in variants_for_intent("cooking_diversity")
        if dict(item.ground_truth).get("minimum_cooking_methods") == 3
    )
    variant = variants[rng.randrange(len(variants))]
    message = f"{_initial_context(persona)}{variant.text}。"
    return (
        (message,),
        MenuExpectation(
            dish_count=6,
            minimum_cooking_methods=3,
            forbidden_terms=tuple(persona.allergens),
            clarification_required=persona.primary_bucket == "high_risk",
        ),
    )


def _nutrition_tradeoff(
    persona: HealthPersona,
    rng: random.Random,
) -> tuple[tuple[str, ...], MenuExpectation]:
    message = f"{_initial_context(persona)}{_choose_text('nutrition_tradeoff', rng)}。"
    return (message,), _base_expectation(persona)


def _safe_negative_message(persona: HealthPersona, rng: random.Random) -> str:
    missing_conditions = tuple(sorted(set(CONDITION_GROUPS) - set(persona.special_groups)))
    missing_allergens = tuple(
        item for item in ALLERGEN_OPTIONS if item not in persona.allergens
    )
    choices: list[str] = []
    choices.extend(f"我没有{condition}" for condition in missing_conditions)
    choices.extend(f"我不对{allergen}过敏" for allergen in missing_allergens)
    statement = choices[rng.randrange(len(choices))]
    return f"{_initial_context(persona)}另外，{statement}。"


def _multi_person_conflict(
    persona: HealthPersona,
) -> tuple[tuple[str, ...], MenuExpectation]:
    own_constraint = persona.allergens[0] if persona.allergens else "花生"
    other_constraint = "牛肉" if own_constraint != "牛肉" else "鸡蛋"
    message = (
        f"{_initial_context(persona)}"
        f"我需要避开{own_constraint}，另一位用餐者需要避开{other_constraint}，"
        "两人的约束不同，请先澄清这份菜单服务谁或是否同时满足。"
    )
    forbidden = tuple(persona.allergens) or (own_constraint,)
    return (
        (message,),
        MenuExpectation(
            dish_count=6,
            forbidden_terms=forbidden,
            clarification_required=True,
        ),
    )


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
            expectation = _base_expectation(persona)
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


def _operation_from_canonical_id(scenario: Scenario) -> str | None:
    operations = "|".join(re.escape(item) for item in DIALOGUE_OPERATIONS)
    pattern = (
        rf"seed-(?P<seed>[+-]?\d+)-index-\d{{4}}-"
        rf"{re.escape(scenario.intent)}-operation-(?P<operation>{operations})"
    )
    match = re.fullmatch(pattern, scenario.scenario_id)
    if match is None or match.group("seed") != str(scenario.seed):
        return None
    return match.group("operation")


def _operation_expectation_matches(operation: str, scenario: Scenario) -> bool:
    expectation = scenario.expectation
    if (
        expectation.dish_count != 6
        or not expectation.preserve_unaffected
        or not set(scenario.persona.allergens).issubset(
            expectation.forbidden_terms
        )
    ):
        return False
    if operation == "append_constraint":
        return bool(
            set(expectation.forbidden_terms) - set(scenario.persona.allergens)
        )
    if operation in {"request_structure_change", "confirm_clarification"}:
        return expectation.meat_count == 2 and expectation.vegetable_count == 4
    if operation == "ambiguous_change":
        return expectation.clarification_required
    return operation in {"retract_preference", "request_position_change"}


def _operation_from_scenario(scenario: Scenario) -> str | None:
    operation = _operation_from_canonical_id(scenario)
    if operation is None or scenario.dialogue_mode != "multi_turn":
        return None
    if scenario.intent == "relative_revision":
        if operation not in _RELATIVE_OPERATIONS:
            return None
    elif scenario.intent == "ambiguous_request":
        if operation != "ambiguous_change":
            return None
    else:
        return None
    if not operation_matches_plan(
        operation,
        scenario.messages,
    ) or not _operation_expectation_matches(operation, scenario):
        return None
    return operation


def validate_coverage(scenarios: Iterable[Scenario]) -> None:
    values = tuple(scenarios)
    present_buckets = {item.persona.primary_bucket for item in values}
    present_intents = {item.intent for item in values}
    present_dialogues = {item.dialogue_mode for item in values}
    present_operations = {
        operation
        for item in values
        if (operation := _operation_from_scenario(item)) is not None
    }
    missing_buckets = sorted(PRIMARY_BUCKETS - present_buckets)
    missing_intents = sorted(set(MANDATORY_INTENTS) - present_intents)
    missing_dialogues = sorted(DIALOGUE_MODES - present_dialogues)
    missing_operations = (
        [
            operation
            for operation in DIALOGUE_OPERATIONS
            if operation not in present_operations
        ]
        if len(values) >= MIN_FULL_OPERATION_COVERAGE
        else []
    )
    if missing_buckets or missing_intents or missing_dialogues or missing_operations:
        raise ValueError(
            "coverage validation failed: "
            f"missing primary_bucket={missing_buckets}; "
            f"missing intent={missing_intents}; "
            f"missing dialogue={missing_dialogues}; "
            f"missing operation={missing_operations}"
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
