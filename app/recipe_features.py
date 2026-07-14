from __future__ import annotations

from dataclasses import dataclass

from .models import Recipe


MEAT_TERMS = ["肉", "猪", "牛", "羊", "鸡", "鸭", "鱼", "虾", "蟹", "鲍", "排骨", "蹄筋"]
VEG_TERMS = ["菜", "蔬", "生菜", "芥蓝", "西兰花", "菠菜", "茄子", "土豆", "番茄", "豆腐", "菌", "菇"]
STAPLE_TERMS = ["饭", "面", "粥", "粉", "饼", "馒头", "包", "饺", "米"]
SOUP_TERMS = ["汤", "羹", "粥"]
DESSERT_TERMS = ["膏", "奶昔", "甜", "糖", "点心", "下午茶", "饮品"]
COLD_TERMS = ["凉", "拌", "沙拉"]

ANIMAL_INGREDIENT_TERMS = [
    "猪",
    "牛",
    "羊",
    "鸡",
    "鸭",
    "鹅",
    "鱼",
    "虾",
    "蟹",
    "鲍",
    "乳鸽",
    "鸽",
    "鹌鹑",
    "兔",
    "鹿",
    "排骨",
    "蹄筋",
    "火腿",
    "培根",
    "香肠",
    "腊肠",
    "海鲜",
    "海参",
    "贝类",
    "扇贝",
    "鲜贝",
    "贝肉",
    "蛤",
    "花甲",
    "蚝",
    "牡蛎",
    "蛏",
    "螺",
    "鱿",
    "鳝",
    "鳗",
    "肉",
]
PLANT_INGREDIENT_TERMS = [
    "菜",
    "蔬",
    "生菜",
    "芥蓝",
    "西兰花",
    "菠菜",
    "茄子",
    "土豆",
    "番茄",
    "豆腐",
    "豆干",
    "豆皮",
    "菌",
    "菇",
    "口蘑",
    "莴笋",
    "芦笋",
    "竹笋",
    "笋",
    "萝卜",
    "山药",
    "莲藕",
    "红薯",
    "地瓜",
    "芋头",
    "洋葱",
    "黄瓜",
    "冬瓜",
    "南瓜",
    "丝瓜",
    "苦瓜",
    "西葫芦",
    "彩椒",
    "青椒",
    "甜椒",
    "玉米",
    "豆角",
    "毛豆",
    "豌豆",
    "蚕豆",
    "扁豆",
    "四季豆",
    "荷兰豆",
    "芸豆",
    "豆芽",
    "豆苗",
]
NON_PROTEIN_ANIMAL_TERMS = [
    "鸡蛋",
    "蛋黄",
    "蛋白",
    "鱼露",
    "蚝油",
    "鸡精",
    "鸡粉",
    "海鲜酱",
    "鲍鱼汁",
    "鲍汁",
    "鱼香",
]
NON_ANIMAL_INGREDIENT_REPLACEMENTS = {
    "牛肝菌": "菌",
    "羊肚菌": "菌",
    "鸡枞菌": "菌",
}
COOKING_METHOD_TERMS = [
    ("凉拌", ("凉拌",)),
    ("蒸", ("蒸",)),
    ("炒", ("炒",)),
    ("炖", ("炖",)),
    ("煮", ("煮",)),
    ("炸", ("炸",)),
    ("烤", ("烧烤", "焗", "烤")),
    ("凉拌", ("拌", "沙拉")),
]
COLD_TEMPERATURE_TERMS = ["凉拌", "冷盘", "凉菜", "冷食", "冰镇", "沙拉"]


@dataclass(frozen=True)
class RecipeFeatures:
    category: str
    protein_style: str
    temperature: str
    cooking_method: str


def recipe_text(recipe: Recipe) -> str:
    return f"{recipe.name} {recipe.ingredients} {' '.join(recipe.labels)}"


def _legacy_category(recipe: Recipe) -> str:
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


def _protein_style(recipe: Recipe) -> str:
    ingredients = recipe.ingredients
    for term, replacement in NON_ANIMAL_INGREDIENT_REPLACEMENTS.items():
        ingredients = ingredients.replace(term, replacement)
    for term in NON_PROTEIN_ANIMAL_TERMS:
        ingredients = ingredients.replace(term, "")
    if any(term in ingredients for term in ANIMAL_INGREDIENT_TERMS):
        return "meat"
    if any(term in ingredients for term in PLANT_INGREDIENT_TERMS):
        return "vegetable"

    name = recipe.name
    for term, replacement in NON_ANIMAL_INGREDIENT_REPLACEMENTS.items():
        name = name.replace(term, replacement)
    for term in NON_PROTEIN_ANIMAL_TERMS:
        name = name.replace(term, "")
    if any(term in name for term in ANIMAL_INGREDIENT_TERMS):
        return "meat"
    if any(term in name for term in PLANT_INGREDIENT_TERMS):
        return "vegetable"
    return "other"


def _cooking_method(text: str) -> str:
    for method, terms in COOKING_METHOD_TERMS:
        if any(term in text for term in terms):
            return method
    return "unknown"


def analyze_recipe(recipe: Recipe) -> RecipeFeatures:
    cooking_method = _cooking_method(recipe.name)
    if cooking_method == "unknown":
        cooking_method = _cooking_method(recipe.steps)

    if any(term in recipe.name for term in COLD_TEMPERATURE_TERMS):
        temperature = "cold"
    elif cooking_method == "凉拌":
        temperature = "cold"
    elif cooking_method in {"蒸", "炒", "炖", "煮", "炸", "烤"}:
        temperature = "hot"
    else:
        temperature = "unknown"

    return RecipeFeatures(
        category=_legacy_category(recipe),
        protein_style=_protein_style(recipe),
        temperature=temperature,
        cooking_method=cooking_method,
    )


def classify_recipe(recipe: Recipe) -> str:
    return analyze_recipe(recipe).category


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
