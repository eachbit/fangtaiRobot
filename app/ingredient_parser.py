from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class ParsedIngredient:
    raw_text: str
    raw_name: str
    canonical_name: str
    amount: float | int | None
    unit: str | None
    grams: float | int | None
    amount_source: str
    confidence: float
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_GROUP_PREFIX = re.compile(r"^(?:(?:主料|辅料|调料|A料|B料)\s*[:：]\s*)+")
_NUMBER = r"(?:\d+(?:\.\d+)?|\d+/\d+|半)"
_UNITS = (
    r"kg|g|ml|千克|公斤|克|毫升|大勺|汤匙|小勺|茶匙|勺|碗|个|瓣|根|片|颗|粒|只|条|块|朵|包|袋|盒"
)
_QUANTITY = re.compile(rf"(?P<number>{_NUMBER})\s*(?P<unit>{_UNITS})", re.IGNORECASE)
_FUZZY = re.compile(r"适量|少许|少量")

_SPOON_GRAMS = {
    "大勺": 15,
    "汤匙": 15,
    "小勺": 5,
    "茶匙": 5,
    "勺": 10,
    "碗": 200,
}
_DEFAULT_GRAMS = {
    "盐": 3,
    "油": 10,
    "食用油": 10,
    "糖": 5,
    "白砂糖": 5,
    "酱油": 10,
    "生抽": 10,
    "老抽": 10,
}
_ALIASES = {
    "西红柿": "番茄",
    "姜片": "姜",
    "姜丝": "姜",
    "姜末": "姜",
    "葱花": "葱",
    "葱片": "葱",
    "葱丝": "葱",
    "葱段": "葱",
    "葱末": "葱",
    "小葱": "葱",
    "蒜花": "蒜",
    "蒜段": "蒜",
    "大蒜": "蒜",
    "蒜瓣": "蒜",
    "蒜片": "蒜",
    "蒜丝": "蒜",
    "蒜末": "蒜",
}


def _number_value(value: str) -> float | int | None:
    if value == "半":
        return 0.5
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        if denominator_value == 0:
            return None
        return float(numerator) / denominator_value
    number = float(value)
    return int(number) if number.is_integer() else number


def _canonicalize(name: str) -> tuple[str, str]:
    name = name.strip(" \t:：,，")
    canonical = _ALIASES.get(name)
    if canonical:
        notes = f"处理状态：{name}" if name != "西红柿" else "别名归一化：西红柿"
        return canonical, notes
    return name, ""


def _append_note(existing: str, note: str) -> str:
    if not note:
        return existing
    return f"{existing}；{note}" if existing else note


def parse_ingredient_segment(text: str) -> ParsedIngredient:
    raw_text = text.strip()
    segment = _GROUP_PREFIX.sub("", raw_text).strip()
    notes = ""

    preparation_notes = re.findall(r"（[^）]*）|\([^)]*\)", segment)
    for preparation in preparation_notes:
        notes = _append_note(notes, preparation)
    working_segment = re.sub(r"（[^）]*）|\([^)]*\)", "", segment)

    quantity_match = _QUANTITY.search(working_segment)
    fuzzy_match = _FUZZY.search(working_segment) if quantity_match is None else None
    amount: float | int | None = None
    unit: str | None = None
    grams: float | int | None = None
    amount_source = "unknown"
    confidence = 0.0

    if quantity_match:
        amount = _number_value(quantity_match.group("number"))
        source_unit = quantity_match.group("unit").lower()
        if amount is None:
            notes = _append_note(notes, f"非法数量：{quantity_match.group('number')}")
        else:
            unit = {
                "克": "g",
                "千克": "kg",
                "公斤": "kg",
                "毫升": "ml",
            }.get(source_unit, source_unit)
        if amount is not None and unit == "g":
            grams = amount
            amount_source = "explicit"
            confidence = 0.98
        elif amount is not None and unit == "kg":
            grams = amount * 1000
            amount_source = "explicit"
            confidence = 0.98
        elif amount is not None and unit == "ml":
            grams = amount
            amount_source = "estimated"
            confidence = 0.82
            notes = _append_note(notes, "按1g/ml近似换算")
        elif amount is not None and unit in _SPOON_GRAMS:
            grams = amount * _SPOON_GRAMS[unit]
            amount_source = "estimated"
            confidence = 0.7
            notes = _append_note(notes, "通用容量近似")
        elif amount is not None and unit == "个":
            if "蛋" in working_segment:
                grams = amount * 50
                amount_source = "estimated"
                confidence = 0.7
                notes = _append_note(notes, "按每个蛋50g近似换算")
            else:
                amount_source = "unknown"
                confidence = 0.2
        elif amount is not None:
            amount_source = "unknown"
            confidence = 0.2
        working_segment = (
            working_segment[: quantity_match.start()] + working_segment[quantity_match.end() :]
        )
    elif fuzzy_match:
        working_segment = (
            working_segment[: fuzzy_match.start()] + working_segment[fuzzy_match.end() :]
        )
        amount_source = "default"
        confidence = 0.4

    raw_name = re.sub(r"\s+", "", working_segment).strip(" \t:：,，")
    canonical_name, alias_note = _canonicalize(raw_name)
    notes = _append_note(notes, alias_note)

    if amount_source == "default":
        for term, default_grams in sorted(_DEFAULT_GRAMS.items(), key=lambda item: len(item[0]), reverse=True):
            if term in canonical_name:
                grams = default_grams
                break

    return ParsedIngredient(
        raw_text=raw_text,
        raw_name=raw_name,
        canonical_name=canonical_name,
        amount=amount,
        unit=unit,
        grams=grams,
        amount_source=amount_source,
        confidence=confidence,
        notes=notes,
    )


def parse_ingredients(text: str) -> list[ParsedIngredient]:
    segments = re.split(r"[；;\r\n]+", text)
    return [parse_ingredient_segment(segment) for segment in segments if segment.strip()]
