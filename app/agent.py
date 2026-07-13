from __future__ import annotations

from functools import lru_cache

from .constraints import extract_constraints
from .data_loader import load_dialog_cases, load_recipes, load_users
from .models import UserProfile
from .planner import plan_meal


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
