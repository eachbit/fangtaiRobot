from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4


TTL_SECONDS = 2 * 60 * 60
MAX_SESSIONS = 256
MAX_HISTORY = 32


@dataclass(frozen=True)
class MenuSnapshot:
    version: int
    user_id: int | None
    messages: tuple[str, ...]
    menu_ids: tuple[int, ...]
    constraints: dict
    operation: str
    source_version: int | None
    created_at: float

    def summary(self) -> dict:
        return {
            "version": self.version,
            "menu_ids": list(self.menu_ids),
            "message_count": len(self.messages),
            "operation": self.operation,
            "source_version": self.source_version,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SessionState:
    session_id: str
    user_id: int | None
    messages: list[str]
    menu_ids: list[int]
    constraints: dict
    menu_version: int
    history: tuple[MenuSnapshot, ...]
    updated_at: float


class SessionStore:
    def __init__(self, ttl_seconds: int = TTL_SECONDS, max_sessions: int = MAX_SESSIONS):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._items: OrderedDict[str, SessionState] = OrderedDict()
        self._lock = Lock()

    def get(self, session_id: str | None) -> SessionState | None:
        if not session_id:
            return None
        with self._lock:
            self._purge_expired()
            state = self._items.get(session_id)
            if not state:
                return None
            self._items.move_to_end(session_id)
            return state

    def save(
        self,
        session_id: str,
        user_id: int | None,
        messages: list[str],
        menu_ids: list[int],
        constraints: dict,
    ) -> SessionState:
        now = time.time()
        with self._lock:
            self._purge_expired()
            previous = self._items.get(session_id)
            version = previous.menu_version + 1 if previous else 1
            snapshot = MenuSnapshot(
                version=version,
                user_id=user_id,
                messages=tuple(messages),
                menu_ids=tuple(menu_ids),
                constraints=dict(constraints),
                operation="recommendation",
                source_version=None,
                created_at=now,
            )
            history = ((previous.history if previous else ()) + (snapshot,))[-MAX_HISTORY:]
            state = SessionState(
                session_id=session_id,
                user_id=user_id,
                messages=list(messages),
                menu_ids=list(menu_ids),
                constraints=dict(constraints),
                menu_version=version,
                history=history,
                updated_at=now,
            )
            self._items[session_id] = state
            self._items.move_to_end(session_id)
            self._trim_sessions()
            return state

    def rollback(self, session_id: str, target_version: int) -> SessionState:
        if type(target_version) is not int or target_version < 1:
            raise ValueError("rollback_version_not_found")
        now = time.time()
        with self._lock:
            self._purge_expired()
            current = self._items.get(session_id)
            if not current:
                raise ValueError("session_not_found_for_rollback")
            target = next((item for item in current.history if item.version == target_version), None)
            if not target:
                raise ValueError("rollback_version_not_found")
            version = current.menu_version + 1
            snapshot = MenuSnapshot(
                version=version,
                user_id=target.user_id,
                messages=target.messages,
                menu_ids=target.menu_ids,
                constraints=dict(target.constraints),
                operation="rollback",
                source_version=target.version,
                created_at=now,
            )
            history = (current.history + (snapshot,))[-MAX_HISTORY:]
            state = SessionState(
                session_id=session_id,
                user_id=target.user_id,
                messages=list(target.messages),
                menu_ids=list(target.menu_ids),
                constraints=dict(target.constraints),
                menu_version=version,
                history=history,
                updated_at=now,
            )
            self._items[session_id] = state
            self._items.move_to_end(session_id)
            self._trim_sessions()
            return state

    def history(self, session_id: str) -> dict:
        with self._lock:
            self._purge_expired()
            state = self._items.get(session_id)
            if not state:
                raise KeyError(session_id)
            self._items.move_to_end(session_id)
            return {
                "session_id": state.session_id,
                "current_version": state.menu_version,
                "history": [item.summary() for item in state.history],
            }

    def _trim_sessions(self) -> None:
        while len(self._items) > self.max_sessions:
            self._items.popitem(last=False)

    def _purge_expired(self) -> None:
        if not self._items:
            return
        now = time.time()
        expired = [
            session_id
            for session_id, state in self._items.items()
            if now - state.updated_at > self.ttl_seconds
        ]
        for session_id in expired:
            self._items.pop(session_id, None)


store = SessionStore()


def new_session_id() -> str:
    return uuid4().hex
