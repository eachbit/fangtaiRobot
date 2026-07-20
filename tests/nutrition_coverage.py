from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import get_recipes
from app.ingredient_parser import parse_ingredients
from app.nutrition_repository import get_nutrition_repository


def build_report() -> dict:
    repository = get_nutrition_repository()
    total = matched = weighted = explicit = 0
    missing: Counter[str] = Counter()
    for recipe in get_recipes():
        for ingredient in parse_ingredients(recipe.ingredients):
            total += 1
            food = repository.resolve(ingredient.canonical_name)
            if food:
                matched += 1
            elif ingredient.canonical_name:
                missing[ingredient.canonical_name] += 1
            if ingredient.grams is not None:
                weighted += 1
            if ingredient.amount_source == "explicit":
                explicit += 1
    return {
        "ingredient_count": total,
        "nutrition_match_coverage": matched / total if total else 0,
        "weight_coverage": weighted / total if total else 0,
        "explicit_weight_coverage": explicit / total if total else 0,
        "top_missing": missing.most_common(40),
    }


def main() -> None:
    report = build_report()
    print(f"ingredient_count={report['ingredient_count']}")
    print(f"nutrition_match_coverage={report['nutrition_match_coverage']:.2%}")
    print(f"weight_coverage={report['weight_coverage']:.2%}")
    print(f"explicit_weight_coverage={report['explicit_weight_coverage']:.2%}")
    print("top_missing:")
    for name, count in report["top_missing"]:
        print(f"  {count:4d} {name}")


if __name__ == "__main__":
    main()
