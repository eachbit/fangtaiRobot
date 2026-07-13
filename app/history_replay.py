from __future__ import annotations

from typing import Callable


def replay_previous_result(
    messages: list[str],
    generate: Callable[[list[str]], dict],
) -> tuple[dict | None, list[str]]:
    if len(messages) < 2:
        return None, list(messages)
    return generate(messages[:-1]), list(messages)
