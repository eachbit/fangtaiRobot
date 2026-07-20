from __future__ import annotations

from functools import lru_cache

from .constraints import extract_constraints
from .data_loader import load_dialog_cases, load_recipes, load_users
from .history_replay import replay_previous_result
from .menu_revision import is_full_reset, revise_menu
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
    return _generate_recommendation(user_id, messages)


def _generate_recommendation(
    user_id: int | None,
    messages: list[str],
    excluded_recipe_ids: set[int] | None = None,
) -> dict:
    user = find_user(user_id)
    constraints = extract_constraints(messages, user)
    result = plan_meal(
        get_recipes(),
        constraints,
        user,
        excluded_recipe_ids=excluded_recipe_ids,
    )
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

    previous_result = snapshot.result if snapshot is not None else None
    if previous_result is None and not is_delta and len(merged_messages) >= 2:
        previous_result, _ = replay_previous_result(
            merged_messages,
            lambda prefix: _generate_recommendation(user_id, prefix),
        )

    user = find_user(user_id)
    constraints = extract_constraints(merged_messages, user)
    if previous_result is not None and is_full_reset(merged_messages):
        old_ids = {item["id"] for item in previous_result.get("menu", [])}
        result = _generate_recommendation(user_id, merged_messages, old_ids)
        new_ids = [item["id"] for item in result["menu"]]
        result["changes"] = {
            "mode": "full_regeneration",
            "kept_dishes": [],
            "replaced_dishes": [],
            "change_count": max(len(old_ids), len(new_ids)),
        }
        result["score_card"]["minimal_change"] = False
    elif previous_result is not None:
        result = revise_menu(get_recipes(), previous_result, constraints, user)
        result.update(
            {
                "user_id": user_id,
                "user": user.raw if user else None,
                "constraints": constraints.to_dict(),
            }
        )
    else:
        result = _generate_recommendation(user_id, merged_messages)
        result["changes"] = {
            "mode": "initial",
            "kept_dishes": [],
            "replaced_dishes": [],
            "change_count": 0,
        }

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
