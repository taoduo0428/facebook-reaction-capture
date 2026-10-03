import json
import tempfile
import unittest
from pathlib import Path

from mitmproxy import connection, http, io

import facebook_raw_flow_writer as raw_writer
from inspect_facebook_raw_capture import _export_exact, _summary_row


def make_flow(host="graph.facebook.com"):
    client = connection.Client(peername=("127.0.0.1", 50000), sockname=("127.0.0.1", 8081))
    server = connection.Server(address=(host, 443))
    flow = http.HTTPFlow(client, server)
    flow.request = http.Request.make(
        "POST",
        f"https://{host}/api/graphql?locale=en_US",
        "fb_api_req_friendly_name=FeedbackReactorsGraphService&variables=%7B%22after%22%3A%22cursor-secret%22%7D",
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "x-fb-friendly-name": "FeedbackReactorsGraphService",
            "cookie": "c_user=123; xs=secret-cookie",
            "authorization": "OAuth secret-token",
        },
    )
    flow.response = http.Response.make(
        200,
        b'{"data":{"node":{"reactors":{"edges":[]}}}}',
        headers={"content-type": "application/json", "set-cookie": "fr=secret-response-cookie"},
    )
    return flow


class RawCaptureTests(unittest.TestCase):
    def test_writer_preserves_exact_allowed_http_flow_and_writes_safe_index(self):
        flow = make_flow()
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            old_flow = raw_writer.RAW_FLOW_FILE
            old_index = raw_writer.RAW_INDEX_FILE
            raw_writer.RAW_FLOW_FILE = temp_path / "raw-facebook-http.mitm"
            raw_writer.RAW_INDEX_FILE = temp_path / "raw-facebook-http-index.jsonl"
            try:
                writer = raw_writer.RawFacebookFlowWriter()
                writer.response(flow)
                writer.done()

                self.assertTrue(raw_writer.RAW_FLOW_FILE.exists())
                self.assertTrue(raw_writer.RAW_INDEX_FILE.exists())
                with raw_writer.RAW_FLOW_FILE.open("rb") as handle:
                    stored = list(io.FlowReader(handle).stream())
                self.assertEqual(len(stored), 1)
                stored_flow = stored[0]
                self.assertEqual(stored_flow.request.raw_content, flow.request.raw_content)
                self.assertEqual(stored_flow.response.raw_content, flow.response.raw_content)
                self.assertEqual(stored_flow.request.headers.get("cookie"), "c_user=123; xs=secret-cookie")
                self.assertEqual(stored_flow.request.headers.get("authorization"), "OAuth secret-token")
                self.assertEqual(stored_flow.response.headers.get("set-cookie"), "fr=secret-response-cookie")

                index_text = raw_writer.RAW_INDEX_FILE.read_text(encoding="utf-8")
                index = json.loads(index_text)
                self.assertEqual(index["flow_id"], flow.id)
                self.assertEqual(index["friendly_name"], "FeedbackReactorsGraphService")
                self.assertTrue(index["graphql_like"])
                for secret in ("secret-cookie", "secret-token", "cursor-secret", "secret-response-cookie"):
                    self.assertNotIn(secret, index_text)
            finally:
                raw_writer.RAW_FLOW_FILE = old_flow
                raw_writer.RAW_INDEX_FILE = old_index

    def test_writer_ignores_non_meta_host(self):
        flow = make_flow("example.com")
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            old_flow = raw_writer.RAW_FLOW_FILE
            old_index = raw_writer.RAW_INDEX_FILE
            raw_writer.RAW_FLOW_FILE = temp_path / "raw-facebook-http.mitm"
            raw_writer.RAW_INDEX_FILE = temp_path / "raw-facebook-http-index.jsonl"
            try:
                writer = raw_writer.RawFacebookFlowWriter()
                writer.response(flow)
                writer.done()
                self.assertFalse(raw_writer.RAW_FLOW_FILE.exists())
                self.assertFalse(raw_writer.RAW_INDEX_FILE.exists())
            finally:
                raw_writer.RAW_FLOW_FILE = old_flow
                raw_writer.RAW_INDEX_FILE = old_index

    def test_inspector_summary_is_secret_free_and_exact_export_preserves_bytes(self):
        flow = make_flow()
        summary = json.dumps(_summary_row(1, flow), ensure_ascii=False)
        for secret in ("secret-cookie", "secret-token", "cursor-secret", "secret-response-cookie"):
            self.assertNotIn(secret, summary)
        with tempfile.TemporaryDirectory() as temp:
            result = _export_exact(flow, Path(temp))
            self.assertEqual(Path(result["request_body"]).read_bytes(), flow.request.raw_content)
            self.assertEqual(Path(result["response_body"]).read_bytes(), flow.response.raw_content)
            self.assertIn(b"cookie: c_user=123; xs=secret-cookie", Path(result["request_head"]).read_bytes().lower())
            self.assertIn(b"set-cookie: fr=secret-response-cookie", Path(result["response_head"]).read_bytes().lower())


if __name__ == "__main__":
    unittest.main()
