SEAFOOD_TERMS = [
    "海鲜",
    "虾",
    "虾仁",
    "蟹",
    "螃蟹",
    "贝",
    "贝类",
    "蛤",
    "蛤蜊",
    "花蛤",
    "扇贝",
    "牡蛎",
    "生蚝",
    "海蛎",
    "海蛎子",
    "鱼",
    "鲈鱼",
    "带鱼",
    "鳕鱼",
]

TERM_ALIASES = {
    "海鲜": SEAFOOD_TERMS,
    "贝类": ["贝", "贝类", "蛤", "蛤蜊", "花蛤", "扇贝", "牡蛎", "生蚝", "海蛎", "海蛎子"],
    "海蛎子": ["海蛎子", "海蛎", "牡蛎", "生蚝"],
    "肉类": ["肉", "猪肉", "牛肉", "羊肉", "鸡肉", "鸭肉", "排骨", "肉末"],
    "肉": ["肉", "猪肉", "牛肉", "羊肉", "鸡肉", "鸭肉", "排骨", "肉末"],
    "蛋": ["蛋", "鸡蛋", "鸭蛋"],
    "鸡蛋": ["蛋", "鸡蛋"],
    "牛奶": ["牛奶", "奶", "奶油", "芝士", "奶酪"],
}


def expand_terms(values: list[str]) -> list[str]:
    expanded: list[str] = []
    for value in values:
        key = value.strip()
        if not key:
            continue
        terms = TERM_ALIASES.get(key, [key])
        for term in terms:
            if term and term not in expanded:
                expanded.append(term)
    return expanded


def contains_food_term(text: str, value: str) -> bool:
    return any(term in text for term in expand_terms([value]))
