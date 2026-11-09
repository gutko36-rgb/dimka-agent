import json
import unittest
from datetime import date

from project_agent import looks_like_project_intent, parse_project_response


class ProjectIntentTests(unittest.TestCase):
    def test_detects_add_note(self):
        self.assertTrue(looks_like_project_intent("Добавь заметку в проект Пульт: выбрать корпус"))

    def test_detects_project_question(self):
        self.assertTrue(looks_like_project_intent("Как продвигается проект Пульт?"))

    def test_ignores_regular_chat(self):
        self.assertFalse(looks_like_project_intent("Расскажи, как лучше организовать день"))

    def test_does_not_capture_general_todoist_question(self):
        self.assertFalse(looks_like_project_intent("Сколько задач на сегодня?"))

    def test_ignores_negated_instruction(self):
        self.assertFalse(looks_like_project_intent("Не добавляй заметку в проект Пульт"))


class ProjectPayloadTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 10)
        self.projects = [{"id": 1, "title": "Главный диспечер."}, {"id": 7, "title": "Пульт 2.0"}]

    def parse(self, payload):
        return parse_project_response(json.dumps(payload, ensure_ascii=False), self.today, self.projects)

    def test_resolves_project_exactly(self):
        result = self.parse({
            "action": "add_note", "project_title": "Пульт 2.0",
            "content": "Проверить макет корпуса", "confidence": 0.99
        })
        self.assertEqual(result["project_id"], 7)
        self.assertEqual(result["content"], "Проверить макет корпуса")

    def test_unknown_project_requires_clarification(self):
        result = self.parse({
            "action": "add_task", "project_title": "Несуществующий",
            "content": "Позвонить", "confidence": 0.99
        })
        self.assertIsNone(result["action"])
        self.assertIn("Не нашёл точное", result["clarification"])

    def test_low_confidence_requires_clarification(self):
        result = self.parse({"action": "add_note", "project_title": "Пульт 2.0", "confidence": 0.2})
        self.assertIsNone(result["action"])

    def test_rejects_invalid_update_field(self):
        with self.assertRaises(ValueError):
            self.parse({
                "action": "update_project", "project_title": "Пульт 2.0",
                "field": "telegram_id", "value": "123", "confidence": 0.99
            })

    def test_validates_status(self):
        result = self.parse({
            "action": "update_project", "project_title": "Пульт 2.0",
            "field": "status", "value": "в работе", "confidence": 0.99
        })
        self.assertEqual(result["value"], "active")

    def test_requires_explicit_clear_instruction(self):
        result = self.parse({
            "action": "update_project", "project_title": "Пульт 2.0",
            "field": "goal", "value": "", "confidence": 0.99
        })
        self.assertIsNone(result["action"])
        self.assertIn("Уточни", result["clarification"])

    def test_allows_explicit_clear(self):
        result = self.parse({
            "action": "update_project", "project_title": "Пульт 2.0",
            "field": "goal", "value": "", "clear": True, "confidence": 0.99
        })
        self.assertIsNone(result["value"])

    def test_rejects_duplicate_project_creation(self):
        result = self.parse({
            "action": "create_project", "title": "Пульт 2.0", "confidence": 0.99
        })
        self.assertIsNone(result["action"])
        self.assertIn("уже существует", result["clarification"])

    def test_parses_due_date(self):
        result = self.parse({
            "action": "add_task", "project_title": "Пульт 2.0",
            "content": "Проверить макет", "due_date": "2026-12-01", "confidence": 0.99
        })
        self.assertEqual(result["due_date"], date(2026, 12, 1))


if __name__ == "__main__":
    unittest.main()
