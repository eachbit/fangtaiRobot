from __future__ import annotations

import threading
import unittest

from app.agent import recommend, recommend_with_session
from app.session_store import MenuVersionConflict, SessionStore


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = SessionStore(ttl_seconds=10, max_sessions=2, clock=self.clock)

    def test_creates_unpredictable_session_and_increments_version(self) -> None:
        session = self.store.create(user_id=1, messages=["推荐晚餐"], result={"menu": []})

        self.assertGreaterEqual(len(session.session_id), 32)
        self.assertEqual(session.menu_version, 1)

        updated = self.store.update(
            session.session_id,
            expected_version=1,
            messages=["推荐晚餐", "不要虾"],
            result={"menu": [{"id": 1}]},
        )
        self.assertEqual(updated.menu_version, 2)

    def test_expired_session_is_removed(self) -> None:
        session = self.store.create(None, ["第一轮"], {"menu": []})
        self.clock.value += 11

        self.assertIsNone(self.store.get(session.session_id))

    def test_evicts_least_recently_used_session_at_capacity(self) -> None:
        first = self.store.create(None, ["一"], {})
        self.clock.value += 1
        second = self.store.create(None, ["二"], {})
        self.clock.value += 1
        self.store.get(first.session_id)
        self.clock.value += 1
        third = self.store.create(None, ["三"], {})

        self.assertIsNotNone(self.store.get(first.session_id))
        self.assertIsNone(self.store.get(second.session_id))
        self.assertIsNotNone(self.store.get(third.session_id))

    def test_rejects_explicit_stale_version(self) -> None:
        session = self.store.create(None, ["第一轮"], {})
        self.store.update(session.session_id, 1, ["第二轮"], {})

        with self.assertRaises(MenuVersionConflict) as context:
            self.store.update(session.session_id, 1, ["冲突更新"], {})

        self.assertEqual(context.exception.current_version, 2)

    def test_concurrent_updates_do_not_silently_overwrite(self) -> None:
        session = self.store.create(None, ["第一轮"], {})
        successes: list[int] = []
        conflicts: list[int] = []

        def update(index: int) -> None:
            try:
                updated = self.store.update(session.session_id, 1, [str(index)], {"index": index})
                successes.append(updated.menu_version)
            except MenuVersionConflict as exc:
                conflicts.append(exc.current_version)

        threads = [threading.Thread(target=update, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(successes, [2])
        self.assertEqual(conflicts, [2])


class LegacyAgentCompatibilityTests(unittest.TestCase):
    def test_legacy_recommend_signature_still_works(self) -> None:
        result = recommend(None, ["推荐2道晚餐"])

        self.assertEqual(len(result["menu"]), 2)
        self.assertIn("answer", result)

    def test_first_session_request_returns_identity_and_version(self) -> None:
        result = recommend_with_session(None, ["推荐2道晚餐"])

        self.assertTrue(result["session_id"])
        self.assertEqual(result["menu_version"], 1)
        self.assertEqual(result["changes"]["mode"], "initial")

    def test_delta_message_appends_to_existing_session(self) -> None:
        first = recommend_with_session(None, ["推荐2道晚餐"])

        second = recommend_with_session(
            None,
            ["不要虾"],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )

        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["menu_version"], 2)
        self.assertIn("虾", second["constraints"]["allergens"] or second["constraints"]["avoid_ingredients"])

    def test_changed_user_does_not_reuse_session(self) -> None:
        first = recommend_with_session(None, ["推荐2道晚餐"])

        second = recommend_with_session(1, ["推荐2道晚餐"], session_id=first["session_id"])

        self.assertNotEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["user_id"], 1)


if __name__ == "__main__":
    unittest.main()
