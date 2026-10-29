"""Offline contract tests; never send requests to ChatGPT."""
import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chatbridge_client import ChatBridgeClient, ChatBridgeError, OutcomeUnknown


class Reply(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()


class Contract(unittest.TestCase):
    def setUp(self):
        self.client = ChatBridgeClient(api_key="test")

    def test_explicit_modes(self):
        with self.assertRaises(ValueError):
            self.client.create_session(mode="standard")
        with self.assertRaises(ValueError):
            self.client.create_session(mode="temporary", project_id="g-p-id")
        with patch.object(self.client, "_request", return_value={}) as call:
            self.client.create_session(mode="persistent", project_id="g-p-id")
            self.assertEqual(call.call_args.args[2]["project_id"], "g-p-id")

    def test_idempotency_and_unknown(self):
        with self.assertRaises(ValueError):
            self.client.send("s", "hello", client_request_id="")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("reset")):
            with self.assertRaises(OutcomeUnknown) as caught:
                self.client.send("s", "hello", client_request_id="original-key")
        self.assertEqual(caught.exception.client_request_id, "original-key")

    def test_http_error(self):
        err = urllib.error.HTTPError("http://localhost", 409, "Conflict", {},
             Reply(json.dumps({"error": {"code": "duplicate_payload", "message": "Bad key"}}).encode()))
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ChatBridgeError) as caught:
                self.client.send("s", "hello", client_request_id="reuse")
        self.assertEqual((caught.exception.status, caught.exception.code),
                         (409, "duplicate_payload"))

    def test_read_only_and_wait(self):
        with patch("urllib.request.urlopen", return_value=Reply(b'{"status":"unknown"}')) as fetch:
            self.assertEqual(self.client.request("r")["status"], "unknown")
            self.assertEqual(fetch.call_args.args[0].get_method(), "GET")
        with patch.object(self.client, "request", side_effect=[
            {"status":"streaming"}, {"status":"unknown"}
        ]) as fetch, patch("time.sleep"):
            self.assertEqual(self.client.wait("r",poll_interval=1)["status"], "unknown")
            self.assertEqual(fetch.call_count,2)

    def test_reconcile_project_and_send(self):
        with self.assertRaises(ValueError):
            self.client.create_project("Test", client_request_id="")
        with patch.object(self.client,"_request",return_value={}) as call:
            self.client.reconcile("r")
            self.assertEqual(call.call_args.args[1], "/v1/requests/r/reconcile")
            self.client.create_project("Test",client_request_id="p-id")
            self.assertEqual(call.call_args.args[2]["memory_mode"],"default")
            self.client.send("s","text",client_request_id="m-id")
            self.assertEqual(call.call_args.kwargs["request_key"],"m-id")


if __name__ == "__main__":
    unittest.main()
