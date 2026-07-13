from __future__ import annotations

from functools import lru_cache

from .constraints import extract_constraints
from .data_loader import load_dialog_cases, load_recipes, load_users
from .models import UserProfile
from .planner import plan_meal
from .session_store import MenuVersionConflict, session_store


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


def recommend(user_id: int | None, messages: list[str]) -> dict:
    user = find_user(user_id)
    constraints = extract_constraints(messages, user)
    result = plan_meal(get_recipes(), constraints, user)
    return {
        "user_id": user_id,
        "user": user.raw if user else None,
        "constraints": constraints.to_dict(),
        **result,
    }


def recommend_with_session(
    user_id: int | None,
    messages: list[str],
    *,
    session_id: str | None = None,
    menu_version: int | None = None,
    is_delta: bool = False,
) -> dict:
    snapshot = session_store.get(session_id) if session_id else None
    if snapshot is not None and snapshot.user_id != user_id:
        snapshot = None

    if snapshot is not None and is_delta:
        merged_messages = [*snapshot.messages, *messages]
    else:
        merged_messages = list(messages)

    result = recommend(user_id, merged_messages)
    changes = {
        "mode": "regenerated" if snapshot else "initial",
        "kept_dishes": [],
        "replaced_dishes": [],
        "change_count": 0,
    }
    result["changes"] = changes

    if snapshot is None:
        created = session_store.create(user_id, merged_messages, result)
        active_session_id = created.session_id
        active_version = created.menu_version
    else:
        updated = session_store.update(
            snapshot.session_id,
            menu_version,
            merged_messages,
            result,
        )
        active_session_id = updated.session_id
        active_version = updated.menu_version

    result["session_id"] = active_session_id
    result["menu_version"] = active_version
    return result
