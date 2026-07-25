from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4


TTL_SECONDS = 2 * 60 * 60
MAX_SESSIONS = 256


@dataclass(frozen=True)
class SessionState:
    session_id: str
    user_id: int | None
    messages: list[str]
    menu_ids: list[int]
    constraints: dict
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
        state = SessionState(
            session_id=session_id,
            user_id=user_id,
            messages=list(messages),
            menu_ids=list(menu_ids),
            constraints=dict(constraints),
            updated_at=time.time(),
        )
        with self._lock:
            self._purge_expired()
            self._items[session_id] = state
            self._items.move_to_end(session_id)
            while len(self._items) > self.max_sessions:
                self._items.popitem(last=False)
        return state

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
