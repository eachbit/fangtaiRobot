from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Recipe:
    id: int
    name: str
    ingredients: str
    steps: str
    labels: list[str]


@dataclass(frozen=True)
class HealthMetrics:
    fasting_glucose_mmol_l: float
    systolic_blood_pressure_mm_hg: int
    diastolic_blood_pressure_mm_hg: int
    total_cholesterol_mmol_l: float
    triglycerides_mmol_l: float
    ldl_mmol_l: float
    hdl_mmol_l: float
    uric_acid_umol_l: int


@dataclass(frozen=True)
class UserProfile:
    id: int
    gender: str
    age: int
    labor_intensity: str
    special_groups: list[str]
    pregnancy_week: str | None
    taste_preference: str
    allergens: list[str]
    health_goals: list[str]
    height_cm: float
    weight_kg: float
    bmi: float
    checkup_metrics: HealthMetrics | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Constraints:
    meal: str | None = None
    people_count: int | None = None
    requested_dish_count: int | None = None
    taste: str | None = None
    avoid_tastes: list[str] = field(default_factory=list)
    health_goals: list[str] = field(default_factory=list)
    allergens: list[str] = field(default_factory=list)
    preferred_ingredients: list[str] = field(default_factory=list)
    avoid_ingredients: list[str] = field(default_factory=list)
    max_minutes: int | None = None
    difficulty: str | None = None
    scene: str | None = None
    inferred_profile: dict[str, Any] = field(default_factory=dict)
    raw_messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "meal": self.meal,
            "people_count": self.people_count,
            "requested_dish_count": self.requested_dish_count,
            "taste": self.taste,
            "avoid_tastes": self.avoid_tastes,
            "health_goals": self.health_goals,
            "allergens": self.allergens,
            "preferred_ingredients": self.preferred_ingredients,
            "avoid_ingredients": self.avoid_ingredients,
            "max_minutes": self.max_minutes,
            "difficulty": self.difficulty,
            "scene": self.scene,
            "inferred_profile": self.inferred_profile,
        }
