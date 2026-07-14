from __future__ import annotations

from dataclasses import dataclass
import random
from typing import TypeAlias


GroundTruthValue: TypeAlias = str | int | bool

LANGUAGE_VARIANT_VERSION = "1.0"


@dataclass(frozen=True)
class LanguageVariant:
    text: str
    expected_intent: str
    variant_id: str
    family_id: str
    ground_truth: tuple[tuple[str, GroundTruthValue], ...] = ()


LANGUAGE_VARIANTS = tuple(
    sorted(
        (
            LanguageVariant(
                "不要放花生",
                "hard_constraint",
                "hard-001",
                "forbidden-ingredient",
                (("forbidden_term", "花生"),),
            ),
            LanguageVariant(
                "我对花生过敏，菜单里避开花生",
                "hard_constraint",
                "hard-002",
                "forbidden-ingredient",
                (("forbidden_term", "花生"),),
            ),
            LanguageVariant(
                "请结合我刚才说的健康情况安排",
                "health_profile",
                "health-001",
                "health-context",
            ),
            LanguageVariant(
                "多来几个素菜",
                "structure_ratio",
                "ratio-001",
                "more-vegetables",
                (("direction", "more_vegetable"), ("preserve_unaffected", True)),
            ),
            LanguageVariant(
                "少整点荤的",
                "structure_ratio",
                "ratio-002",
                "more-vegetables",
                (("direction", "more_vegetable"), ("preserve_unaffected", True)),
            ),
            LanguageVariant(
                "肉菜太多了",
                "structure_ratio",
                "ratio-003",
                "more-vegetables",
                (("direction", "more_vegetable"), ("preserve_unaffected", True)),
            ),
            LanguageVariant(
                "荤素一比二",
                "structure_ratio",
                "ratio-004",
                "one-to-two-ratio",
                (("meat_count", 2), ("vegetable_count", 4)),
            ),
            LanguageVariant(
                "不要把素菜换掉",
                "relative_revision",
                "revision-001",
                "preserve-vegetables",
                (("preserve_vegetables", True),),
            ),
            LanguageVariant(
                "只换第二道，其他菜保留",
                "relative_revision",
                "revision-002",
                "position-change",
                (("position", 2), ("preserve_unaffected", True)),
            ),
            LanguageVariant(
                "别全是蒸的",
                "cooking_diversity",
                "cooking-001",
                "cooking-diversity",
                (("minimum_cooking_methods", 3),),
            ),
            LanguageVariant(
                "六道菜至少用三种做法",
                "cooking_diversity",
                "cooking-002",
                "cooking-diversity",
                (("minimum_cooking_methods", 3),),
            ),
            LanguageVariant(
                "在控制能量和保证蛋白质之间帮我平衡",
                "nutrition_tradeoff",
                "nutrition-001",
                "nutrition-tradeoff",
            ),
            LanguageVariant(
                "这份菜单调整一下",
                "ambiguous_request",
                "ambiguous-001",
                "ambiguous-change",
                (("clarification_required", True),),
            ),
            LanguageVariant(
                "我没有高血压",
                "negative_expression",
                "negative-001",
                "negative-health",
                (("absent_condition", "高血压"),),
            ),
            LanguageVariant(
                "我不对花生过敏",
                "negative_expression",
                "negative-002",
                "negative-allergen",
                (("absent_allergen", "花生"),),
            ),
            LanguageVariant(
                "我和另一位用餐者要求不同，请先确认按谁的限制安排",
                "multi_person_conflict",
                "people-001",
                "multi-person-conflict",
                (("clarification_required", True),),
            ),
        ),
        key=lambda item: item.variant_id,
    )
)

_KNOWN_INTENTS = frozenset(item.expected_intent for item in LANGUAGE_VARIANTS)


def variants_for_intent(intent: str) -> tuple[LanguageVariant, ...]:
    if intent not in _KNOWN_INTENTS:
        raise ValueError(f"unknown intent {intent!r}")
    return tuple(
        item for item in LANGUAGE_VARIANTS if item.expected_intent == intent
    )


def select_variant(intent: str, seed: int, slot: int = 0) -> LanguageVariant:
    variants = variants_for_intent(intent)
    rng = random.Random(f"{LANGUAGE_VARIANT_VERSION}:{seed}:{slot}:{intent}")
    return variants[rng.randrange(len(variants))]
