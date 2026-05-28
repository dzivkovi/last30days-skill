"""Tests for threads source module.

Covers:
- _extract_core_subject (noise stripping)
- _parse_date (timestamp parsing across SC's various date fields)
- parse_threads_response (engine-side envelope unwrapping)
- search_threads request shape (REGRESSION TEST for issue #15:
  ScrapeCreators requires 'query' param, not 'keyword')
- _parse_items against the real ScrapeCreators response shape
  ({"success": True, "credits_remaining": N, "posts": [...]})
"""

import unittest
from unittest.mock import patch

from lib import threads


class TestExtractCoreSubject(unittest.TestCase):
    def test_strips_threads_noise(self):
        result = threads._extract_core_subject("latest trending news claude code")
        self.assertNotIn("latest", result)
        self.assertNotIn("trending", result)
        self.assertNotIn("news", result)
        self.assertIn("claude", result)

    def test_preserves_core(self):
        result = threads._extract_core_subject("react native")
        self.assertEqual(result, "react native")


class TestParseDate(unittest.TestCase):
    def test_taken_at_unix_timestamp(self):
        # 2024-06-15T12:00:00Z as unix
        item = {"taken_at": 1718452800}
        self.assertEqual(threads._parse_date(item), "2024-06-15")

    def test_created_at_iso(self):
        item = {"created_at": "2024-03-01T08:30:00.000Z"}
        self.assertEqual(threads._parse_date(item), "2024-03-01")

    def test_taken_at_preferred_over_created_at(self):
        item = {"taken_at": 1718452800, "created_at": "2024-06-14T12:00:00Z"}
        self.assertEqual(threads._parse_date(item), "2024-06-15")

    def test_none_returns_none(self):
        self.assertIsNone(threads._parse_date({}))

    def test_invalid_date_returns_none(self):
        self.assertIsNone(threads._parse_date({"created_at": "not-a-date"}))


class TestParseThreadsResponse(unittest.TestCase):
    """parse_threads_response unwraps the envelope produced by search_threads.

    search_threads returns {"items": [...]} (or with error key on failure).
    parse_threads_response extracts the items list.
    """

    def test_unwraps_items(self):
        envelope = {"items": [{"id": "TH1", "text": "hello"}]}
        self.assertEqual(threads.parse_threads_response(envelope), [{"id": "TH1", "text": "hello"}])

    def test_empty_envelope(self):
        self.assertEqual(threads.parse_threads_response({}), [])

    def test_error_envelope(self):
        envelope = {"items": [], "error": "HTTPError: HTTP 400: Bad Request"}
        self.assertEqual(threads.parse_threads_response(envelope), [])


class TestSearchThreadsRequestShape(unittest.TestCase):
    """REGRESSION TEST for issue #15.

    ScrapeCreators' /v1/threads/search endpoint requires the 'query' parameter.
    A regression in PR #393 (commit 80a1a47e, 2026-05-15) introduced
    params={"keyword": ...} which causes HTTP 400 on every request.

    These tests pin the correct call shape and the response-extraction path
    to prevent re-introduction.
    """

    @patch("lib.threads.http.get")
    def test_uses_query_param_not_keyword(self, mock_http_get):
        """The request MUST use 'query', not 'keyword'.

        ScrapeCreators returns HTTP 400 "Invalid parameters or missing required
        fields" if 'keyword' is sent instead of 'query'.
        """
        mock_http_get.return_value = {"success": True, "credits_remaining": 99, "posts": []}

        threads.search_threads(
            topic="tourism",
            from_date="2026-01-01",
            to_date="2026-05-27",
            token="fake-key",
        )

        self.assertEqual(mock_http_get.call_count, 1)
        kwargs = mock_http_get.call_args.kwargs
        params = kwargs.get("params", {})

        self.assertIn(
            "query",
            params,
            f"Request must send 'query' (ScrapeCreators API requirement). "
            f"Got params: {list(params.keys())}. Issue #15.",
        )
        self.assertNotIn(
            "keyword",
            params,
            f"Request must NOT send 'keyword' (causes HTTP 400 on ScrapeCreators). "
            f"Got params: {list(params.keys())}. Issue #15 / PR #393 regression.",
        )

    @patch("lib.threads.http.get")
    def test_query_value_is_core_subject(self, mock_http_get):
        """The query value should be the noise-stripped core subject.

        Verifies the param chain: topic -> _extract_core_subject -> http.get
        params['query']. Uses a literal expected value (not _extract_core_subject's
        output) so the assertion remains load-bearing if _extract_core_subject
        is ever changed accidentally.
        """
        mock_http_get.return_value = {"success": True, "credits_remaining": 99, "posts": []}

        threads.search_threads(
            topic="trending vacation",
            from_date="2026-01-01",
            to_date="2026-05-27",
            token="fake-key",
        )

        params = mock_http_get.call_args.kwargs.get("params", {})
        self.assertEqual(params["query"], "vacation")

    @patch("lib.threads.http.get")
    def test_endpoint_url_unchanged(self, mock_http_get):
        """URL must remain https://api.scrapecreators.com/v1/threads/search.

        Pinned because the URL is the second half of "what makes the request work."
        """
        mock_http_get.return_value = {"success": True, "credits_remaining": 99, "posts": []}

        threads.search_threads(
            topic="tourism",
            from_date="2026-01-01",
            to_date="2026-05-27",
            token="fake-key",
        )

        url = mock_http_get.call_args.args[0]
        self.assertEqual(url, "https://api.scrapecreators.com/v1/threads/search")

    @patch("lib.threads.http.get")
    def test_no_token_returns_error_without_call(self, mock_http_get):
        """Missing token short-circuits before any HTTP call."""
        result = threads.search_threads(
            topic="tourism",
            from_date="2026-01-01",
            to_date="2026-05-27",
            token=None,
        )
        mock_http_get.assert_not_called()
        self.assertEqual(result["items"], [])
        self.assertIn("SCRAPECREATORS_API_KEY", result["error"])


class TestSearchThreadsResponseShape(unittest.TestCase):
    """search_threads must correctly extract items from ScrapeCreators' real shape.

    Actual ScrapeCreators response:
        {
          "success": true,
          "credits_remaining": 20970,
          "posts": [ ... ]
        }

    The extractor at lib/threads.py:167-174 must surface posts[] as items.
    """

    @patch("lib.threads.http.get")
    def test_posts_key_extracted_to_items(self, mock_http_get):
        # Use a unix timestamp inside the search window (2024 range)
        mock_http_get.return_value = {
            "success": True,
            "credits_remaining": 20000,
            "posts": [
                {
                    "id": "TH1",
                    "code": "abc123",
                    "user": {"username": "traveler", "full_name": "Tina Traveler"},
                    "text": "Molise is the least visited region in all of Italy!",
                    "like_count": 42,
                    "reply_count": 5,
                    "repost_count": 2,
                    "created_at": "2024-06-15T12:00:00Z",
                },
            ],
        }

        result = threads.search_threads(
            topic="tourism",
            from_date="2024-01-01",
            to_date="2024-12-31",
            token="fake-key",
        )

        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["handle"], "traveler")
        self.assertEqual(item["display_name"], "Tina Traveler")
        self.assertIn("Molise", item["text"])
        self.assertEqual(item["engagement"]["likes"], 42)
        self.assertEqual(item["engagement"]["replies"], 5)
        self.assertEqual(item["engagement"]["reposts"], 2)
        self.assertEqual(item["date"], "2024-06-15")

    @patch("lib.threads.http.get")
    def test_empty_posts_returns_empty_items(self, mock_http_get):
        mock_http_get.return_value = {"success": True, "credits_remaining": 20000, "posts": []}

        result = threads.search_threads(
            topic="tourism",
            from_date="2024-01-01",
            to_date="2024-12-31",
            token="fake-key",
        )

        self.assertEqual(result["items"], [])
        self.assertNotIn("error", result)

    @patch("lib.threads.http.get")
    def test_http_error_returns_safe_envelope(self, mock_http_get):
        from lib.http import HTTPError

        mock_http_get.side_effect = HTTPError("HTTP 400: Bad Request", 400, "")

        result = threads.search_threads(
            topic="tourism",
            from_date="2024-01-01",
            to_date="2024-12-31",
            token="fake-key",
        )

        self.assertEqual(result["items"], [])
        self.assertIn("HTTP 400", result["error"])


if __name__ == "__main__":
    unittest.main()
