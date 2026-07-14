from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import random

from app.data_loader import load_users
from app.models import HealthMetrics, UserProfile
from tests.evaluation.schemas import CheckupMetrics, HealthPersona


CONDITION_GROUPS = frozenset({"高血压", "高血糖", "高尿酸"})
SPECIAL_GROUPS = frozenset({"孕妇", "备孕", "哺乳期"})
_BUCKETS = (
    "healthy",
    "single_condition",
    "multi_condition",
    "special_group",
    "high_risk",
)
_RATIOS = (20, 25, 30, 15, 10)
ALLERGEN_OPTIONS = ("花生", "牛奶", "虾", "鸡蛋", "坚果")
_SYNTHETIC_ALLERGENS = ((), ("花生",), ("牛奶",), ("虾",), ("鸡蛋",), ("坚果",))
_SYNTHETIC_GOALS = (
    ("均衡营养",),
    ("补充蛋白质", "提高耐力"),
    ("控制体重", "规律饮食"),
    ("补钙", "补铁"),
    ("改善饮食结构",),
)


@dataclass(frozen=True)
class PersonaDisclosure:
    persona_id: str
    message: str
    negative_expression: bool


def classify_primary_bucket(user: UserProfile) -> str:
    groups = set(user.special_groups)
    if groups & SPECIAL_GROUPS:
        return "special_group"
    condition_count = len(groups & CONDITION_GROUPS)
    if condition_count >= 2:
        return "multi_condition"
    if condition_count == 1:
        return "single_condition"
    return "healthy"


def _checkup_from_metrics(metrics: HealthMetrics | None) -> CheckupMetrics | None:
    if metrics is None:
        return None
    return CheckupMetrics(
        fasting_glucose_mmol_l=metrics.fasting_glucose_mmol_l,
        systolic_blood_pressure_mm_hg=metrics.systolic_blood_pressure_mm_hg,
        diastolic_blood_pressure_mm_hg=metrics.diastolic_blood_pressure_mm_hg,
        total_cholesterol_mmol_l=metrics.total_cholesterol_mmol_l,
        triglycerides_mmol_l=metrics.triglycerides_mmol_l,
        ldl_mmol_l=metrics.ldl_mmol_l,
        hdl_mmol_l=metrics.hdl_mmol_l,
        uric_acid_umol_l=metrics.uric_acid_umol_l,
    )


def persona_from_user(user: UserProfile) -> HealthPersona:
    return HealthPersona(
        persona_id=f"official-{user.id}",
        primary_bucket=classify_primary_bucket(user),
        source_user_id=user.id,
        gender=user.gender,
        age=user.age,
        labor_intensity=user.labor_intensity,
        pregnancy_week=user.pregnancy_week,
        taste_preference=user.taste_preference,
        special_groups=tuple(user.special_groups),
        allergens=tuple(user.allergens),
        health_goals=tuple(user.health_goals),
        height_cm=user.height_cm,
        weight_kg=user.weight_kg,
        bmi=user.bmi,
        checkup_metrics=_checkup_from_metrics(user.checkup_metrics),
    )


def _quotas(count: int) -> dict[str, int]:
    raw = [count * ratio / 100 for ratio in _RATIOS]
    quotas = [math.floor(value) for value in raw]
    remainder_order = sorted(
        range(len(_BUCKETS)),
        key=lambda index: (raw[index] - quotas[index], -index),
        reverse=True,
    )
    for index in remainder_order[: count - sum(quotas)]:
        quotas[index] += 1
    return dict(zip(_BUCKETS, quotas, strict=True))


def _synthetic_metrics(rng: random.Random, index: int) -> CheckupMetrics | None:
    if index % 2:
        return None
    diastolic = rng.randint(65, 95)
    return CheckupMetrics(
        fasting_glucose_mmol_l=round(rng.uniform(3.8, 7.8), 1),
        systolic_blood_pressure_mm_hg=diastolic + rng.randint(20, 45),
        diastolic_blood_pressure_mm_hg=diastolic,
        total_cholesterol_mmol_l=round(rng.uniform(3.2, 6.8), 1),
        triglycerides_mmol_l=round(rng.uniform(0.6, 2.5), 1),
        ldl_mmol_l=round(rng.uniform(1.6, 4.2), 1),
        hdl_mmol_l=round(rng.uniform(0.8, 2.0), 1),
        uric_acid_umol_l=rng.randint(180, 480),
    )


def _synthetic_groups(bucket: str, index: int) -> tuple[str, ...]:
    if bucket == "single_condition":
        conditions = tuple(sorted(CONDITION_GROUPS))
        return (conditions[index % len(conditions)],)
    if bucket == "multi_condition":
        combinations = (("高血压", "高血糖"), ("高血压", "高尿酸"), ("高血糖", "高尿酸"))
        return combinations[index % len(combinations)]
    if bucket == "special_group":
        special_groups = tuple(sorted(SPECIAL_GROUPS))
        return (special_groups[index % len(special_groups)],)
    return ()


def _synthetic_persona(rng: random.Random, bucket: str, index: int) -> HealthPersona:
    height_cm = round(rng.uniform(150.0, 190.0), 1)
    weight_kg = round(rng.uniform(45.0, 95.0), 1)
    groups = _synthetic_groups(bucket, index)
    is_pregnant = "孕妇" in groups
    return HealthPersona(
        persona_id=f"synthetic-{bucket}-{index:03d}",
        primary_bucket=bucket,
        gender="女" if is_pregnant or index % 2 else "男",
        age=rng.randint(22, 62),
        labor_intensity=("轻度", "中度", "重度")[index % 3],
        pregnancy_week=f"{rng.randint(8, 36)}周" if is_pregnant else None,
        taste_preference=("清淡", "少辣", "家常", "微辣")[index % 4],
        special_groups=groups,
        allergens=_SYNTHETIC_ALLERGENS[index % len(_SYNTHETIC_ALLERGENS)],
        health_goals=_SYNTHETIC_GOALS[index % len(_SYNTHETIC_GOALS)],
        height_cm=height_cm,
        weight_kg=weight_kg,
        bmi=round(weight_kg / (height_cm / 100) ** 2, 1),
        checkup_metrics=_synthetic_metrics(rng, index),
    )


def build_personas(seed: int, count: int) -> tuple[HealthPersona, ...]:
    if count < 10:
        raise ValueError("count must be at least 10")

    rng = random.Random(seed)
    official_by_bucket: dict[str, list[HealthPersona]] = defaultdict(list)
    for user in load_users():
        persona = persona_from_user(user)
        official_by_bucket[persona.primary_bucket].append(persona)

    personas: list[HealthPersona] = []
    synthetic_index = 1
    for bucket, quota in _quotas(count).items():
        available = official_by_bucket[bucket]
        selected = rng.sample(available, k=min(quota, len(available)))
        personas.extend(selected)
        for _ in range(quota - len(selected)):
            personas.append(_synthetic_persona(rng, bucket, synthetic_index))
            synthetic_index += 1
    return tuple(personas)


def _missing_condition(persona: HealthPersona) -> str | None:
    return next(
        (condition for condition in sorted(CONDITION_GROUPS) if condition not in persona.special_groups),
        None,
    )


def _missing_allergen(persona: HealthPersona) -> str | None:
    return next((allergen for allergen in ALLERGEN_OPTIONS if allergen not in persona.allergens), None)


def _positive_disclosure(persona: HealthPersona) -> str:
    groups = "、".join(persona.special_groups)
    goals = "、".join(persona.health_goals)
    if persona.primary_bucket == "healthy":
        return f"我目前以日常营养为主，想兼顾{goals}。"
    if persona.primary_bucket == "single_condition":
        return f"我平时会留意{groups}相关的饮食安排，也希望兼顾{goals}。"
    if persona.primary_bucket == "multi_condition":
        return f"我需要同时留意{groups}相关的饮食安排，目标是{goals}。"
    if persona.primary_bucket == "special_group":
        stage = f"，目前{persona.pregnancy_week}" if persona.pregnancy_week else ""
        return f"我现在处于{groups}{stage}这个阶段，饮食上想兼顾{goals}。"
    return "我有一些饮食注意事项希望先澄清，是否需要医生建议也请先说明。"


def build_disclosures(personas: tuple[HealthPersona, ...]) -> tuple[PersonaDisclosure, ...]:
    bucket_indexes: dict[str, int] = defaultdict(int)
    disclosures: list[PersonaDisclosure] = []
    for persona in personas:
        bucket_index = bucket_indexes[persona.primary_bucket]
        bucket_indexes[persona.primary_bucket] += 1
        negative = bucket_index % 5 == 0
        message = _positive_disclosure(persona)
        if negative:
            missing_condition = _missing_condition(persona)
            missing_allergen = _missing_allergen(persona)
            health_statement = (
                f"我没有{missing_condition}方面的困扰"
                if missing_condition is not None
                else "我没有补充其他健康情况"
            )
            allergen_statement = (
                f"也不对{missing_allergen}过敏"
                if missing_allergen is not None
                else "也没有其他食物过敏"
            )
            message += f"另外{health_statement}，{allergen_statement}。"
        disclosures.append(
            PersonaDisclosure(
                persona_id=persona.persona_id,
                message=message,
                negative_expression=negative,
            )
        )
    return tuple(disclosures)
