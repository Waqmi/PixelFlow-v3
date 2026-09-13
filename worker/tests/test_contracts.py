from __future__ import annotations

import unittest

from pixelflow_core.contracts import Message, ProtocolError
from pixelflow_core.worker import handle_message


class ProtocolTests(unittest.TestCase):
    def test_round_trip_preserves_unicode_payload(self) -> None:
        message = Message(1, "req-1", "ping", {"主题": "夜间"})
        self.assertEqual(Message.from_json(message.to_json()), message)

    def test_invalid_version_is_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            Message.from_json('{"protocol":2,"request_id":"r","type":"ping","payload":{}}')

    def test_unknown_request_returns_error(self) -> None:
        response, should_exit = handle_message(Message(1, "req-2", "nope", {}))
        self.assertIn('"error"', response)
        self.assertFalse(should_exit)

    def test_shutdown_is_terminal(self) -> None:
        response, should_exit = handle_message(Message(1, "req-3", "shutdown", {}))
        self.assertIn('"done"', response)
        self.assertTrue(should_exit)


if __name__ == "__main__":
    unittest.main()

