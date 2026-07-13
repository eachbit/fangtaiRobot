from __future__ import annotations

from .models import Recipe


MEAT_TERMS = ["肉", "猪", "牛", "羊", "鸡", "鸭", "鱼", "虾", "蟹", "鲍", "排骨", "蹄筋"]
VEG_TERMS = ["菜", "蔬", "生菜", "芥蓝", "西兰花", "菠菜", "茄子", "土豆", "番茄", "豆腐", "菌", "菇"]
STAPLE_TERMS = ["饭", "面", "粥", "粉", "饼", "馒头", "包", "饺", "米"]
SOUP_TERMS = ["汤", "羹", "粥"]
DESSERT_TERMS = ["膏", "奶昔", "甜", "糖", "点心", "下午茶", "饮品"]
COLD_TERMS = ["凉", "拌", "沙拉"]


def recipe_text(recipe: Recipe) -> str:
    return f"{recipe.name} {recipe.ingredients} {' '.join(recipe.labels)}"


def classify_recipe(recipe: Recipe) -> str:
    text = recipe_text(recipe)
    if any(term in text for term in DESSERT_TERMS):
        return "dessert"
    if any(term in text for term in SOUP_TERMS):
        return "soup"
    if any(term in text for term in STAPLE_TERMS):
        return "staple"
    if any(term in text for term in MEAT_TERMS):
        return "meat"
    if any(term in text for term in VEG_TERMS):
        return "vegetable"
    return "other"


def is_breakfast_friendly(recipe: Recipe) -> bool:
    text = recipe_text(recipe)
    category = classify_recipe(recipe)
    if "早餐" in recipe.labels:
        return True
    if category in {"staple", "soup"}:
        return True
    if any(term in text for term in ["奶昔", "粥", "面", "蛋", "饼", "包"]):
        return True
    return False


def is_bad_breakfast(recipe: Recipe) -> bool:
    text = recipe_text(recipe)
    return any(term in text for term in ["猪排", "烧烤", "牛筋", "肥肠", "辣椒粉"])


def is_cold_dish(recipe: Recipe) -> bool:
    return any(term in recipe_text(recipe) for term in COLD_TERMS)
