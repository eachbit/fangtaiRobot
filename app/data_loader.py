from __future__ import annotations

import csv
import json
import math
import re
from decimal import Decimal
from numbers import Real
from pathlib import Path

from .models import HealthMetrics, Recipe, UserProfile


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RECIPES_FILE = DATA_DIR / "recipes_sample_2000.csv"
USERS_FILE = DATA_DIR / "50个用户健康档案（脱敏）.json"
CASES_FILE = DATA_DIR / "对话用例.json"

GENDER_KEY = "性别"
AGE_KEY = "年龄"
LABOR_INTENSITY_KEY = "劳动强度"
SPECIAL_GROUPS_KEY = "特殊人群"
PREGNANCY_WEEK_KEY = "孕周期"
TASTE_PREFERENCE_KEY = "口味偏好"
ALLERGENS_KEY = "过敏食材"
HEALTH_GOALS_KEY = "健康需求"
HEIGHT_KEY = "身高_cm"
WEIGHT_KEY = "体重_kg"
BMI_KEY = "BMI"
CHECKUP_METRICS_KEY = "体检指标"
FASTING_GLUCOSE_KEY = "空腹血糖_mmol/L"
BLOOD_PRESSURE_KEY = "血压_mmHg"
TOTAL_CHOLESTEROL_KEY = "总胆固醇_mmol/L"
TRIGLYCERIDES_KEY = "甘油三酯_mmol/L"
LDL_KEY = "低密度脂蛋白_mmol/L"
HDL_KEY = "高密度脂蛋白_mmol/L"
URIC_ACID_KEY = "尿酸_umol/L"

USER_PROFILE_KEYS = {
    "id",
    GENDER_KEY,
    AGE_KEY,
    LABOR_INTENSITY_KEY,
    SPECIAL_GROUPS_KEY,
    PREGNANCY_WEEK_KEY,
    TASTE_PREFERENCE_KEY,
    ALLERGENS_KEY,
    HEALTH_GOALS_KEY,
    HEIGHT_KEY,
    WEIGHT_KEY,
    BMI_KEY,
    CHECKUP_METRICS_KEY,
}
CHECKUP_METRIC_KEYS = {
    FASTING_GLUCOSE_KEY,
    BLOOD_PRESSURE_KEY,
    TOTAL_CHOLESTEROL_KEY,
    TRIGLYCERIDES_KEY,
    LDL_KEY,
    HDL_KEY,
    URIC_ACID_KEY,
}
BLOOD_PRESSURE_PATTERN = re.compile(r"^(\d{2,3})/(\d{2,3})$")

HEIGHT_SANITY_MIN_CM = 50.0
HEIGHT_SANITY_MAX_CM = 250.0
WEIGHT_SANITY_MIN_KG = 10.0
WEIGHT_SANITY_MAX_KG = 500.0
BMI_SANITY_MIN = 5.0
BMI_SANITY_MAX = 100.0
CHECKUP_FLOAT_SANITY_MIN = 0.1
CHECKUP_FLOAT_SANITY_MAX = 100.0
SYSTOLIC_BLOOD_PRESSURE_SANITY_MIN_MM_HG = 40
SYSTOLIC_BLOOD_PRESSURE_SANITY_MAX_MM_HG = 300
DIASTOLIC_BLOOD_PRESSURE_SANITY_MIN_MM_HG = 20
DIASTOLIC_BLOOD_PRESSURE_SANITY_MAX_MM_HG = 200
URIC_ACID_SANITY_MIN_UMOL_L = 10
URIC_ACID_SANITY_MAX_UMOL_L = 2000


def split_labels(value: str) -> list[str]:
    return [part.strip() for part in value.replace("，", "、").split("、") if part.strip()]


def load_recipes(path: Path = RECIPES_FILE) -> list[Recipe]:
    recipes: list[Recipe] = []
    with path.open("r", encoding="gbk", newline="") as file:
        reader = csv.DictReader(file)
        for idx, row in enumerate(reader, start=1):
            recipes.append(
                Recipe(
                    id=idx,
                    name=(row.get("名称") or "").strip(),
                    ingredients=(row.get("食材清单") or "").strip(),
                    steps=(row.get("烹饪步骤") or "").strip(),
                    labels=split_labels(row.get("label") or ""),
                )
            )
    return recipes


def _invalid(subject: str, field: str, message: str) -> ValueError:
    return ValueError(f"{subject}: {field} {message}")


def _subject(index: int, item: dict | None = None) -> str:
    if item is not None and "id" in item:
        return f"user {item['id']} at index {index}"
    return f"user at index {index}"


def _positive_float(
    value: object,
    subject: str,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _invalid(subject, field, "must be a finite positive number")
    try:
        number = float(value)
    except OverflowError:
        raise _invalid(subject, field, "must be a finite positive number") from None
    if isinstance(value, int) and int(number) != value:
        raise _invalid(subject, field, "integer must be exactly representable as a float")
    if not math.isfinite(number) or number <= 0:
        raise _invalid(subject, field, "must be a finite positive number")
    if not minimum <= number <= maximum:
        raise _invalid(subject, field, "is outside the technical sanity range")
    return number


def _positive_int(
    value: object,
    subject: str,
    field: str,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid(subject, field, "must be a positive integer")
    if maximum is not None and value > maximum:
        raise _invalid(subject, field, "is outside the technical sanity range")
    return value


def _required_fields(item: dict, subject: str) -> None:
    missing = USER_PROFILE_KEYS - set(item)
    if missing:
        raise _invalid(subject, sorted(missing)[0], "is required")
    unknown = set(item) - USER_PROFILE_KEYS
    if unknown:
        raise _invalid(subject, sorted(unknown)[0], "is not a recognized field")


def _string(value: object, subject: str, field: str) -> str:
    if not isinstance(value, str):
        raise _invalid(subject, field, "must be a string")
    return value


def _string_list(value: object, subject: str, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise _invalid(subject, field, "must be a list of strings")
    return value


def _checkup_metrics(value: object, subject: str) -> HealthMetrics | None:
    if not isinstance(value, dict):
        raise _invalid(subject, CHECKUP_METRICS_KEY, "must be an object")
    if not value:
        return None
    if set(value) != CHECKUP_METRIC_KEYS:
        raise _invalid(subject, CHECKUP_METRICS_KEY, "must contain exactly the required fields")

    blood_pressure = value[BLOOD_PRESSURE_KEY]
    if not isinstance(blood_pressure, str):
        raise _invalid(subject, BLOOD_PRESSURE_KEY, "must use systolic/diastolic format")
    match = BLOOD_PRESSURE_PATTERN.fullmatch(blood_pressure)
    if match is None:
        raise _invalid(subject, BLOOD_PRESSURE_KEY, "must use systolic/diastolic format")
    systolic, diastolic = (int(part) for part in match.groups())
    if not SYSTOLIC_BLOOD_PRESSURE_SANITY_MIN_MM_HG <= systolic <= SYSTOLIC_BLOOD_PRESSURE_SANITY_MAX_MM_HG:
        raise _invalid(subject, BLOOD_PRESSURE_KEY, "is outside the technical sanity range")
    if not DIASTOLIC_BLOOD_PRESSURE_SANITY_MIN_MM_HG <= diastolic <= DIASTOLIC_BLOOD_PRESSURE_SANITY_MAX_MM_HG:
        raise _invalid(subject, BLOOD_PRESSURE_KEY, "is outside the technical sanity range")
    if systolic <= diastolic:
        raise _invalid(subject, BLOOD_PRESSURE_KEY, "must have systolic greater than diastolic")

    return HealthMetrics(
        fasting_glucose_mmol_l=_positive_float(
            value[FASTING_GLUCOSE_KEY], subject, FASTING_GLUCOSE_KEY, CHECKUP_FLOAT_SANITY_MIN, CHECKUP_FLOAT_SANITY_MAX
        ),
        systolic_blood_pressure_mm_hg=systolic,
        diastolic_blood_pressure_mm_hg=diastolic,
        total_cholesterol_mmol_l=_positive_float(
            value[TOTAL_CHOLESTEROL_KEY], subject, TOTAL_CHOLESTEROL_KEY, CHECKUP_FLOAT_SANITY_MIN, CHECKUP_FLOAT_SANITY_MAX
        ),
        triglycerides_mmol_l=_positive_float(
            value[TRIGLYCERIDES_KEY], subject, TRIGLYCERIDES_KEY, CHECKUP_FLOAT_SANITY_MIN, CHECKUP_FLOAT_SANITY_MAX
        ),
        ldl_mmol_l=_positive_float(value[LDL_KEY], subject, LDL_KEY, CHECKUP_FLOAT_SANITY_MIN, CHECKUP_FLOAT_SANITY_MAX),
        hdl_mmol_l=_positive_float(value[HDL_KEY], subject, HDL_KEY, CHECKUP_FLOAT_SANITY_MIN, CHECKUP_FLOAT_SANITY_MAX),
        uric_acid_umol_l=_positive_int(
            value[URIC_ACID_KEY],
            subject,
            URIC_ACID_KEY,
            URIC_ACID_SANITY_MIN_UMOL_L,
            URIC_ACID_SANITY_MAX_UMOL_L,
        ),
    )


def load_users(path: Path = USERS_FILE) -> list[UserProfile]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("users: top-level JSON must be a list")

    users: list[UserProfile] = []
    user_ids: set[int] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"user at index {index}: entry must be an object")
        subject = _subject(index, item)
        _required_fields(item, subject)

        user_id = _positive_int(item["id"], subject, "id")
        subject = _subject(index, {"id": user_id})
        if user_id in user_ids:
            raise _invalid(subject, "id", "must be unique")
        user_ids.add(user_id)

        gender = _string(item[GENDER_KEY], subject, GENDER_KEY)
        age = _positive_int(item[AGE_KEY], subject, AGE_KEY)
        labor_intensity = _string(item[LABOR_INTENSITY_KEY], subject, LABOR_INTENSITY_KEY)
        special_groups = _string_list(item[SPECIAL_GROUPS_KEY], subject, SPECIAL_GROUPS_KEY)
        pregnancy_week = item[PREGNANCY_WEEK_KEY]
        if pregnancy_week is not None:
            pregnancy_week = _string(pregnancy_week, subject, PREGNANCY_WEEK_KEY)
        taste_preference = _string(item[TASTE_PREFERENCE_KEY], subject, TASTE_PREFERENCE_KEY)
        allergens = _string_list(item[ALLERGENS_KEY], subject, ALLERGENS_KEY)
        health_goals = _string_list(item[HEALTH_GOALS_KEY], subject, HEALTH_GOALS_KEY)
        height_cm = _positive_float(item[HEIGHT_KEY], subject, HEIGHT_KEY, HEIGHT_SANITY_MIN_CM, HEIGHT_SANITY_MAX_CM)
        weight_kg = _positive_float(item[WEIGHT_KEY], subject, WEIGHT_KEY, WEIGHT_SANITY_MIN_KG, WEIGHT_SANITY_MAX_KG)
        bmi = _positive_float(item[BMI_KEY], subject, BMI_KEY, BMI_SANITY_MIN, BMI_SANITY_MAX)
        raw_bmi = weight_kg / (height_cm / 100) ** 2
        bmi_matches = math.isclose(raw_bmi, bmi, rel_tol=0.0, abs_tol=0.1)
        if not bmi_matches:
            decimal_height = Decimal(str(height_cm))
            decimal_raw_bmi = Decimal(str(weight_kg)) / (decimal_height / Decimal(100)) ** 2
            bmi_matches = abs(decimal_raw_bmi - Decimal(str(bmi))) <= Decimal("0.1")
        if not bmi_matches:
            raise _invalid(subject, BMI_KEY, "does not match height_cm and weight_kg")

        users.append(
            UserProfile(
                id=user_id,
                gender=gender,
                age=age,
                labor_intensity=labor_intensity,
                special_groups=special_groups,
                pregnancy_week=pregnancy_week,
                taste_preference=taste_preference,
                allergens=allergens,
                health_goals=health_goals,
                height_cm=height_cm,
                weight_kg=weight_kg,
                bmi=bmi,
                checkup_metrics=_checkup_metrics(item[CHECKUP_METRICS_KEY], subject),
                raw=item,
            )
        )
    return users


def load_dialog_cases(path: Path = CASES_FILE) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
