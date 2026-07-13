from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import Recipe, UserProfile


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
RECIPES_FILE = DATA_DIR / "recipes_sample_2000.csv"
USERS_FILE = DATA_DIR / "50个用户健康档案（脱敏）.json"
CASES_FILE = DATA_DIR / "对话用例.json"


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


def load_users(path: Path = USERS_FILE) -> list[UserProfile]:
    data = json.loads(path.read_text(encoding="utf-8"))
    users: list[UserProfile] = []
    for item in data:
        users.append(
            UserProfile(
                id=int(item["id"]),
                gender=item.get("性别", ""),
                age=int(item.get("年龄", 0)),
                labor_intensity=item.get("劳动强度", ""),
                special_groups=list(item.get("特殊人群") or []),
                pregnancy_week=item.get("孕周期"),
                taste_preference=item.get("口味偏好", ""),
                allergens=list(item.get("过敏食材") or []),
                health_goals=list(item.get("健康需求") or []),
                raw=item,
            )
        )
    return users


def load_dialog_cases(path: Path = CASES_FILE) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
