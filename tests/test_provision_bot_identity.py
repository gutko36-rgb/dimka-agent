import unittest
from types import SimpleNamespace

from scripts.provision_bot_identity import provision_bot_identity


class FakeQuery:
    def __init__(self, client, operation):
        self.client = client
        self.operation = operation
        self.values = None
        self.filters = []

    def select(self, *_args):
        return self

    def eq(self, *args):
        self.filters.append(args)
        return self

    def limit(self, *_args):
        return self

    def insert(self, values):
        self.values = values
        self.operation = "insert"
        return self

    def update(self, values):
        self.values = values
        self.operation = "update"
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def execute(self):
        self.client.calls.append((self.operation, self.values, self.filters))
        if self.operation == "select":
            return SimpleNamespace(data=self.client.existing)
        if self.operation == "insert":
            self.client.registry = self.values
        if self.operation == "update" and self.values.get("is_active") is True:
            self.client.registry["is_active"] = True
        if self.operation == "delete":
            self.client.registry = None
        return SimpleNamespace(data=[])


class FakeAdmin:
    def __init__(self, client):
        self.client = client

    def create_user(self, attributes):
        self.client.created_user = attributes
        if self.client.fail_create:
            raise RuntimeError("auth unavailable")
        return SimpleNamespace(user=SimpleNamespace(id="auth-user-1"))

    def delete_user(self, user_id):
        self.client.deleted_user = user_id


class FakeClient:
    def __init__(self, existing=None, fail_create=False):
        self.existing = existing or []
        self.fail_create = fail_create
        self.registry = None
        self.calls = []
        self.created_user = None
        self.deleted_user = None
        self.auth = SimpleNamespace(admin=FakeAdmin(self))

    def table(self, _name):
        return FakeQuery(self, "select")


class ProvisionBotIdentityTests(unittest.TestCase):
    def kwargs(self):
        return {
            "bot_id": "finance",
            "role": "finance",
            "display_name": "Finance worker",
            "email": "finance@example.com",
            "password": "a-very-long-random-worker-password",
        }

    def test_creates_inactive_registry_then_trusted_auth_user_and_activates(self):
        client = FakeClient()
        user_id = provision_bot_identity(client, **self.kwargs())

        self.assertEqual(user_id, "auth-user-1")
        self.assertEqual(client.registry["bot_id"], "finance")
        self.assertTrue(client.registry["is_active"])
        self.assertEqual(client.created_user["app_metadata"], {"bot_id": "finance"})
        self.assertTrue(client.created_user["email_confirm"])
        self.assertEqual(client.deleted_user, None)

    def test_refuses_existing_registry_entry(self):
        client = FakeClient(existing=[{"bot_id": "finance"}])
        with self.assertRaises(RuntimeError):
            provision_bot_identity(client, **self.kwargs())
        self.assertIsNone(client.created_user)

    def test_refuses_primary_bot_identity(self):
        client = FakeClient()
        args = self.kwargs()
        args["bot_id"] = "dimka"
        with self.assertRaises(ValueError):
            provision_bot_identity(client, **args)
        self.assertEqual(client.calls, [])

    def test_auth_failure_removes_inactive_registry_row(self):
        client = FakeClient(fail_create=True)
        with self.assertRaises(RuntimeError):
            provision_bot_identity(client, **self.kwargs())
        self.assertIsNone(client.registry)
        self.assertNotIn(("update", {"is_active": True}, [("bot_id", "finance")]), client.calls)

    def test_rejects_short_password(self):
        client = FakeClient()
        args = self.kwargs()
        args["password"] = "short"
        with self.assertRaises(ValueError):
            provision_bot_identity(client, **args)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
