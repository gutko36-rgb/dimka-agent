import unittest
from datetime import date
from decimal import Decimal

from lifestyle_workflow import (
    parse_goal_create,
    parse_goal_progress,
    parse_habit_create,
    parse_habit_log,
    render_goals,
    render_habit_history,
    render_habits,
)


class LifestyleWorkflowTests(unittest.TestCase):
    def test_parse_goal_with_description_and_due_date(self):
        result = parse_goal_create("Выучить английский | 20 минут каждый день | 2027-01-01")
        self.assertEqual(result["title"], "Выучить английский")
        self.assertEqual(result["description"], "20 минут каждый день")
        self.assertEqual(result["target_date"], date(2027, 1, 1))

    def test_parse_goal_without_optional_fields(self):
        result = parse_goal_create("Переехать в новый дом | -")
        self.assertIsNone(result["description"])
        self.assertIsNone(result["target_date"])

    def test_goal_progress_validates_range_and_id(self):
        result = parse_goal_progress("5 | 37,5 | Закрыл первый этап")
        self.assertEqual(result["goal_id"], 5)
        self.assertEqual(result["progress"], Decimal("37.50"))
        self.assertEqual(result["note"], "Закрыл первый этап")
        with self.assertRaises(ValueError):
            parse_goal_progress("0 | 120")

    def test_parse_habit_frequency(self):
        result = parse_habit_create("Ходьба | 5 | 30 минут")
        self.assertEqual(result["title"], "Ходьба")
        self.assertEqual(result["target_per_week"], 5)
        self.assertEqual(result["description"], "30 минут")

    def test_habit_log_accepts_only_done_or_skipped(self):
        self.assertEqual(
            parse_habit_log("7 | done | Утренняя прогулка"),
            {"habit_id": 7, "status": "done", "note": "Утренняя прогулка"},
        )
        with self.assertRaises(ValueError):
            parse_habit_log("7 | missed")

    def test_render_goals_habits_and_week_history(self):
        goals = render_goals([{"id": 1, "title": "Английский", "progress": 25, "target_date": None}])
        habits = render_habits([{"id": 2, "title": "Ходьба", "target_per_week": 5}])
        history = render_habit_history(
            [{"id": 2, "title": "Ходьба", "target_per_week": 5}],
            [
                {"habit_id": 2, "log_date": "2026-11-09", "status": "done"},
                {"habit_id": 2, "log_date": "2026-11-10", "status": "skipped"},
            ],
            start_date=date(2026, 11, 3),
            end_date=date(2026, 11, 10),
        )
        self.assertIn("Английский", goals)
        self.assertIn("Ходьба", habits)
        self.assertIn("выполнено 1, пропущено 1", history)


if __name__ == "__main__":
    unittest.main()
