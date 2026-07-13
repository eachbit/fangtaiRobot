from __future__ import annotations

import unittest

from app.agent import recommend_with_session


class MenuRevisionTests(unittest.TestCase):
    def test_new_dislike_keeps_unaffected_dishes_and_replaces_only_conflicts(self) -> None:
        first = recommend_with_session(None, ["晚餐推荐4道菜，想吃高蛋白"])
        old_ids = [item["id"] for item in first["menu"]]
        shrimp_ids = [item["id"] for item in first["menu"] if "虾" in item["name"] + item["ingredients"]]
        self.assertTrue(shrimp_ids, "fixture needs at least one shrimp dish")

        second = recommend_with_session(
            None,
            ["不要虾，其他菜保留"],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )
        new_ids = [item["id"] for item in second["menu"]]

        kept_expected = [recipe_id for recipe_id in old_ids if recipe_id not in shrimp_ids]
        self.assertEqual([recipe_id for recipe_id in new_ids if recipe_id in old_ids], kept_expected)
        self.assertEqual(second["changes"]["change_count"], len(shrimp_ids))
        self.assertEqual(second["changes"]["mode"], "minimal_revision")
        self.assertTrue(second["score_card"]["minimal_change"])
        self.assertNotIn("虾", " ".join(item["name"] + item["ingredients"] for item in second["menu"]))

    def test_non_conflicting_follow_up_preserves_ids_and_order(self) -> None:
        first = recommend_with_session(None, ["推荐3道晚餐"])
        old_ids = [item["id"] for item in first["menu"]]

        second = recommend_with_session(
            None,
            ["就按这个菜单，做法简单一点"],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )

        self.assertEqual([item["id"] for item in second["menu"]], old_ids)
        self.assertEqual(second["changes"]["change_count"], 0)

    def test_explicit_full_reset_does_not_lock_previous_menu(self) -> None:
        first = recommend_with_session(None, ["推荐3道晚餐"])
        old_ids = [item["id"] for item in first["menu"]]

        second = recommend_with_session(
            None,
            ["全部换掉，重新推荐一桌"],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )

        self.assertEqual(second["changes"]["mode"], "full_regeneration")
        self.assertNotEqual([item["id"] for item in second["menu"]], old_ids)
        self.assertFalse(second["score_card"]["minimal_change"])

    def test_full_history_replay_matches_session_revision(self) -> None:
        initial = "晚餐推荐4道菜，想吃高蛋白"
        follow_up = "不要虾，其他菜保留"
        first = recommend_with_session(None, [initial])
        session_result = recommend_with_session(
            None,
            [follow_up],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )

        history_result = recommend_with_session(None, [initial, follow_up])

        self.assertEqual(
            [item["id"] for item in history_result["menu"]],
            [item["id"] for item in session_result["menu"]],
        )
        self.assertEqual(history_result["changes"], session_result["changes"])

    def test_positional_instruction_replaces_only_second_dish(self) -> None:
        first = recommend_with_session(None, ["推荐3道晚餐"])
        old_ids = [item["id"] for item in first["menu"]]

        second = recommend_with_session(
            None,
            ["第二道换掉，其他保留"],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )
        new_ids = [item["id"] for item in second["menu"]]

        self.assertEqual(new_ids[0], old_ids[0])
        self.assertNotEqual(new_ids[1], old_ids[1])
        self.assertEqual(new_ids[2], old_ids[2])
        self.assertEqual(second["changes"]["change_count"], 1)

    def test_change_count_matches_actual_changed_positions(self) -> None:
        first = recommend_with_session(None, ["晚餐推荐4道菜，想吃高蛋白"])
        old_ids = [item["id"] for item in first["menu"]]

        second = recommend_with_session(
            None,
            ["不要虾，其他菜保留"],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )
        new_ids = [item["id"] for item in second["menu"]]
        actual_changes = sum(old != new for old, new in zip(old_ids, new_ids)) + abs(
            len(old_ids) - len(new_ids)
        )

        self.assertEqual(second["changes"]["change_count"], actual_changes)
        menu_text = " ".join(item["name"] + item["ingredients"] for item in second["menu"])
        self.assertNotIn("虾", menu_text)

    def test_reducing_dish_count_keeps_prefix_and_reports_removed_count(self) -> None:
        first = recommend_with_session(None, ["推荐4道晚餐"])
        old_ids = [item["id"] for item in first["menu"]]

        second = recommend_with_session(
            None,
            ["改成3道菜，前面的保留"],
            session_id=first["session_id"],
            menu_version=first["menu_version"],
            is_delta=True,
        )

        self.assertEqual([item["id"] for item in second["menu"]], old_ids[:3])
        self.assertEqual(second["changes"]["change_count"], 1)


if __name__ == "__main__":
    unittest.main()
