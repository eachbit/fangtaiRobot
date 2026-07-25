from __future__ import annotations

from functools import lru_cache

from .constraints import extract_constraints
from .data_loader import load_dialog_cases, load_recipes, load_users
from .models import UserProfile
from .planner import plan_meal
from .session_store import new_session_id, store


@lru_cache(maxsize=1)
def get_recipes():
    return load_recipes()


@lru_cache(maxsize=1)
def get_users():
    return load_users()


@lru_cache(maxsize=1)
def get_dialog_cases():
    return load_dialog_cases()


def find_user(user_id: int | None) -> UserProfile | None:
    if user_id is None:
        return None
    for user in get_users():
        if user.id == user_id:
            return user
    return None


def _reset_requested(messages: list[str]) -> bool:
    text = "\n".join(messages)
    return any(word in text for word in ["全部重做", "全部换掉", "重新推荐", "换一桌", "重新来", "不要保留"])


def recommend(user_id: int | None, messages: list[str], session_id: str | None = None) -> dict:
    user = find_user(user_id)
    previous = store.get(session_id)
    merged_messages = list(messages)
    if previous:
        if previous.messages and messages[: len(previous.messages)] != previous.messages:
            merged_messages = previous.messages + messages
        else:
            merged_messages = list(messages)
    constraints = extract_constraints(merged_messages, user)
    previous_menu_ids = None if _reset_requested(messages) else (previous.menu_ids if previous else None)
    result = plan_meal(get_recipes(), constraints, user, previous_menu_ids=previous_menu_ids)
    effective_session_id = previous.session_id if previous else (session_id or new_session_id())
    constraint_snapshot = constraints.to_dict()
    store.save(
        effective_session_id,
        user_id,
        merged_messages,
        [item["id"] for item in result["menu"]],
        constraint_snapshot,
    )
    return {
        "user_id": user_id,
        "user": user.raw if user else None,
        "constraints": constraint_snapshot,
        "session_id": effective_session_id,
        **result,
    }
