import unittest
from uuid import uuid4

from bot_worker import BotWorker, WorkerBatchResult


def task_message(**overrides):
    message = {
        "id": str(uuid4()),
        "task_id": str(uuid4()),
        "from_bot_id": "dimka",
        "to_bot_id": "finance",
        "message_type": "task",
        "payload": {"task_type": "math.add", "input": {"left": 2, "right": 3}},
        "hop_count": 0,
    }
    message.update(overrides)
    return message


class FakeQueue:
    def __init__(self, messages):
        self.messages = list(messages)
        self.claim_calls = []
        self.enqueued = []
        self.status_updates = []
        self.fail_enqueue = False
        self.fail_status = False

    def claim_inbox(self, *, limit, lease_seconds):
        self.claim_calls.append((limit, lease_seconds))
        return self.messages

    def enqueue(self, **kwargs):
        if self.fail_enqueue:
            raise RuntimeError("network failure")
        self.enqueued.append(kwargs)
        return {"id": str(uuid4())}

    def update_status(self, **kwargs):
        if self.fail_status:
            raise RuntimeError("network failure")
        self.status_updates.append(kwargs)
        return []


class BotWorkerTests(unittest.TestCase):
    def test_success_sends_result_to_sender_and_completes_task(self):
        message = task_message()
        queue = FakeQueue([message])
        worker = BotWorker(
            queue,
            {"math.add": lambda data: {"sum": data["left"] + data["right"]}},
        )

        result = worker.run_once()

        self.assertEqual(result, WorkerBatchResult(claimed=1, completed=1, failed=0, deferred=0))
        self.assertEqual(queue.claim_calls, [(10, 300)])
        reply = queue.enqueued[0]
        self.assertEqual(reply["to_bot_id"], "dimka")
        self.assertEqual(reply["message_type"], "result")
        self.assertEqual(reply["payload"], {"task_type": "math.add", "ok": True, "result": {"sum": 5}})
        self.assertEqual(reply["idempotency_key"], f"result:{message['id']}")
        self.assertEqual(reply["task_id"], message["task_id"])
        self.assertEqual(reply["parent_message_id"], message["id"])
        self.assertEqual(reply["hop_count"], 1)
        self.assertEqual(queue.status_updates[0]["status"], "completed")

    def test_unknown_task_type_is_failed_without_running_handler(self):
        queue = FakeQueue([task_message(payload={"task_type": "unknown", "input": {}})])
        worker = BotWorker(queue, {"math.add": lambda data: {"sum": 0}})

        result = worker.run_once()

        self.assertEqual(result.failed, 1)
        self.assertEqual(queue.enqueued, [])
        self.assertEqual(queue.status_updates[0]["last_error"], "unknown_task_type")

    def test_invalid_payload_is_failed(self):
        queue = FakeQueue([task_message(payload={"task_type": "math.add", "input": []})])
        worker = BotWorker(queue, {"math.add": lambda data: {"sum": 0}})

        result = worker.run_once()

        self.assertEqual(result.failed, 1)
        self.assertEqual(queue.enqueued, [])
        self.assertEqual(queue.status_updates[0]["last_error"], "invalid_task_message")

    def test_handler_exception_is_failed_without_leaking_exception_text(self):
        def broken(_data):
            raise RuntimeError("secret-token-should-not-be-stored")

        queue = FakeQueue([task_message()])
        worker = BotWorker(queue, {"math.add": broken})

        result = worker.run_once()

        self.assertEqual(result.failed, 1)
        self.assertEqual(queue.status_updates[0]["last_error"], "handler_error")
        self.assertNotIn("secret-token", str(queue.status_updates))

    def test_hop_limit_prevents_reply(self):
        queue = FakeQueue([task_message(hop_count=8)])
        worker = BotWorker(queue, {"math.add": lambda data: {"sum": 5}})

        result = worker.run_once()

        self.assertEqual(result.failed, 1)
        self.assertEqual(queue.enqueued, [])
        self.assertEqual(queue.status_updates[0]["last_error"], "hop_limit_exceeded")

    def test_result_delivery_failure_leaves_message_for_lease_retry(self):
        queue = FakeQueue([task_message()])
        queue.fail_enqueue = True
        worker = BotWorker(queue, {"math.add": lambda data: {"sum": 5}})

        result = worker.run_once()

        self.assertEqual(result.deferred, 1)
        self.assertEqual(queue.status_updates, [])

    def test_completion_update_failure_is_deferred_after_idempotent_reply(self):
        queue = FakeQueue([task_message()])
        queue.fail_status = True
        worker = BotWorker(queue, {"math.add": lambda data: {"sum": 5}})

        result = worker.run_once()

        self.assertEqual(result.deferred, 1)
        self.assertEqual(len(queue.enqueued), 1)

    def test_rejects_empty_handler_registry(self):
        with self.assertRaises(ValueError):
            BotWorker(FakeQueue([]), {})


if __name__ == "__main__":
    unittest.main()
