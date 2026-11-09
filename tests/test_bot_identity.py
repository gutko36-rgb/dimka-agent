import unittest

from bot_identity import BotIdentity, load_bot_identity


class BotIdentityTests(unittest.TestCase):
    def test_defaults_preserve_dimka_identity_and_disable_group(self):
        self.assertEqual(
            load_bot_identity({}),
            BotIdentity(
                bot_id="dimka",
                role="personal_assistant",
                display_name="Димка",
                coordination_chat_id=None,
            ),
        )

    def test_loads_configured_identity(self):
        identity = load_bot_identity({
            "BOT_ID": "finance_bot",
            "BOT_ROLE": "finance",
            "BOT_DISPLAY_NAME": "Финансовый агент",
            "BOT_COORDINATION_CHAT_ID": "-1004326591308",
        })
        self.assertEqual(identity.bot_id, "finance_bot")
        self.assertEqual(identity.role, "finance")
        self.assertEqual(identity.display_name, "Финансовый агент")
        self.assertEqual(identity.coordination_chat_id, -1004326591308)

    def test_optional_chat_id_can_be_omitted(self):
        self.assertIsNone(load_bot_identity({"BOT_ID": "projects"}).coordination_chat_id)

    def test_rejects_invalid_identifiers(self):
        for key, value in (("BOT_ID", "Dimka"), ("BOT_ROLE", "finance agent")):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    load_bot_identity({key: value})

    def test_rejects_empty_display_name(self):
        with self.assertRaises(ValueError):
            load_bot_identity({"BOT_DISPLAY_NAME": "   "})

    def test_rejects_invalid_chat_id(self):
        with self.assertRaises(ValueError):
            load_bot_identity({"BOT_COORDINATION_CHAT_ID": "group-chat"})

    def test_rejects_positive_chat_id(self):
        with self.assertRaises(ValueError):
            load_bot_identity({"BOT_COORDINATION_CHAT_ID": "12345"})


if __name__ == "__main__":
    unittest.main()
