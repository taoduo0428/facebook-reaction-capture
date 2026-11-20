import json
import hashlib
import base64
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

import facebook_capture_addon_safe as addon
from facebook_capture_progress import summarize_reaction_records


def reaction_record(source, users, has_next, reaction_id=None):
    return {
        "friendly_name": "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection",
        "request_feedback_post_id": source,
        "request_reaction_id": reaction_id,
        "response_body": json.dumps(
            {
                "data": {
                    "node": {
                        "reactors": {
                            "edges": [{"node": {"id": user_id, "name": name}} for user_id, name in users],
                            "page_info": {"has_next_page": has_next},
                        }
                    }
                }
            }
        ),
    }


def share_record(source, shares, has_next):
    return {
        "friendly_name": "ResharesOfContentFeedQuery_At_Connection_Pagination",
        "request_feedback_post_id": source,
        "response_body": json.dumps(
            {
                "data": {
                    "node": {
                        "reshares": {
                            "edges": [
                                {"node": {"id": story_id, "actors": [{"id": actor_id, "name": name}]}}
                                for story_id, actor_id, name in shares
                            ],
                            "page_info": {"has_next_page": has_next},
                        }
                    }
                }
            }
        ),
    }


class LiveProgressTests(unittest.TestCase):
    def setUp(self):
        addon._reset_live_progress_state()
        addon.RECORD_LINE_COUNT = 0

    def test_reaction_progress_deduplicates_users_and_preserves_open_terminal(self):
        first = reaction_record("p1", [("u1", "One"), ("u2", "Two")], True)
        second = reaction_record("p1", [("u2", "Two"), ("u3", "Three")], False)
        self.assertTrue(addon._update_live_progress(10, first))
        self.assertTrue(addon._update_live_progress(11, second))
        snapshot = addon._live_progress_snapshot()
        source = snapshot["families"]["reaction"]["sources"]["p1"]
        self.assertEqual(source["query_records"], 2)
        self.assertEqual(source["user_count"], 3)
        self.assertEqual(source["first_line"], 10)
        self.assertEqual(source["latest_line"], 11)
        self.assertEqual(source["page_count"], 2)
        self.assertTrue(source["open_page_seen"])
        self.assertTrue(source["terminal_page_seen"])
        group = source["groups"]["__ALL__"]
        self.assertEqual(group["query_records"], 2)
        self.assertEqual(group["user_count"], 3)
        self.assertTrue(group["open_page_seen"])
        self.assertTrue(group["terminal_page_seen"])

    def test_share_progress_tracks_visible_share_rows(self):
        first = share_record("p2", [("s1", "a1", "A"), ("s2", "a2", "B")], True)
        second = share_record("p2", [("s2", "a2", "B"), ("s3", "a3", "C")], False)
        addon._update_live_progress(20, first)
        addon._update_live_progress(21, second)
        source = addon._live_progress_snapshot()["families"]["share"]["sources"]["p2"]
        self.assertEqual(source["query_records"], 2)
        self.assertEqual(source["user_count"], 3)
        self.assertEqual(source["page_count"], 2)
        self.assertTrue(source["open_page_seen"])
        self.assertTrue(source["terminal_page_seen"])

    def test_untracked_family_record_is_recorded_for_strict_boundary_fallback(self):
        record = reaction_record("", [("u1", "One")], True)
        record.pop("request_feedback_post_id", None)
        addon._update_live_progress(33, record)
        untracked = addon._live_progress_snapshot()["families"]["reaction"]["untracked"]
        self.assertEqual(untracked["query_records"], 1)
        self.assertEqual(untracked["first_line"], 33)
        self.assertEqual(untracked["latest_line"], 33)

    def test_response_feedback_source_can_drive_live_progress_without_request_source(self):
        target = base64.b64encode(b"feedback:122106880826437311").decode("ascii")
        record = reaction_record("", [("u1", "One")], True)
        record.pop("request_feedback_post_id", None)
        body = json.loads(record["response_body"])
        body["data"]["node"]["id"] = target
        record["response_body"] = json.dumps(body)
        record["response_feedback_post_id"] = "122106880826437311"
        addon._update_live_progress(34, record)
        snapshot = addon._live_progress_snapshot()
        source = snapshot["families"]["reaction"]["sources"]["122106880826437311"]
        self.assertEqual(source["user_count"], 1)
        self.assertEqual(source["query_records"], 1)

    def test_request_feedback_decoder_rejects_non_numeric_direct_source(self):
        self.assertEqual(addon._decode_feedback_post_id("123456"), "123456")
        self.assertIsNone(addon._decode_feedback_post_id("123_456"))
        self.assertIsNone(addon._decode_feedback_post_id("feedback:123456"))

    def test_atomic_snapshot_contains_no_sensitive_transport_fields(self):
        record = reaction_record("p3", [("u1", "One")], False)
        record.update(
            {
                "cookie": "secret",
                "authorization": "Bearer secret",
                "access_token": "secret",
                "token": "secret",
                "request_body": "secret",
            }
        )
        addon._update_live_progress(40, record)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "facebook-live-progress.json"
            addon._write_live_progress_snapshot(path)
            self.assertTrue(path.exists())
            self.assertFalse((Path(temp) / "facebook-live-progress.json.tmp").exists())
            raw = path.read_text(encoding="utf-8").lower()
            for forbidden in ("cookie", "authorization", "access_token", "request_body", "response_body", "bearer secret"):
                self.assertNotIn(forbidden, raw)
            parsed = json.loads(raw)
            self.assertEqual(parsed["families"]["reaction"]["sources"]["p3"]["user_count"], 1)

    def test_cursor_probe_matches_next_request_without_persisting_cursor_or_hash(self):
        context = {"request_feedback_post_id": "p4"}
        friendly = "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection"
        initial = addon._reaction_cursor_request_probe(friendly, context, urlencode({"variables": json.dumps({})}))
        self.assertFalse(initial["request_cursor_present"])
        self.assertFalse(initial["previous_end_cursor_available"])
        self.assertIsNone(initial["request_cursor_matches_previous_end"])

        cursor = "opaque-cursor-value"
        response_body = json.dumps(
            {"data": {"node": {"reactors": {"edges": [], "page_info": {"has_next_page": True, "end_cursor": cursor}}}}}
        )
        response_probe = addon._reaction_cursor_response_probe({"friendly_name": friendly, **context}, response_body)
        self.assertTrue(response_probe["response_end_cursor_present"])
        self.assertTrue(response_probe["response_has_next_page"])

        next_body = urlencode({"variables": json.dumps({"after": cursor})})
        matched = addon._reaction_cursor_request_probe(friendly, context, next_body)
        self.assertTrue(matched["request_cursor_present"])
        self.assertTrue(matched["previous_end_cursor_available"])
        self.assertTrue(matched["request_cursor_matches_previous_end"])

        wrong_body = urlencode({"variables": json.dumps({"after": "different-cursor"})})
        mismatched = addon._reaction_cursor_request_probe(friendly, context, wrong_body)
        self.assertFalse(mismatched["request_cursor_matches_previous_end"])

        persisted = json.dumps({**matched, **response_probe}, sort_keys=True)
        self.assertNotIn(cursor, persisted)
        self.assertNotIn(hashlib.sha256(cursor.encode("utf-8")).hexdigest(), persisted)

    def test_rate_limit_1675004_preserves_last_successful_cursor_checkpoint(self):
        context = {"request_feedback_post_id": "p-rate", "request_reaction_id": "all"}
        friendly = "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection"
        cursor = "checkpoint-cursor"
        success = json.dumps({"data": {"node": {"reactors": {"edges": [{"node": {"id": "u1", "name": "One"}}], "page_info": {"has_next_page": True, "end_cursor": cursor}}}}})
        first = addon._reaction_cursor_response_probe({"friendly_name": friendly, **context}, success)
        self.assertTrue(first["response_page_advanced"])
        self.assertEqual(first["response_reactor_page_index"], 1)
        self.assertFalse(first["response_rate_limited"])

        throttle = json.dumps({"errors": [{"message": "Rate limit exceeded", "severity": "CRITICAL", "code": 1675004}], "extensions": {"is_final": True}})
        limited = addon._reaction_cursor_response_probe({"friendly_name": friendly, **context}, throttle)
        self.assertTrue(limited["response_rate_limited"])
        self.assertEqual(limited["response_graphql_error_code"], 1675004)
        self.assertFalse(limited["response_page_advanced"])
        self.assertTrue(limited["response_cursor_checkpoint_preserved"])
        self.assertEqual(limited["response_reactor_page_index"], 2)

        next_body = urlencode({"variables": json.dumps({"after": cursor})})
        checkpoint = addon._reaction_cursor_request_probe(friendly, context, next_body)
        self.assertTrue(checkpoint["request_cursor_matches_previous_end"])

        next_cursor = "next-checkpoint-cursor"
        recovered = addon._reaction_cursor_response_probe(
            {"friendly_name": friendly, **context},
            json.dumps({"data": {"node": {"reactors": {"edges": [{"node": {"id": "u2", "name": "Two"}}], "page_info": {"has_next_page": True, "end_cursor": next_cursor}}}}}),
        )
        self.assertTrue(recovered["response_page_advanced"])
        self.assertEqual(recovered["response_reactor_page_index"], 2)
        self.assertEqual(recovered["response_reactor_edge_count"], 1)

    def test_rate_limit_state_is_exposed_in_live_progress_without_cursor_data(self):
        record = reaction_record("p-live-rate", [("u1", "One")], True)
        record.update({"response_rate_limited": False, "response_graphql_error_code": None})
        addon._update_live_progress(50, record)
        throttled = {
            "friendly_name": "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection",
            "request_feedback_post_id": "p-live-rate",
            "response_body": json.dumps({"errors": [{"message": "Rate limit exceeded", "code": 1675004}]}),
            "response_rate_limited": True,
            "response_graphql_error_code": 1675004,
        }
        addon._update_live_progress(51, throttled)
        source = addon._live_progress_snapshot()["families"]["reaction"]["sources"]["p-live-rate"]
        self.assertEqual(source["rate_limit_count"], 1)
        self.assertTrue(source["latest_rate_limited"])
        self.assertEqual(source["latest_graphql_error_code"], 1675004)

    def test_generic_368_feature_block_updates_safe_session_state(self):
        body = json.dumps(
            {
                "errors": [
                    {
                        "message": "A server error field_exception occured.",
                        "code": 3252001,
                        "api_error_code": 368,
                        "summary": "temporary feature block",
                    }
                ]
            }
        )
        probe = addon._graphql_error_probe(body)
        self.assertTrue(probe["response_temporary_feature_block"])
        self.assertEqual(probe["response_graphql_error_code"], 3252001)
        self.assertEqual(probe["response_api_error_code"], 368)
        self.assertFalse(probe["response_rate_limited"])

        record = {
            "friendly_name": "FbBloksAsyncComponentsQuery-com.bloks.www.screen_query.DLPFullScreenSEScreenQuery",
            **probe,
        }
        self.assertTrue(addon._update_live_progress(61, record))
        snapshot = addon._live_progress_snapshot()
        session = snapshot["session"]
        self.assertEqual(snapshot["version"], 2)
        self.assertEqual(session["temporary_feature_block_count"], 1)
        self.assertEqual(session["latest_temporary_feature_block_line"], 61)
        self.assertEqual(session["latest_temporary_feature_block_graphql_code"], 3252001)
        self.assertEqual(session["latest_temporary_feature_block_api_code"], 368)
        self.assertNotIn("summary", json.dumps(session).lower())

    def test_successful_reactor_page_clears_unrelated_background_feature_block(self):
        records = [
            (70, reaction_record("p-window", [("u1", "One")], True)),
            (
                71,
                {
                    "friendly_name": "FbBloksAsyncComponentsQuery-com.bloks.www.screen_query.DLPFullScreenSEScreenQuery",
                    "response_temporary_feature_block": True,
                    "response_graphql_error_code": 3252001,
                    "response_api_error_code": 368,
                },
            ),
        ]
        summary = summarize_reaction_records(records, "p-window")
        self.assertFalse(summary["session_restricted"])
        self.assertEqual(summary["session_restriction_line"], 0)

    def test_reactor_feature_block_remains_active_after_an_earlier_successful_page(self):
        records = [
            (70, reaction_record("p-window", [("u1", "One")], True)),
            (
                71,
                {
                    "friendly_name": "FbShortsIGMediaReactorsQuery",
                    "response_temporary_feature_block": True,
                    "response_graphql_error_code": 3252001,
                    "response_api_error_code": 368,
                },
            ),
        ]
        summary = summarize_reaction_records(records, "p-window")
        self.assertTrue(summary["session_restricted"])
        self.assertEqual(summary["session_restriction_line"], 71)
        self.assertEqual(summary["session_restriction_graphql_code"], 3252001)
        self.assertEqual(summary["session_restriction_api_error_code"], 368)

    def test_cursor_probe_does_not_leak_into_live_snapshot(self):
        context = {"request_feedback_post_id": "p5"}
        friendly = "FeedbackReactorsGraphService"
        cursor = "do-not-persist"
        addon._reaction_cursor_response_probe(
            {"friendly_name": friendly, **context},
            json.dumps({"data": {"node": {"reactors": {"page_info": {"has_next_page": True, "end_cursor": cursor}}}}}),
        )
        raw = json.dumps(addon._live_progress_snapshot(), sort_keys=True)
        self.assertNotIn(cursor, raw)
        self.assertNotIn(hashlib.sha256(cursor.encode("utf-8")).hexdigest(), raw)

    def test_cursor_probe_can_match_unknown_nested_cursor_key_without_leaking_it(self):
        context = {"request_feedback_post_id": "p6"}
        friendly = "FeedbackReactorsGraphService_At_Connection_Pagination_Feedback_reactors_connection"
        cursor = "nested-opaque-cursor"
        addon._reaction_cursor_response_probe(
            {"friendly_name": friendly, **context},
            json.dumps({"data": {"node": {"reactors": {"page_info": {"has_next_page": True, "end_cursor": cursor}}}}}),
        )
        body = urlencode({"variables": json.dumps({"pagination": {"opaque_next_marker": cursor}, "count": 25})})
        result = addon._reaction_cursor_request_probe(friendly, context, body)
        self.assertTrue(result["request_cursor_present"])
        self.assertTrue(result["previous_end_cursor_available"])
        self.assertTrue(result["request_cursor_matches_previous_end"])
        raw = json.dumps(result, sort_keys=True)
        self.assertNotIn(cursor, raw)
        self.assertNotIn(hashlib.sha256(cursor.encode("utf-8")).hexdigest(), raw)

    def test_cursor_probe_skips_sensitive_variable_subtrees(self):
        secret = "secret-token-value"
        hashes = addon._request_scalar_hashes_from_values(
            {"variables": json.dumps({"access_token": secret, "nested": {"authorization": secret}, "count": 25})}
        )
        self.assertNotIn(hashlib.sha256(secret.encode("utf-8")).hexdigest(), hashes)

    def test_request_structure_fingerprint_is_value_independent_and_secret_free(self):
        first = {
            "doc_id": "111",
            "variables": json.dumps({
                "feedback_id": "post-a",
                "after": "cursor-a",
                "count": 25,
                "access_token": "secret-a",
                "nested": {"session_id": "secret-session", "flag": True},
            }),
            "fb_api_req_friendly_name": "FeedbackReactorsGraphService",
            "fb_dtsg": "secret-form",
        }
        second = {
            "doc_id": "999",
            "variables": json.dumps({
                "feedback_id": "post-b",
                "after": "cursor-b",
                "count": 50,
                "access_token": "secret-b",
                "nested": {"session_id": "other-session", "flag": False},
            }),
            "fb_api_req_friendly_name": "FeedbackReactorsGraphService",
            "fb_dtsg": "other-secret-form",
        }
        headers = {"x-fb-friendly-name": "FeedbackReactorsGraphService", "x-fb-lsd": "secret-header"}
        a = addon._safe_request_structure(first, headers, "FeedbackReactorsGraphService")
        b = addon._safe_request_structure(second, headers, "FeedbackReactorsGraphService")
        self.assertEqual(a["request_context_fingerprint"], b["request_context_fingerprint"])
        raw = json.dumps(a, sort_keys=True).lower()
        for forbidden in ("secret-a", "secret-session", "secret-form", "secret-header", "fb_dtsg", "access_token", "session_id"):
            self.assertNotIn(forbidden, raw)
        changed = addon._safe_request_structure(second, {**headers, "x-asbd-id": "present"}, "FeedbackReactorsGraphService")
        self.assertNotEqual(a["request_context_fingerprint"], changed["request_context_fingerprint"])


if __name__ == "__main__":
    unittest.main()
