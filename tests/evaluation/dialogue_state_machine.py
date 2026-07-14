from __future__ import annotations

from dataclasses import dataclass
import random

from tests.evaluation.schemas import HealthPersona, MenuExpectation


DIALOGUE_OPERATIONS = (
    "append_constraint",
    "retract_preference",
    "request_position_change",
    "request_structure_change",
    "ambiguous_change",
    "confirm_clarification",
)

_EXTRA_RESTRICTIONS = ("香菜", "芹菜", "动物内脏", "油炸食品")
_METRIC_LABELS = (
    ("fasting_glucose_mmol_l", "空腹血糖"),
    ("systolic_blood_pressure_mm_hg", "收缩压"),
    ("diastolic_blood_pressure_mm_hg", "舒张压"),
    ("total_cholesterol_mmol_l", "总胆固醇"),
    ("triglycerides_mmol_l", "甘油三酯"),
    ("ldl_mmol_l", "低密度脂蛋白"),
    ("hdl_mmol_l", "高密度脂蛋白"),
    ("uric_acid_umol_l", "尿酸"),
)


@dataclass(frozen=True)
class DialogueOperation:
    name: str
    added_constraints: tuple[str, ...] = ()
    retracted_preferences: tuple[str, ...] = ()
    retracted_health_facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class DialoguePlan:
    persona: HealthPersona
    operation: DialogueOperation
    messages: tuple[str, ...]
    expectation: MenuExpectation
    first_stage_expectation: MenuExpectation


def persona_disclosure(persona: HealthPersona) -> str:
    groups = "、".join(persona.special_groups) or "无"
    allergens = "、".join(persona.allergens) or "无"
    goals = "、".join(persona.health_goals) or "均衡营养"
    details = [
        f"健康组：{persona.primary_bucket}",
        f"长期健康情况或特殊人群：{groups}",
        f"过敏：{allergens}",
        f"健康目标：{goals}",
    ]
    if persona.taste_preference:
        details.append(f"长期口味偏好：{persona.taste_preference}")
    if persona.bmi is not None:
        details.append(f"BMI：{persona.bmi}")
    if persona.checkup_metrics is not None:
        metrics = persona.checkup_metrics.to_dict()
        disclosed_metrics = [
            f"{label}：{metrics[field]}"
            for field, label in _METRIC_LABELS
            if metrics[field] is not None
        ]
        if disclosed_metrics:
            details.append("体检指标：" + "、".join(disclosed_metrics))
    return "我的长期信息保持不变：" + "；".join(details) + "。"


def _base_forbidden(persona: HealthPersona) -> tuple[str, ...]:
    return tuple(persona.allergens)


def _append_constraint(
    persona: HealthPersona,
    rng: random.Random,
) -> DialoguePlan:
    available = tuple(
        value for value in _EXTRA_RESTRICTIONS if value not in persona.allergens
    )
    added = available[rng.randrange(len(available))]
    expectation = MenuExpectation(
        forbidden_terms=(*_base_forbidden(persona), added),
        preserve_unaffected=True,
    )
    return DialoguePlan(
        persona=persona,
        operation=DialogueOperation("append_constraint", added_constraints=(added,)),
        messages=(
            persona_disclosure(persona),
            f"在原要求上新增忌口：不吃{added}，其他约束和未影响菜保留。",
        ),
        expectation=expectation,
        first_stage_expectation=expectation,
    )


def _retract_preference(persona: HealthPersona) -> DialoguePlan:
    preference = persona.taste_preference or "清淡"
    expectation = MenuExpectation(
        forbidden_terms=_base_forbidden(persona),
        preserve_unaffected=True,
    )
    return DialoguePlan(
        persona=persona,
        operation=DialogueOperation(
            "retract_preference",
            retracted_preferences=(preference,),
        ),
        messages=(
            persona_disclosure(persona),
            f"只撤回偏好：不再要求{preference}口味；过敏、长期健康情况和未影响菜继续保留。",
        ),
        expectation=expectation,
        first_stage_expectation=expectation,
    )


def _position_change(persona: HealthPersona) -> DialoguePlan:
    expectation = MenuExpectation(
        forbidden_terms=_base_forbidden(persona),
        preserve_unaffected=True,
    )
    return DialoguePlan(
        persona=persona,
        operation=DialogueOperation("request_position_change"),
        messages=(
            persona_disclosure(persona),
            "请只把第二道菜换掉，其他未影响菜和原约束全部保留。",
        ),
        expectation=expectation,
        first_stage_expectation=expectation,
    )


def _structure_change(persona: HealthPersona) -> DialoguePlan:
    expectation = MenuExpectation(
        dish_count=6,
        meat_count=2,
        vegetable_count=4,
        forbidden_terms=_base_forbidden(persona),
        preserve_unaffected=True,
    )
    return DialoguePlan(
        persona=persona,
        operation=DialogueOperation("request_structure_change"),
        messages=(
            persona_disclosure(persona),
            "请明确把六道菜调整为荤素一比二，也就是两荤四素，未影响菜和原约束保留。",
        ),
        expectation=expectation,
        first_stage_expectation=expectation,
    )


def _ambiguous_change(persona: HealthPersona) -> DialoguePlan:
    expectation = MenuExpectation(
        forbidden_terms=_base_forbidden(persona),
        clarification_required=True,
        preserve_unaffected=True,
    )
    return DialoguePlan(
        persona=persona,
        operation=DialogueOperation("ambiguous_change"),
        messages=(persona_disclosure(persona), "这份菜单调整一下，未说明部分和现有菜先保留。"),
        expectation=expectation,
        first_stage_expectation=expectation,
    )


def _confirm_clarification(persona: HealthPersona) -> DialoguePlan:
    first_stage = MenuExpectation(
        forbidden_terms=_base_forbidden(persona),
        clarification_required=True,
        preserve_unaffected=True,
    )
    expectation = MenuExpectation(
        dish_count=6,
        meat_count=2,
        vegetable_count=4,
        forbidden_terms=_base_forbidden(persona),
        preserve_unaffected=True,
    )
    return DialoguePlan(
        persona=persona,
        operation=DialogueOperation("confirm_clarification"),
        messages=(
            persona_disclosure(persona),
            "先把荤素结构调整一下。",
            "确认按荤素一比二执行：六道菜两荤四素，未影响菜和其他要求保留。",
        ),
        expectation=expectation,
        first_stage_expectation=first_stage,
    )


def build_dialogue_plan(
    persona: HealthPersona,
    operation: str,
    seed: int = 0,
) -> DialoguePlan:
    if operation not in DIALOGUE_OPERATIONS:
        raise ValueError(f"unknown dialogue operation {operation!r}")
    if operation == "append_constraint":
        return _append_constraint(persona, random.Random(seed))
    if operation == "retract_preference":
        return _retract_preference(persona)
    if operation == "request_position_change":
        return _position_change(persona)
    if operation == "request_structure_change":
        return _structure_change(persona)
    if operation == "ambiguous_change":
        return _ambiguous_change(persona)
    return _confirm_clarification(persona)
