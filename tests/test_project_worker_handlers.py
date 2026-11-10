import unittest

from project_worker_handlers import build_project_handlers, project_summary


class ProjectWorkerHandlerTests(unittest.TestCase):
    def test_summary_calculates_progress_overdue_and_upcoming_items(self):
        result = project_summary({
            "today": "2026-11-10",
            "project": {
                "id": 7,
                "title": "Пульт",
                "status": "active",
                "deadline": "2026-11-09",
                "goal": "Собрать рабочий прототип",
                "description": "Основной проект",
                "result": "Прототип",
            },
            "items": [
                {"id": 1, "kind": "stage", "content": "Согласовать схему", "done": True},
                {"id": 2, "kind": "stage", "content": "Собрать корпус", "due_date": "2026-11-09", "done": False},
                {"id": 3, "kind": "task", "content": "Заказать детали", "due_date": "2026-11-15", "done": False},
                {"id": 4, "kind": "note", "content": "Проверить размеры", "created_at": "2026-11-08T10:00:00+00:00"},
            ],
        })
        self.assertEqual(result["title"], "Пульт")
        self.assertTrue(result["project_overdue"])
        self.assertEqual(result["stage_progress"], {"done": 1, "total": 2})
        self.assertEqual(result["task_progress"], {"done": 0, "total": 1})
        self.assertEqual(result["overdue_items"][0]["content"], "Собрать корпус")
        self.assertEqual(result["upcoming_items"][0]["content"], "Заказать детали")
        self.assertEqual(result["notes"][0]["content"], "Проверить размеры")

    def test_completed_project_is_not_flagged_overdue(self):
        result = project_summary({
            "today": "2026-11-10",
            "project": {"title": "Готово", "status": "done", "deadline": "2026-10-01"},
            "items": [],
        })
        self.assertFalse(result["project_overdue"])

    def test_rejects_invalid_project_and_item_data(self):
        with self.assertRaises(ValueError):
            project_summary({"today": "2026-11-10", "project": {}, "items": []})
        with self.assertRaises(ValueError):
            project_summary({
                "today": "2026-11-10",
                "project": {"title": "Пульт", "status": "active"},
                "items": [{"kind": "delete", "content": "x"}],
            })
        with self.assertRaises(ValueError):
            project_summary({
                "today": "2026-11-10",
                "project": {"title": "Пульт", "status": "active"},
                "items": [{}] * 501,
            })

    def test_handler_registry_is_allowlisted(self):
        handlers = build_project_handlers()
        self.assertEqual(set(handlers), {"projects.summary"})
        self.assertTrue(callable(handlers["projects.summary"]))


if __name__ == "__main__":
    unittest.main()
