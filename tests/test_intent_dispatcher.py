import unittest

from intent_dispatcher import AGENT_POLICIES, dispatch_intent, get_agent_policy


class IntentDispatcherTests(unittest.TestCase):
    def test_finance_statement_routes_to_confirmed_draft(self):
        decision = dispatch_intent("Потратил 1500 рублей на продукты")
        self.assertEqual(decision.agent_id, "finance")
        self.assertEqual(decision.action, "prepare_finance_draft")
        self.assertEqual(decision.capability, "finance.draft")
        self.assertEqual(decision.confirmation_policy, "required")

    def test_finance_question_routes_read_only(self):
        decision = dispatch_intent("Сколько потратил на продукты за месяц?")
        self.assertEqual(decision.agent_id, "finance")
        self.assertEqual(decision.action, "answer_finance_question")
        self.assertEqual(decision.capability, "finance.read")
        self.assertEqual(decision.confirmation_policy, "not_required")

    def test_project_request_is_delegated_to_confirmation_aware_handler(self):
        decision = dispatch_intent("Добавь задачу в проект Дом")
        self.assertEqual(decision.agent_id, "projects")
        self.assertEqual(decision.action, "prepare_project_action")
        self.assertEqual(decision.confirmation_policy, "handler_enforced")

    def test_general_message_routes_to_main_assistant(self):
        decision = dispatch_intent("Привет, как дела?")
        self.assertEqual(decision.agent_id, "assistant")
        self.assertEqual(decision.action, "chat")
        self.assertEqual(decision.confirmation_policy, "not_required")

    def test_negated_finance_statement_is_not_a_write_intent(self):
        decision = dispatch_intent("Я не потратил 1500 рублей")
        self.assertEqual(decision.agent_id, "assistant")

    def test_internal_agents_have_no_direct_write_or_admin_capabilities(self):
        for policy in AGENT_POLICIES.values():
            self.assertTrue(policy.writes_require_main_bot_confirmation)
            self.assertFalse(any("delete" in capability or "sql" in capability for capability in policy.capabilities))
            self.assertFalse(any(capability.endswith(".write") for capability in policy.capabilities))

    def test_unknown_agent_is_denied(self):
        with self.assertRaises(ValueError):
            get_agent_policy("unknown-agent")


if __name__ == "__main__":
    unittest.main()
