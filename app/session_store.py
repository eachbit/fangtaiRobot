from __future__ import annotations

import copy
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable


class MenuVersionConflict(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__(f"menu version conflict; current version is {current_version}")
        self.current_version = current_version


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    user_id: int | None
    messages: list[str]
    result: dict[str, Any]
    menu_version: int
    created_at: float
    last_accessed_at: float


class SessionStore:
    def __init__(
        self,
        ttl_seconds: float = 7200,
        max_sessions: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_sessions <= 0:
            raise ValueError("ttl_seconds and max_sessions must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock
        self._sessions: OrderedDict[str, SessionSnapshot] = OrderedDict()
        self._lock = threading.RLock()

    def create(
        self,
        user_id: int | None,
        messages: list[str],
        result: dict[str, Any],
    ) -> SessionSnapshot:
        with self._lock:
            now = self._clock()
            self._remove_expired(now)
            while len(self._sessions) >= self._max_sessions:
                self._sessions.popitem(last=False)
            session_id = secrets.token_urlsafe(32)
            snapshot = SessionSnapshot(
                session_id=session_id,
                user_id=user_id,
                messages=list(messages),
                result=copy.deepcopy(result),
                menu_version=1,
                created_at=now,
                last_accessed_at=now,
            )
            self._sessions[session_id] = snapshot
            return copy.deepcopy(snapshot)

    def get(self, session_id: str) -> SessionSnapshot | None:
        with self._lock:
            now = self._clock()
            self._remove_expired(now)
            snapshot = self._sessions.get(session_id)
            if snapshot is None:
                return None
            refreshed = SessionSnapshot(
                session_id=snapshot.session_id,
                user_id=snapshot.user_id,
                messages=list(snapshot.messages),
                result=copy.deepcopy(snapshot.result),
                menu_version=snapshot.menu_version,
                created_at=snapshot.created_at,
                last_accessed_at=now,
            )
            self._sessions[session_id] = refreshed
            self._sessions.move_to_end(session_id)
            return copy.deepcopy(refreshed)

    def update(
        self,
        session_id: str,
        expected_version: int | None,
        messages: list[str],
        result: dict[str, Any],
    ) -> SessionSnapshot:
        with self._lock:
            now = self._clock()
            self._remove_expired(now)
            current = self._sessions.get(session_id)
            if current is None:
                raise KeyError(session_id)
            if expected_version is not None and expected_version != current.menu_version:
                raise MenuVersionConflict(current.menu_version)
            updated = SessionSnapshot(
                session_id=current.session_id,
                user_id=current.user_id,
                messages=list(messages),
                result=copy.deepcopy(result),
                menu_version=current.menu_version + 1,
                created_at=current.created_at,
                last_accessed_at=now,
            )
            self._sessions[session_id] = updated
            self._sessions.move_to_end(session_id)
            return copy.deepcopy(updated)

    def _remove_expired(self, now: float) -> None:
        expired = [
            session_id
            for session_id, snapshot in self._sessions.items()
            if now - snapshot.last_accessed_at > self._ttl_seconds
        ]
        for session_id in expired:
            del self._sessions[session_id]


session_store = SessionStore()
