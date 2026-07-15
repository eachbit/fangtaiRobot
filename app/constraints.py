from __future__ import annotations

import re

from .food_terms import contains_food_term, expand_terms
from .models import Constraints, UserProfile


TASTE_KEYWORDS = {
    "清淡": ["清淡", "清爽", "不油", "少油"],
    "偏辣": ["辣", "重口", "下饭"],
    "酸甜": ["酸甜", "酸一点", "甜一点"],
    "甜": ["甜", "甜口"],
    "咸香": ["咸香", "咸口"],
}

HEALTH_KEYWORDS = {
    "减脂": ["减脂", "减肥", "低脂", "控制体重"],
    "增肌": ["增肌", "高蛋白"],
    "补钙": ["补钙"],
    "补铁": ["补铁", "补气血", "气血"],
    "控糖": ["控糖", "少糖"],
    "降压": ["降压"],
    "降尿酸": ["降尿酸"],
    "健胃消食": ["胃口不好", "养胃", "健胃", "消食"],
}

MEAL_KEYWORDS = {
    "早餐": ["早餐", "早饭", "早上"],
    "午餐": ["午餐", "午饭", "中午", "带去公司"],
    "晚餐": ["晚餐", "晚饭", "晚上", "今晚"],
    "夜宵": ["夜宵", "晚上有点饿"],
}

INGREDIENT_KEYWORDS = [
    "番茄",
    "鸡蛋",
    "土豆",
    "面",
    "鱼",
    "鸡翅",
    "牛肉",
    "虾",
    "豆腐",
    "鸡肉",
]

ALLERGEN_PATTERNS = [
    "海鲜",
    "贝类",
    "海蛎子",
    "花生",
    "牛奶",
    "鸡蛋",
    "虾",
    "蟹",
    "坚果",
    "芒果",
    "羊肉",
]

SPECIAL_GROUP_KEYWORDS = {
    "高血压": ["高血压", "血压高"],
    "高血糖": ["高血糖", "血糖高", "糖尿病"],
    "高尿酸": ["高尿酸", "尿酸高", "痛风"],
    "高血脂": ["高血脂", "血脂高"],
    "肾功能异常": ["肾功能异常"],
    "孕妇": ["孕妇", "怀孕", "孕期"],
    "备孕": ["备孕"],
    "哺乳期": ["哺乳", "哺乳期"],
    "老人": ["老人", "老年", "爸妈", "父母"],
    "儿童": ["儿童"],
}

_LIST_SEPARATOR = re.compile(r"[、，,/]|(?:和|及)")
_NEGATION_SCOPE = re.compile(
    r"(?:没有|不是|并非|不属于|未患|无|不算|不需要|不要)"
    r"(?:患有|得了|被诊断出|被诊断为|诊断出|诊断为|有|需要|"
    r"误加|添加|标记|认为|当作)?"
    r"[^；;。\n]{0,40}$"
)
_DISCLOSURE_FOLLOWUP = (
    r"(?:最近|目前|平时|同时|另外|并且|需要|想|请|空腹|血压|体检|口味|"
    r"年龄|预算|晚餐|午餐|早餐|推荐|安排|但|不过|然后|"
    r"(?:饮食)?目标(?:是|为))"
)
_EXPLICIT_HEALTH_PATTERN = re.compile(
    r"(?:我目前的|我的|目前的)?健康情况(?:是|为)\s*(.+?)"
    rf"(?=；|;|。|\n|\s+(?=(?:饮食)?目标(?:是|为))|"
    rf"，(?={_DISCLOSURE_FOLLOWUP})|$)"
)
_EXPLICIT_GOAL_PATTERN = re.compile(
    r"(?:饮食)?目标(?:是|为)\s*(.+?)"
    rf"(?=；|;|。|\n|，(?={_DISCLOSURE_FOLLOWUP})|$)"
)
_ADDITIVE_DISCLOSURE_MARKERS = ("另外", "再加", "补充", "同时", "还有", "并且")
_NARRATIVE_ITEM_PREFIXES = (
    "最近",
    "目前",
    "平时",
    "年龄",
    "预算",
    "晚餐",
    "午餐",
    "早餐",
    "推荐",
    "安排",
    "请",
    "需要",
    "想",
    "口味",
    "体检",
    "空腹",
)
_CONTRAST_BOUNDARY = re.compile(r"(?:但是|但|不过(?!敏)|然而|而是|后来)")
_DISH_INCREMENT_PATTERN = re.compile(
    r"(?:再|另外|只)?(?:多)?加\s*([一二两三四五六七八九十\d]+)\s*道"
    r"(?:素菜|荤菜|菜)?"
)
_DISH_ABSOLUTE_PATTERN = re.compile(
    r"(?:"
    r"(?:推荐|安排|来|做|给我|改成|想要|吃|先给|先来)\s*"
    r"([一二两三四五六七八九十\d]+)\s*道(?:菜|餐|饭)?"
    r"|"
    r"([一二两三四五六七八九十\d]+)\s*道"
    r"(?:菜|餐|饭|[\u4e00-\u9fa5]{1,10}菜)"
    r")"
)
_DISH_REFERENCE_SUFFIX = re.compile(
    r"(?:其他|其余|剩余|原来(?:的)?|保留的?|未影响的?|混成|合成|"
    r"前|后|这|那|上述|第)\s*$"
)


def _contains_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _split_disclosed_values(value: str) -> list[str]:
    values: list[str] = []
    for item in _LIST_SEPARATOR.split(value):
        cleaned = item.strip(" ：:，,。；;\t")
        if not cleaned:
            continue
        if cleaned.startswith(_NARRATIVE_ITEM_PREFIXES):
            break
        if re.search(r"\d+(?:岁|元|分钟|道菜|mmol|mmHg)", cleaned, re.IGNORECASE):
            break
        values.append(cleaned)
    return _dedupe(values)


def _extract_disclosed_values(text: str, pattern: re.Pattern[str]) -> list[str]:
    matches = list(pattern.finditer(text))
    values: list[str] = []
    for match in matches:
        disclosed = _split_disclosed_values(match.group(1))
        prefix = text[max(0, match.start() - 12):match.start()]
        if not values or any(
            marker in prefix for marker in _ADDITIVE_DISCLOSURE_MARKERS
        ):
            values.extend(disclosed)
            values = _dedupe(values)
        else:
            values = disclosed
    return values


def _contains_non_negated(text: str, keyword: str) -> bool:
    for match in re.finditer(re.escape(keyword), text):
        sentence_start = max(
            text.rfind(separator, 0, match.start())
            for separator in ("；", ";", "。", "\n")
        )
        prefix = text[sentence_start + 1:match.start()]
        contrasts = list(_CONTRAST_BOUNDARY.finditer(prefix))
        if contrasts:
            prefix = prefix[contrasts[-1].end():]
        if not _NEGATION_SCOPE.search(prefix):
            return True
    return False


def _extract_allergens(text: str) -> list[str]:
    allergens: list[str] = []
    clauses = re.split(
        r"[，,；;。\n]|(?:但是|但|不过(?!敏)|然而|而是)",
        text,
    )
    for clause in clauses:
        clause = clause.strip()
        if not clause or any(
            phrase in clause
            for phrase in ("没有已知食物过敏", "没有食物过敏", "无食物过敏")
        ):
            continue

        accepted_positive = False
        positive_matches = list(re.finditer(r"(?<!不)对(.+?)过敏", clause))
        for match in positive_matches:
            raw = match.group(1)
            if re.search(r"不(?:太)?$", raw):
                continue
            raw = re.sub(r"^(?:我|本人)?对?", "", raw).strip()
            allergens.extend(_split_disclosed_values(raw))
            accepted_positive = True

        if not accepted_positive and not positive_matches and clause.endswith("过敏"):
            raw_direct = clause[:-2]
            raw_direct = re.split(r"(?:并且|且)", raw_direct)[-1]
            raw_direct = re.sub(r"^(?:我|本人)?对?", "", raw_direct).strip()
            if raw_direct and not any(
                marker in raw_direct
                for marker in ("不对", "没有", "并非", "不是", "无")
            ) and not re.search(r"不(?:太)?$", raw_direct):
                allergens.extend(_split_disclosed_values(raw_direct))

        for allergen in ALLERGEN_PATTERNS:
            if f"不能吃{allergen}" in clause or f"不吃{allergen}" in clause:
                allergens.append(allergen)
    return _dedupe(allergens)


def _extract_requested_dish_count(text: str) -> int | None:
    increments = list(_DISH_INCREMENT_PATTERN.finditer(text))
    increment_spans = [(match.start(), match.end()) for match in increments]
    events: list[tuple[int, str, int | None]] = [
        (match.start(), "increment", _parse_chinese_number(match.group(1)))
        for match in increments
    ]
    for match in _DISH_ABSOLUTE_PATTERN.finditer(text):
        if any(start <= match.start() < end for start, end in increment_spans):
            continue
        prefix = text[max(0, match.start() - 10):match.start()]
        if _DISH_REFERENCE_SUFFIX.search(prefix):
            continue
        value = match.group(1) or match.group(2)
        events.append(
            (match.start(), "absolute", _parse_chinese_number(value))
        )

    requested: int | None = None
    for _, event_type, value in sorted(events):
        if value is None:
            continue
        if event_type == "absolute":
            requested = value
        elif requested is not None:
            requested += value
    return requested


def extract_constraints(messages: list[str], user: UserProfile | None = None) -> Constraints:
    text = "\n".join(messages)
    constraints = Constraints(raw_messages=messages)
    inferred_profile = infer_profile_from_text(text)
    constraints.inferred_profile = inferred_profile

    if user:
        constraints.taste = user.taste_preference or None
        constraints.health_goals.extend(user.health_goals)
        constraints.allergens.extend(user.allergens)

    if inferred_profile.get("taste_preference"):
        constraints.taste = inferred_profile["taste_preference"]
    constraints.health_goals.extend(inferred_profile.get("health_goals", []))
    constraints.allergens.extend(inferred_profile.get("allergens", []))

    for meal, keywords in MEAL_KEYWORDS.items():
        if _contains_any(text, keywords):
            constraints.meal = meal
            break

    people_match = re.search(r"([一二两三四五六七八九十\d]+)\s*个?人", text)
    if people_match:
        constraints.people_count = _parse_chinese_number(people_match.group(1))
    elif "一家四口" in text:
        constraints.people_count = 4

    constraints.requested_dish_count = _extract_requested_dish_count(text)

    constraints.avoid_tastes.extend(_extract_avoid_tastes(text))

    for taste, keywords in TASTE_KEYWORDS.items():
        if taste == "偏辣" and "辣" in constraints.avoid_tastes:
            continue
        if taste in ("甜", "酸甜") and "甜" in constraints.avoid_tastes:
            continue
        if _contains_any(text, keywords):
            constraints.taste = taste

    if "辣" in constraints.avoid_tastes and constraints.taste == "偏辣":
        constraints.taste = "清淡"
    if "甜" in constraints.avoid_tastes and constraints.taste in ("甜", "酸甜"):
        constraints.taste = None

    for ingredient in INGREDIENT_KEYWORDS:
        if ingredient in text:
            constraints.preferred_ingredients.append(ingredient)

    for match in re.finditer(r"(?:不|别)(?:喜欢吃|爱吃|想吃|要吃|吃|要)([\u4e00-\u9fa5]{1,8})", text):
        constraints.avoid_ingredients.append(match.group(1))

    minutes_match = re.search(r"(\d+)\s*分钟", text)
    if minutes_match:
        constraints.max_minutes = int(minutes_match.group(1))
    elif "十分钟" in text:
        constraints.max_minutes = 10
    elif "半小时" in text:
        constraints.max_minutes = 30

    if any(word in text for word in ["简单", "快手", "别太复杂", "太麻烦"]):
        constraints.difficulty = "简单"
    if any(word in text for word in ["仪式感", "正式", "请几个人"]):
        constraints.scene = "聚餐"
    if "公司" in text:
        constraints.scene = "便当"
    if "夏天" in text or "清爽" in text:
        constraints.scene = "夏季清爽"

    constraints.health_goals = _dedupe(constraints.health_goals)
    constraints.allergens = _dedupe(expand_terms(constraints.allergens))
    constraints.avoid_tastes = _dedupe(constraints.avoid_tastes)
    constraints.preferred_ingredients = _dedupe(constraints.preferred_ingredients)
    constraints.avoid_ingredients = _dedupe(expand_terms(constraints.avoid_ingredients))
    constraints.preferred_ingredients = _remove_blocked_preferred_ingredients(
        constraints.preferred_ingredients,
        constraints.allergens + constraints.avoid_ingredients,
    )
    return constraints


def infer_profile_from_text(text: str) -> dict:
    profile: dict = {
        "source": "dialog",
        "gender": None,
        "age": None,
        "labor_intensity": None,
        "special_groups": [],
        "pregnancy_week": None,
        "taste_preference": None,
        "allergens": [],
        "health_goals": [],
    }

    if any(word in text for word in ["我是男", "男生", "男性", "我老公", "我爸"]):
        profile["gender"] = "男"
    if any(word in text for word in ["我是女", "女生", "女性", "我老婆", "我妈", "女朋友"]):
        profile["gender"] = "女"

    age_match = re.search(r"(\d{1,3})\s*岁", text)
    if age_match:
        profile["age"] = int(age_match.group(1))

    week_match = re.search(r"孕(?:周|期)?\s*(\d{1,2})\s*周|怀孕\s*(\d{1,2})\s*周", text)
    if week_match:
        week = week_match.group(1) or week_match.group(2)
        profile["pregnancy_week"] = f"{week}周"
        profile["special_groups"].append("孕妇")

    if any(word in text for word in ["体力活", "运动量大", "劳动强度高", "经常运动"]):
        profile["labor_intensity"] = "高"
    elif any(word in text for word in ["久坐", "办公室", "不怎么运动", "劳动强度低"]):
        profile["labor_intensity"] = "低"

    disclosed_groups = _extract_disclosed_values(text, _EXPLICIT_HEALTH_PATTERN)
    if disclosed_groups:
        profile["special_groups"].extend(disclosed_groups)
    else:
        for group, keywords in SPECIAL_GROUP_KEYWORDS.items():
            if any(_contains_non_negated(text, keyword) for keyword in keywords):
                profile["special_groups"].append(group)

    avoid_tastes = _extract_avoid_tastes(text)
    for taste, keywords in TASTE_KEYWORDS.items():
        if taste == "偏辣" and "辣" in avoid_tastes:
            continue
        if taste in ("甜", "酸甜") and "甜" in avoid_tastes:
            continue
        if _contains_any(text, keywords):
            profile["taste_preference"] = taste

    disclosed_goals = _extract_disclosed_values(text, _EXPLICIT_GOAL_PATTERN)
    if disclosed_goals:
        profile["health_goals"].extend(disclosed_goals)
    else:
        for goal, keywords in HEALTH_KEYWORDS.items():
            if any(_contains_non_negated(text, keyword) for keyword in keywords):
                profile["health_goals"].append(goal)

    profile["allergens"].extend(_extract_allergens(text))

    profile["special_groups"] = _dedupe(profile["special_groups"])
    profile["allergens"] = _dedupe(expand_terms(profile["allergens"]))
    profile["health_goals"] = _dedupe(profile["health_goals"])
    return profile


def _extract_avoid_tastes(text: str) -> list[str]:
    avoid: list[str] = []
    if any(pattern in text for pattern in ["别辣", "不辣", "不吃辣", "不能吃辣", "不要辣", "一点辣都不想", "少辣"]):
        avoid.append("辣")
    if any(pattern in text for pattern in ["别太甜", "不甜", "不吃甜", "不能吃甜", "不要甜", "少甜", "不要太甜"]):
        avoid.append("甜")
    if any(pattern in text for pattern in ["别太油", "不油", "少油", "不要油", "不能太油"]):
        avoid.append("油")
    return _dedupe(avoid)


def _remove_blocked_preferred_ingredients(preferred: list[str], blocked: list[str]) -> list[str]:
    result: list[str] = []
    for ingredient in preferred:
        if any(contains_food_term(ingredient, value) or contains_food_term(value, ingredient) for value in blocked):
            continue
        result.append(ingredient)
    return result


def _parse_chinese_number(value: str) -> int | None:
    mapping = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if value.isdigit():
        return int(value)
    if value in mapping:
        return mapping[value]
    if value.startswith("十") and len(value) == 2:
        return 10 + mapping.get(value[1], 0)
    if value.endswith("十") and len(value) == 2:
        return mapping.get(value[0], 0) * 10
    return None
