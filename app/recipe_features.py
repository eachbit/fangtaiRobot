from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Recipe


MEAT_TERMS = ["肉", "猪", "牛", "羊", "鸡", "鸭", "鱼", "虾", "蟹", "鲍", "排骨", "蹄筋"]
VEG_TERMS = ["菜", "蔬", "生菜", "芥蓝", "西兰花", "菠菜", "茄子", "土豆", "番茄", "豆腐", "菌", "菇"]
STAPLE_TERMS = ["饭", "面", "粥", "粉", "饼", "馒头", "包", "饺", "米"]
SOUP_TERMS = ["汤", "羹", "粥"]
DESSERT_TERMS = ["膏", "奶昔", "甜", "糖", "点心", "下午茶", "饮品"]
COLD_TERMS = ["凉", "拌", "沙拉"]

ANIMAL_INGREDIENT_TERMS = [
    "猪肉",
    "猪里脊",
    "猪排",
    "猪蹄",
    "猪手",
    "猪肋排",
    "猪肚",
    "猪肝",
    "猪腰",
    "猪耳",
    "猪舌",
    "猪心",
    "猪血",
    "猪骨",
    "猪尾",
    "五花肉",
    "梅花肉",
    "里脊肉",
    "瘦肉",
    "肥肉",
    "肉末",
    "肉馅",
    "肉片",
    "肉丝",
    "肉松",
    "肉糜",
    "肉夹馍",
    "咸肉",
    "腊肉",
    "叉烧",
    "排骨",
    "蹄筋",
    "肥肠",
    "火腿",
    "培根",
    "香肠",
    "腊肠",
    "午餐肉",
    "牛肉",
    "牛腩",
    "牛排",
    "牛筋",
    "牛蹄筋",
    "牛肚",
    "牛百叶",
    "牛尾",
    "牛舌",
    "牛肝",
    "牛骨",
    "肥牛",
    "羊肉",
    "羊排",
    "羊腿",
    "羊蝎子",
    "羊肚",
    "羊杂",
    "鸡肉",
    "鸡胸",
    "鸡腿",
    "鸡翅",
    "鸡爪",
    "凤爪",
    "鸡胗",
    "鸡肝",
    "鸡心",
    "鸡汤",
    "鸡骨",
    "整鸡",
    "仔鸡",
    "童子鸡",
    "乌鸡",
    "土鸡",
    "三黄鸡",
    "柴鸡",
    "鸡块",
    "鸡丝",
    "鸡丁",
    "鸭肉",
    "鸭翅",
    "鸭腿",
    "鸭掌",
    "鸭脖",
    "鸭血",
    "鸭胗",
    "鸭肝",
    "鸭胸",
    "鸭舌",
    "烤鸭",
    "卤鸭",
    "板鸭",
    "整鸭",
    "老鸭",
    "鹅肉",
    "鹅掌",
    "鹅肝",
    "鹅翅",
    "鹅腿",
    "烧鹅",
    "整鹅",
    "兔肉",
    "兔腿",
    "鹿肉",
    "鹌鹑",
    "乳鸽",
    "鸽肉",
    "鸽子",
    "鱼肉",
    "鱼片",
    "鱼块",
    "鱼柳",
    "鱼排",
    "鱼头",
    "鱼尾",
    "鱼丸",
    "鱼籽",
    "鱼子",
    "鲈鱼",
    "鲫鱼",
    "鲤鱼",
    "草鱼",
    "鳕鱼",
    "带鱼",
    "鲳鱼",
    "桂鱼",
    "鳜鱼",
    "三文鱼",
    "龙利鱼",
    "巴沙鱼",
    "黄花鱼",
    "鲶鱼",
    "黑鱼",
    "青鱼",
    "鲢鱼",
    "鳙鱼",
    "罗非鱼",
    "银鱼",
    "鳗鱼",
    "鳝鱼",
    "黄鳝",
    "泥鳅",
    "鲑鱼",
    "金枪鱼",
    "沙丁鱼",
    "秋刀鱼",
    "多宝鱼",
    "虾仁",
    "虾肉",
    "明虾",
    "对虾",
    "河虾",
    "海虾",
    "基围虾",
    "龙虾",
    "草虾",
    "白虾",
    "青虾",
    "大虾",
    "鲜虾",
    "虾米",
    "虾皮",
    "虾头",
    "虾尾",
    "蟹肉",
    "螃蟹",
    "大闸蟹",
    "河蟹",
    "海蟹",
    "梭子蟹",
    "青蟹",
    "毛蟹",
    "蟹柳",
    "海鲜",
    "海参",
    "贝类",
    "扇贝",
    "鲜贝",
    "贝肉",
    "干贝",
    "蛤蜊",
    "花蛤",
    "蛤肉",
    "花甲",
    "生蚝",
    "海蚝",
    "牡蛎",
    "蛏子",
    "海螺",
    "田螺",
    "螺肉",
    "鲍鱼",
    "鱿鱼",
    "章鱼",
    "墨鱼",
    "乌贼",
    "八爪鱼",
]
STANDALONE_ANIMAL_INGREDIENT_PATTERN = re.compile(
    r"^(?:新鲜|鲜活|鲜|活)?(?:整只|整条|整)?"
    r"(?:猪|牛|羊|鸡|鸭|鹅|鱼|虾|蟹|贝|蛤|鲍|蚝|兔|鸽|肉)"
    r"(?=$|\s|[\d一二两三四五六七八九十半]|克|千克|公斤|斤|只|条|块|份)"
)
NAMED_ANIMAL_INGREDIENT_PATTERN = re.compile(
    r"^[\u4e00-\u9fff]{1,6}(?:鸡|鸭|鹅|鱼|虾|蟹|贝|蛤|蚝|鸽)"
    r"(?=$|\s|[\d一二两三四五六七八九十半]|克|千克|公斤|斤|只|条|块|份)"
)
LIVESTOCK_CUT_INGREDIENT_PATTERN = re.compile(
    r"^(?:猪|牛|羊)[\u4e00-\u9fff]{0,3}"
    r"(?:肉|排|肋排|肘子?|蹄|肚|肝|腰|耳|舌|心|血|骨|尾|筋|腩|百叶|油渣?|油)"
    r"(?=$|\s|[\d一二两三四五六七八九十半]|克|千克|公斤|斤|只|条|块|份)"
)
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
    "腐竹",
    "千张",
    "豆泡",
    "豆制品",
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
    "牛油果",
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
    "鸡毛菜": "菜",
    "牛蒡": "菜",
    "鱼腥草": "菜",
    "鸡腿菇": "菇",
    "猪肚菇": "菇",
    "蟹味菇": "菇",
    "海鲜菇": "菇",
    "鲍鱼菇": "菇",
    "素鸡": "豆制品",
}
COOKING_METHOD_TERMS = [
    ("凉拌", ("凉拌", "冷拌", "沙拉")),
    ("蒸", ("蒸",)),
    ("炒", ("炒",)),
    ("炖", ("炖",)),
    ("煮", ("煮",)),
    ("炸", ("炸",)),
    ("烤", ("烧烤", "焗", "烤")),
]
COOKING_DEVICE_TERMS = ["蒸烤箱", "蒸烤架", "蒸烤盘"]
COLD_TEMPERATURE_TERMS = ["凉拌", "冷拌", "冷盘", "凉菜", "冷食", "冰镇", "沙拉"]


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
    if _has_animal_ingredient(ingredients, allow_standalone=True):
        return "meat"
    if any(term in ingredients for term in PLANT_INGREDIENT_TERMS):
        return "vegetable"

    name = recipe.name
    for term, replacement in NON_ANIMAL_INGREDIENT_REPLACEMENTS.items():
        name = name.replace(term, replacement)
    for term in NON_PROTEIN_ANIMAL_TERMS:
        name = name.replace(term, "")
    if _has_animal_ingredient(name, allow_standalone=False):
        return "meat"
    if any(term in name for term in PLANT_INGREDIENT_TERMS):
        return "vegetable"
    return "other"


def _has_animal_ingredient(text: str, allow_standalone: bool) -> bool:
    if any(term in text for term in ANIMAL_INGREDIENT_TERMS):
        return True
    if not allow_standalone:
        return False
    items = re.split(r"[；;，,、\n]+", text)
    for item in items:
        ingredient = re.split(r"[:：]", item)[-1].strip()
        if any(
            pattern.search(ingredient)
            for pattern in (
                STANDALONE_ANIMAL_INGREDIENT_PATTERN,
                NAMED_ANIMAL_INGREDIENT_PATTERN,
                LIVESTOCK_CUT_INGREDIENT_PATTERN,
            )
        ):
            return True
    return False


def _cooking_method(text: str) -> str:
    normalized = text
    for term in COOKING_DEVICE_TERMS:
        normalized = normalized.replace(term, "")

    matches: list[tuple[int, str]] = []
    for method, terms in COOKING_METHOD_TERMS:
        for term in terms:
            position = normalized.rfind(term)
            if position >= 0:
                matches.append((position, method))
    return max(matches, default=(-1, "unknown"))[1]


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
