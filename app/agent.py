from __future__ import annotations

import re
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


def recommend(
    user_id: int | None,
    messages: list[str],
    session_id: str | None = None,
    rollback_to: int | None = None,
) -> dict:
    user = find_user(user_id)
    previous = store.get(session_id)
    rollback_target = _rollback_target(messages, previous, rollback_to)
    if rollback_target is not None:
        if not previous:
            raise ValueError("session_not_found_for_rollback")
        current_version = previous.menu_version
        state = store.rollback(previous.session_id, rollback_target)
        constraints = extract_constraints(state.messages, user)
        result = plan_meal(get_recipes(), constraints, user, previous_menu_ids=state.menu_ids)
        result["changes"] = {
            "mode": "rollback",
            "kept_dishes": list(state.menu_ids),
            "replaced_dishes": [],
            "change_count": 0,
            "from_version": current_version,
            "to_version": state.menu_version,
            "source_version": rollback_target,
        }
        result["score_card"]["minimal_change"] = True
        result["answer"] = f"已恢复到菜单版本 v{rollback_target}，当前版本为 v{state.menu_version}。"
        return _response(user_id, user, state, constraints, result)

    if previous:
        prefix_matches = bool(previous.messages) and messages[: len(previous.messages)] == previous.messages
        turns = list(messages[len(previous.messages) :]) if prefix_matches else list(messages)
        current_messages = list(previous.messages)
        current_menu_ids = list(previous.menu_ids)
        state = previous
    else:
        turns = list(messages)
        current_messages = []
        current_menu_ids = None
        state = None

    if not turns:
        if not state:
            raise ValueError("messages must not be empty")
        constraints = extract_constraints(state.messages, user)
        result = plan_meal(get_recipes(), constraints, user, previous_menu_ids=state.menu_ids)
        return _response(user_id, user, state, constraints, result)

    effective_session_id = state.session_id if state else (session_id or new_session_id())
    result = None
    constraints = None
    for message in turns:
        current_messages.append(message)
        constraints = extract_constraints(current_messages, user)
        previous_menu_ids = None if _reset_requested([message]) else current_menu_ids
        result = plan_meal(get_recipes(), constraints, user, previous_menu_ids=previous_menu_ids)
        current_menu_ids = [item["id"] for item in result["menu"]]
        state = store.save(
            effective_session_id,
            user_id,
            current_messages,
            current_menu_ids,
            constraints.to_dict(),
        )

    assert state is not None and constraints is not None and result is not None
    return _response(user_id, user, state, constraints, result)


def _response(
    user_id: int | None,
    user: UserProfile | None,
    state,
    constraints,
    result: dict,
) -> dict:
    return {
        "user_id": user_id,
        "user": user.raw if user else None,
        "messages": list(state.messages),
        "constraints": constraints.to_dict(),
        "session_id": state.session_id,
        "menu_version": state.menu_version,
        "history": store.history(state.session_id)["history"],
        **result,
    }


def _rollback_target(messages: list[str], previous, explicit_target: int | None) -> int | None:
    if explicit_target is not None:
        if type(explicit_target) is not int:
            raise ValueError("rollback_version_not_found")
        return explicit_target
    if not previous or not messages:
        return None
    text = "\n".join(messages)
    match = re.search(r"(?:回到|恢复到|返回到|撤回到)\s*(?:第|版本|v|V)?\s*(\d+)\s*(?:版|版本)?", text)
    if match:
        return int(match.group(1))
    if any(word in text for word in ["撤销刚才", "撤销上一步", "恢复上一版", "回到上一版", "撤回刚才"]):
        return previous.menu_version - 1
    return None
