import json
import unittest
from datetime import date

from finance_agent import looks_like_finance_entry, looks_like_finance_question, parse_finance_response


class FinanceIntentTests(unittest.TestCase):
    def test_detects_completed_expense(self):
        self.assertTrue(looks_like_finance_entry("Потратил 800 рублей на продукты"))

    def test_detects_multiple_transaction_message(self):
        self.assertTrue(looks_like_finance_entry(
            "Сегодня заработал 15 000 рублей и потратил 2 000 на бензин"
        ))

    def test_ignores_future_purchase_plan(self):
        self.assertFalse(looks_like_finance_entry("Хочу купить зимнюю резину за 20 тысяч"))

    def test_ignores_negated_purchase(self):
        self.assertFalse(looks_like_finance_entry("Не потратил 500 рублей на продукты"))

    def test_ignores_hypothetical(self):
        self.assertFalse(looks_like_finance_entry("Если бы потратил 500 рублей, хватило бы на бензин"))

    def test_ignores_conditional_would_statement(self):
        self.assertFalse(looks_like_finance_entry("Я бы потратил 500 рублей на бензин"))

    def test_ignores_finance_question(self):
        self.assertFalse(looks_like_finance_entry("Сколько я потратил на машину в этом месяце?"))

    def test_ignores_question_even_with_amount_and_no_question_mark(self):
        self.assertFalse(looks_like_finance_entry("Сколько потратил 500 рублей на бензин"))

    def test_detects_read_only_finance_question(self):
        self.assertTrue(looks_like_finance_question("Сколько я потратил на машину в этом месяце?"))

    def test_detects_project_finance_question(self):
        self.assertTrue(looks_like_finance_question("Сколько потратил на проект Пульт?"))

    def test_ignores_non_finance_question(self):
        self.assertFalse(looks_like_finance_question("Сколько задач на сегодня?"))


class FinancePayloadTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 10)
        self.projects = [{"id": 3, "title": "Пульт"}]

    def parse(self, rows, clarification=None):
        return parse_finance_response(
            json.dumps({"transactions": rows, "clarification": clarification}, ensure_ascii=False),
            self.today,
            self.projects,
        )

    def test_parses_multiple_transactions_and_project(self):
        result = self.parse([
            {"kind": "income", "amount": 15000, "category": "подработка",
             "note": "оплата за работу", "date": "2026-11-10", "confidence": 0.99},
            {"kind": "expense", "amount": 5000, "category": "материалы",
             "note": "пластик", "date": "2026-11-10", "project_title": "Пульт",
             "confidence": 0.95},
        ])
        self.assertEqual(len(result["transactions"]), 2)
        self.assertEqual(result["transactions"][1]["project_id"], 3)
        self.assertEqual(result["transactions"][0]["kind"], "income")

    def test_unknown_project_requires_clarification(self):
        result = self.parse([
            {"kind": "expense", "amount": 2500, "category": "материалы",
             "project_title": "Несуществующий проект", "confidence": 0.99}
        ])
        self.assertIsNotNone(result["clarification"])
        self.assertEqual(result["transactions"], [])

    def test_low_confidence_requires_clarification(self):
        result = self.parse([
            {"kind": "expense", "amount": 2500, "category": "прочее",
             "confidence": 0.4, "needs_clarification": "Это расход или перевод?"}
        ])
        self.assertEqual(result["clarification"], "Это расход или перевод?")

    def test_rejects_negative_amount(self):
        with self.assertRaises(ValueError):
            self.parse([{"kind": "expense", "amount": -1, "category": "еда"}])

    def test_rejects_invalid_kind(self):
        with self.assertRaises(ValueError):
            self.parse([{"kind": "transfer", "amount": 100, "category": "перевод"}])

    def test_model_can_ask_clarification(self):
        result = self.parse([], "Уточни, это доход или возврат долга?")
        self.assertEqual(result["clarification"], "Уточни, это доход или возврат долга?")


if __name__ == "__main__":
    unittest.main()
