from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


NUTRIENT_KEYS = (
    "kcal",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
    "fiber_g",
    "sugar_g",
    "sodium_mg",
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "nutrition"


@dataclass(frozen=True)
class NutritionFood:
    canonical_name: str
    source: str
    source_id: str
    nutrients_per_100g: dict[str, float]


class NutritionRepository:
    def __init__(
        self,
        foods_path: Path | None = None,
        aliases_path: Path | None = None,
    ) -> None:
        foods_path = foods_path or DATA_DIR / "foods.json"
        aliases_path = aliases_path or DATA_DIR / "aliases.json"
        raw_foods = json.loads(foods_path.read_text(encoding="utf-8"))
        self._foods: dict[str, NutritionFood] = {}
        for item in raw_foods:
            values = item["nutrients_per_100g"]
            if set(values) != set(NUTRIENT_KEYS):
                raise ValueError(f"invalid nutrient fields for {item['canonical_name']}")
            food = NutritionFood(
                canonical_name=item["canonical_name"],
                source=item["source"],
                source_id=item["source_id"],
                nutrients_per_100g={key: float(values[key]) for key in NUTRIENT_KEYS},
            )
            self._foods[food.canonical_name] = food
        self._aliases: dict[str, str] = json.loads(aliases_path.read_text(encoding="utf-8"))

    def get(self, canonical_name: str) -> NutritionFood | None:
        return self._foods.get(canonical_name.strip())

    def resolve(self, name: str) -> NutritionFood | None:
        clean_name = name.strip()
        canonical_name = self._aliases.get(clean_name, clean_name)
        return self.get(canonical_name)

    def __len__(self) -> int:
        return len(self._foods)


@lru_cache(maxsize=1)
def get_nutrition_repository() -> NutritionRepository:
    return NutritionRepository()
