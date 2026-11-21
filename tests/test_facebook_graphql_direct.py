from pathlib import Path
import gzip
import json
import unittest

import zstandard

from facebook_graphql_direct import (
    REACTOR_FIELDS,
    BlockedError,
    GraphQLClient,
    RateLimitedError,
    decode_body,
    discover_post_ids,
    find_page_info,
    is_blocked,
    is_rate_limited,
    iter_reactors,
    iter_shares,
    normalize_post_id,
)


class DecodeBodyTests(unittest.TestCase):
    def test_gzip_and_zstd_bodies_decode(self):
        payload = b'{"data":{"feedback":{}}}'
        self.assertEqual(decode_body(gzip.compress(payload)), payload)
        self.assertEqual(
            decode_body(zstandard.ZstdCompressor().compress(payload), "zstd"), payload
        )

    def test_plain_body_passes_through(self):
        self.assertEqual(decode_body(b"plain"), b"plain")
        self.assertEqual(decode_body(b""), b"")


class FindPageInfoTests(unittest.TestCase):
    def test_preferred_connection_wins_over_decoy(self):
        payload = {
            "data": {
                "feedback": {
                    "top_story": {"page_info": {"has_next_page": True, "end_cursor": "WRONG"}},
                    "unified_reactors": {
                        "page_info": {"has_next_page": True, "end_cursor": "RIGHT"}
                    },
                }
            }
        }
        self.assertEqual(
            find_page_info(payload, REACTOR_FIELDS)["end_cursor"], "RIGHT"
        )

    def test_falls_back_to_any_page_info_without_preference(self):
        payload = {"a": {"page_info": {"has_next_page": False, "end_cursor": "ONLY"}}}
        self.assertEqual(find_page_info(payload)["end_cursor"], "ONLY")

    def test_missing_page_info_returns_none(self):
        self.assertIsNone(find_page_info({"data": {}}))


class IterReactorsTests(unittest.TestCase):
    def test_unified_reactors_edges_yield_identities(self):
        payload = {
            "data": {
                "feedback": {
                    "unified_reactors": {
                        "edges": [
                            {"node": {"id": "1001", "name": "One"},
                             "feedback_reaction_info": {"id": "1635"}},
                            {"node": {"strong_id__": "1002", "name": "Two"}},
                            {"node": {"name": "no-id"}},
                        ]
                    }
                }
            }
        }
        rows = list(iter_reactors(payload))
        self.assertEqual([row[0] for row in rows], ["1001", "1002"])
        self.assertEqual(rows[0][2], "1635")
        self.assertIsNone(rows[1][2])

    def test_non_connection_fields_are_ignored(self):
        payload = {"data": {"feedback": {"reactors": {"count": 949}}}}
        self.assertEqual(list(iter_reactors(payload)), [])


class IterSharesTests(unittest.TestCase):
    """Reshares nest the sharer inside node.actors[], not at edge level."""

    @staticmethod
    def _payload(edges):
        return {
            "data": {
                "fetch__Feedback": {
                    "id": "F",
                    "reshares": {
                        "edges": edges,
                        "page_info": {"has_next_page": False, "end_cursor": None},
                    },
                }
            }
        }

    def test_reshares_yield_actor_and_story_identity(self):
        payload = self._payload(
            [
                {
                    "node": {
                        "id": "S1",
                        "url": "https://x/1",
                        "post_id": "P1",
                        "actors": [{"strong_id__": "1001", "name": "One"}],
                    }
                },
                {
                    "node": {
                        "id": "S2",
                        "url": "https://x/2",
                        "post_id": "P2",
                        "actors": [{"id": "1002", "name": "Two"}],
                    }
                },
            ]
        )
        rows = list(iter_shares(payload))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["actor_id"], "1001")
        self.assertEqual(rows[0]["share_story_id"], "S1")
        self.assertEqual(rows[0]["share_post_id"], "P1")
        self.assertEqual(rows[1]["actor_id"], "1002")

    def test_edges_without_actor_are_skipped(self):
        payload = self._payload([{"node": {"id": "S3", "url": "https://x/3"}}])
        self.assertEqual(list(iter_shares(payload)), [])

    def test_missing_reshares_connection_returns_empty(self):
        self.assertEqual(list(iter_shares({"data": {}})), [])


class ClientProxyTests(unittest.TestCase):
    """graph.facebook.com is unreachable without the project upstream proxy."""

    def test_explicit_proxy_is_applied_to_session(self):
        client = GraphQLClient("token", proxy="http://127.0.0.1:7897")
        self.assertEqual(client.session.proxies.get("https"), "http://127.0.0.1:7897")
        self.assertEqual(client.session.proxies.get("http"), "http://127.0.0.1:7897")

    def test_proxy_falls_back_to_environment(self):
        import os

        os.environ["FACEBOOK_UPSTREAM_PROXY"] = "http://127.0.0.1:9999"
        try:
            client = GraphQLClient("token")
            self.assertEqual(client.session.proxies.get("https"), "http://127.0.0.1:9999")
        finally:
            os.environ.pop("FACEBOOK_UPSTREAM_PROXY", None)

    def test_no_proxy_leaves_session_clean(self):
        import os

        os.environ.pop("FACEBOOK_UPSTREAM_PROXY", None)
        client = GraphQLClient("token")
        self.assertFalse(client.session.proxies)


class PaginationDefaultsTests(unittest.TestCase):
    """Defaults must stay conservative: the account is the user's, not a lab."""

    def test_default_sleep_keeps_a_safety_margin(self):
        import inspect

        signature = inspect.signature(GraphQLClient.paginate)
        default = signature.parameters["sleep_seconds"].default
        self.assertGreaterEqual(default, 0.9)

    def test_default_worker_count_is_low(self):
        import inspect

        from facebook_graphql_direct import main

        # argparse defaults are not introspectable, so assert the source contract.
        source = Path(__file__).resolve().parents[1] / "facebook_graphql_direct.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('default=2', text)
        self.assertIn('default=1.0', text)


class RateLimitTests(unittest.TestCase):
    """Throttle handling must react to Facebook's own error codes."""

    def test_known_throttle_codes_are_detected(self):
        for code in (1675004, 3252001, 368):
            with self.subTest(code=code):
                self.assertTrue(is_rate_limited({"errors": [{"code": code}]}))

    def test_message_based_throttle_is_detected(self):
        payload = {"errors": [{"message": "Rate limit exceeded"}]}
        self.assertTrue(is_rate_limited(payload))

    def test_normal_payload_is_not_throttled(self):
        self.assertFalse(is_rate_limited({"data": {"feedback": {}}}))
        self.assertFalse(is_rate_limited({}))

    def test_paginate_backs_off_after_throttle(self):
        def page(cursor, has_next, edges, errors=None):
            payload = {
                "data": {"feedback": {"unified_reactors": {
                    "edges": edges,
                    "page_info": {"has_next_page": has_next, "end_cursor": cursor},
                }}}
            }
            if errors:
                payload["errors"] = errors
            return payload

        responses = [
            page("C1", True, [{"node": {"id": "1"}}]),
            page("C2", True, [], [{"code": 1675004}]),
            page(None, False, []),
        ]
        client = _FakeClient(responses)
        import facebook_graphql_direct as module

        original = module.time.sleep
        slept = []
        module.time.sleep = lambda seconds: slept.append(seconds)
        try:
            list(
                client.paginate(
                    "FeedbackReactorsGraphService",
                    "DOC",
                    {"reactors_connection_first": 25},
                    page_info_fields=REACTOR_FIELDS,
                    sleep_seconds=1.0,
                )
            )
        finally:
            module.time.sleep = original
        self.assertEqual(len(slept), 2, slept)
        self.assertAlmostEqual(slept[0], 1.0, places=1)
        self.assertGreaterEqual(slept[1], 2.0, "throttle must double the delay")

    def test_throttled_terminal_page_raises_instead_of_truncating(self):
        # Facebook answers a throttled request with HTTP 200 and no page_info.
        # Treating that as the final page silently truncates the roster to one
        # page, which is indistinguishable from a genuinely short list.
        payload = {
            "data": {"feedback": {"unified_reactors": {
                "edges": [],
                "page_info": {"has_next_page": False, "end_cursor": None},
            }}},
            "errors": [{"code": 1675004, "message": "Rate limit exceeded"}],
        }
        client = _FakeClient([payload])
        with self.assertRaises(RateLimitedError):
            list(
                client.paginate(
                    "FeedbackReactorsGraphService",
                    "DOC",
                    {"reactors_connection_first": 25},
                    page_info_fields=REACTOR_FIELDS,
                    sleep_seconds=0,
                )
            )


class _FakeClient(GraphQLClient):
    def __init__(self, responses):
        self.token = "test"
        self.calls = []
        self._responses = list(responses)

    def call(self, friendly, doc_id, variables):
        self.calls.append({"friendly": friendly, "doc_id": doc_id, "variables": dict(variables)})
        return self._responses.pop(0)




class FreezeThawTests(unittest.TestCase):
    """An interrupted batch must resume from the last cursor, not page 1."""

    @staticmethod
    def _page(cursor, has_next, user_id):
        return {
            "data": {"feedback": {"unified_reactors": {
                "edges": [{"node": {"id": user_id}}],
                "page_info": {"has_next_page": has_next, "end_cursor": cursor},
            }}}
        }

    def test_state_is_written_then_resumed(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"

            first = _FakeClient([self._page("C1", True, "1"), self._page("C2", True, "2")])
            list(
                first.paginate(
                    "FeedbackReactorsGraphService",
                    "DOC",
                    {"reactors_connection_first": 25},
                    page_info_fields=REACTOR_FIELDS,
                    max_pages=2,
                    sleep_seconds=0.0,
                    state_path=state,
                )
            )
            self.assertTrue(state.is_file())
            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(saved["cursor"], "C2")
            self.assertEqual(saved["pages"], 2)

            resumed = _FakeClient([self._page("C3", False, "3")])
            list(
                resumed.paginate(
                    "FeedbackReactorsGraphService",
                    "DOC",
                    {"reactors_connection_first": 25},
                    next_friendly="FeedbackReactorsGraphService_At_Connection_Pagination_x",
                    next_doc_id="DOC2",
                    next_variables={
                        "reactors_connection_first": 25,
                        "reactors_connection_after_cursor": "",
                    },
                    page_info_fields=REACTOR_FIELDS,
                    sleep_seconds=0.0,
                    state_path=state,
                )
            )
            # The resumed call must send the stored cursor, not start over.
            self.assertEqual(
                resumed.calls[0]["variables"]["reactors_connection_after_cursor"], "C2"
            )
            # A terminal page clears the state file.
            self.assertFalse(state.is_file())

    def test_state_for_another_friendly_is_ignored(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(
                json.dumps({"friendly": "OtherQuery", "cursor": "X", "pages": 5}),
                encoding="utf-8",
            )
            client = _FakeClient([self._page(None, False, "1")])
            list(
                client.paginate(
                    "FeedbackReactorsGraphService",
                    "DOC",
                    {"reactors_connection_first": 25},
                    page_info_fields=REACTOR_FIELDS,
                    sleep_seconds=0.0,
                    state_path=state,
                )
            )
            self.assertNotIn("reactors_connection_after_cursor", client.calls[0]["variables"])


class BlockDetectionTests(unittest.TestCase):
    """Mobile endpoint can answer 200 with a checkpoint page instead of JSON."""

    def test_temporary_block_variants_are_detected(self):
        for text in (
            "You are temporarily blocked",
            "You\u2019re temporarily blocked",
            "You can't use this feature at the moment",
            "We suspended your account",
            "Login approval needed",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_blocked(text))

    def test_normal_payload_is_not_a_block(self):
        self.assertFalse(is_blocked("normal graphql payload"))
        self.assertFalse(is_blocked(""))

    def test_rate_limited_error_is_distinct_from_block(self):
        self.assertTrue(issubclass(RateLimitedError, RuntimeError))
        self.assertTrue(issubclass(BlockedError, RuntimeError))
        self.assertFalse(issubclass(BlockedError, RateLimitedError))


class _FlakyClient(_FakeClient):
    """Raises RateLimitedError for the first N calls, then answers normally."""

    def __init__(self, failures, then_payload):
        super().__init__([then_payload])
        self.failures = failures
        self.attempts = 0

    def call(self, friendly, doc_id, variables):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise RateLimitedError("HTTP 429")
        return super().call(friendly, doc_id, variables)


class RateLimitRetryTests(unittest.TestCase):
    def test_429_is_retried_before_giving_up(self):
        terminal = {
            "data": {"feedback": {"unified_reactors": {
                "edges": [{"node": {"id": "1"}}],
                "page_info": {"has_next_page": False, "end_cursor": None},
            }}}
        }
        client = _FlakyClient(2, terminal)
        import facebook_graphql_direct as module

        original = module.time.sleep
        slept = []
        module.time.sleep = lambda seconds: slept.append(seconds)
        try:
            pages = list(
                client.paginate(
                    "FeedbackReactorsGraphService",
                    "DOC",
                    {"reactors_connection_first": 25},
                    page_info_fields=REACTOR_FIELDS,
                    sleep_seconds=1.0,
                )
            )
        finally:
            module.time.sleep = original
        self.assertEqual(len(pages), 1)
        self.assertEqual(client.attempts, 3)
        self.assertEqual(len(slept), 2)
        self.assertGreaterEqual(slept[0], 1.0)

    def test_persistent_429_raises(self):
        client = _FlakyClient(99, {"data": {}})
        import facebook_graphql_direct as module

        original = module.time.sleep
        module.time.sleep = lambda seconds: None
        try:
            with self.assertRaises(RateLimitedError):
                list(
                    client.paginate(
                        "FeedbackReactorsGraphService",
                        "DOC",
                        {"reactors_connection_first": 25},
                        sleep_seconds=0.0,
                    )
                )
        finally:
            module.time.sleep = original


class PaginateTests(unittest.TestCase):
    """The cursor key lives only in the pagination template, not in page 1."""

    def test_second_page_uses_pagination_template_and_cursor_field(self):
        first_page = {
            "data": {"feedback": {"unified_reactors": {
                "edges": [{"node": {"id": "1"}}],
                "page_info": {"has_next_page": True, "end_cursor": "CURSOR-1"},
            }}}
        }
        second_page = {
            "data": {"feedback": {"unified_reactors": {
                "edges": [{"node": {"id": "2"}}],
                "page_info": {"has_next_page": False, "end_cursor": None},
            }}}
        }
        client = _FakeClient([first_page, second_page])
        pages = list(
            client.paginate(
                "FeedbackReactorsGraphService",
                "DOC-1",
                {"reactors_connection_first": 25, "feedback_id": "F"},
                next_friendly="FeedbackReactorsGraphService_At_Connection_Pagination_x",
                next_doc_id="DOC-2",
                next_variables={
                    "reactors_connection_first": 25,
                    "reactors_connection_after_cursor": "",
                },
                page_info_fields=REACTOR_FIELDS,
                first=25,
                sleep_seconds=0,
            )
        )
        self.assertEqual(len(pages), 2)
        self.assertEqual(client.calls[0]["friendly"], "FeedbackReactorsGraphService")
        self.assertEqual(client.calls[0]["variables"]["reactors_connection_first"], 25)
        self.assertEqual(
            client.calls[1]["friendly"],
            "FeedbackReactorsGraphService_At_Connection_Pagination_x",
        )
        self.assertEqual(client.calls[1]["doc_id"], "DOC-2")
        self.assertEqual(
            client.calls[1]["variables"]["reactors_connection_after_cursor"], "CURSOR-1"
        )
        # The decoy default must never be used when the template names the cursor.
        self.assertNotIn("after", client.calls[1]["variables"])

    def test_terminal_page_stops_without_extra_request(self):
        terminal = {
            "data": {"feedback": {"unified_reactors": {
                "edges": [], "page_info": {"has_next_page": False, "end_cursor": None},
            }}}
        }
        client = _FakeClient([terminal])
        pages = list(
            client.paginate(
                "FeedbackReactorsGraphService",
                "DOC",
                {"reactors_connection_first": 25},
                page_info_fields=REACTOR_FIELDS,
                sleep_seconds=0,
            )
        )
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()

class PostIdNormalizationTests(unittest.TestCase):
    def test_plain_numeric_id_passes_through(self):
        self.assertEqual(normalize_post_id("1691321539667513"), "1691321539667513")

    def test_composite_id_keeps_trailing_segment(self):
        self.assertEqual(
            normalize_post_id("642731516961998_1797199558181849"),
            "1797199558181849",
        )

    def test_non_numeric_and_short_values_are_rejected(self):
        for raw in (None, "", "pfbid02abc", "12345", "abc_def", "   "):
            self.assertIsNone(normalize_post_id(raw), raw)

    def test_whitespace_is_trimmed(self):
        self.assertEqual(normalize_post_id("  1691321539667513  "), "1691321539667513")


class DiscoverPostIdsTests(unittest.TestCase):
    def _write(self, rows):
        import tempfile

        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8-sig", newline=""
        )
        handle.write("post_id,author_id,reaction_count\n")
        for post_id, author, count in rows:
            handle.write(f"{post_id},{author},{count}\n")
        handle.close()
        return Path(handle.name)

    def test_harvest_dedupes_and_honours_the_limit(self):
        csv_path = self._write([
            ("1691321539667513", "1", "949"),
            ("1643052717863108", "2", "974"),
            ("642731516961998_1797199558181849", "3", "117"),
            ("1643052717863108", "2", "974"),
            ("1028382856935747", "4", "2021"),
        ])
        ids = discover_post_ids(csv_path, exclude="1691321539667513", limit=2)
        self.assertEqual(ids, ["1643052717863108", "1797199558181849"])

    def test_exclude_removes_the_focus_post(self):
        csv_path = self._write([
            ("1691321539667513", "1", "949"),
            ("1643052717863108", "2", "974"),
        ])
        ids = discover_post_ids(csv_path, exclude="1691321539667513", limit=0)
        self.assertEqual(ids, ["1643052717863108"])

    def test_missing_csv_yields_no_ids(self):
        self.assertEqual(discover_post_ids(Path("does-not-exist.csv")), [])
