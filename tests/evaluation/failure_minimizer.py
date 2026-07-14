from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
import re


_CLAUSE_RE = re.compile(r"[^，,。.;；!?！？]+[，,。.;；!?！？]?")


@dataclass(frozen=True)
class MinimizationResult:
    messages: tuple[str, ...]
    attempts: int
    reached_cap: bool


def minimize_failure(
    original_messages: Sequence[str],
    target_code: str,
    evaluate_codes: Callable[[tuple[str, ...]], Iterable[str]],
    max_attempts: int = 100,
    confirmations: int = 3,
) -> MinimizationResult:
    if max_attempts < 0:
        raise ValueError("max_attempts must not be negative")
    if confirmations < 1:
        raise ValueError("confirmations must be at least 1")

    current = tuple(message for message in original_messages if message.strip())
    if not current:
        raise ValueError("original_messages must contain a non-empty message")

    attempts = 0

    def reproduces(candidate: tuple[str, ...]) -> bool:
        nonlocal attempts
        for _ in range(confirmations):
            if attempts >= max_attempts:
                return False
            attempts += 1
            if target_code not in tuple(evaluate_codes(candidate)):
                return False
        return True

    def reduce_with(candidates: Callable[[tuple[str, ...]], Iterable[tuple[str, ...]]]) -> None:
        nonlocal current
        changed = True
        while changed and attempts < max_attempts:
            changed = False
            for candidate in candidates(current):
                if not candidate or not all(message.strip() for message in candidate):
                    continue
                if reproduces(candidate):
                    current = candidate
                    changed = True
                    break
                if attempts >= max_attempts:
                    break

    def turn_candidates(messages: tuple[str, ...]) -> Iterable[tuple[str, ...]]:
        if len(messages) <= 1:
            return
        for index in range(len(messages)):
            yield messages[:index] + messages[index + 1 :]

    def clause_candidates(messages: tuple[str, ...]) -> Iterable[tuple[str, ...]]:
        for message_index, message in enumerate(messages):
            clauses = _CLAUSE_RE.findall(message)
            if len(clauses) <= 1:
                continue
            for clause_index in range(len(clauses)):
                shortened = "".join(
                    clause
                    for index, clause in enumerate(clauses)
                    if index != clause_index
                ).strip()
                if shortened:
                    yield (
                        messages[:message_index]
                        + (shortened,)
                        + messages[message_index + 1 :]
                    )

    reduce_with(turn_candidates)
    reduce_with(clause_candidates)
    return MinimizationResult(current, attempts, attempts >= max_attempts)
