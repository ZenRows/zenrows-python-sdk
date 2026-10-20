"""Real behavior tests for fetch()/extract() and the client-level gaps they share.

Existing tests in test_client.py only assert on *call arguments* passed to
`Session.request` — none of them exercise what the client actually does with
a response, an error status, an unvalidated mode string, or the constructor/
context-manager surface. These tests close that gap: they assert on what
`fetch()`/`extract()`/`get()` actually *return* and *raise* (or don't).
"""

import warnings
from unittest import IsolatedAsyncioTestCase, TestCase, mock

from requests import Response, Session

from zenrows import ZenRowsClient

apikey = "APIKEY"
url = "http://example.com"
api_url_base = "https://api.zenrows.com/v1/"


def _fake_response(status_code: int, body: bytes = b"") -> Response:
    response = Response()
    response.status_code = status_code
    response._content = body
    return response


class TestFetchExtractErrorHandling(TestCase):
    """The client never raises on a non-2xx status — it hands the caller the
    real Response so they can check `.status_code`/`.text` themselves. That's
    a deliberate design choice (no `raise_for_status()` anywhere in `_worker`),
    not an oversight — these tests pin that behavior down so a future change
    can't silently start raising (or silently start swallowing errors) without
    a test failing.
    """

    def setUp(self):
        self.client = ZenRowsClient(apikey)

    @mock.patch.object(Session, "request")
    def test_fetch_returns_error_response_unchanged_no_raise(self, mock_request):
        mock_request.return_value = _fake_response(403, b"blocked")

        response = self.client.fetch(url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content, b"blocked")

    @mock.patch.object(Session, "request")
    def test_extract_returns_error_response_unchanged_no_raise(self, mock_request):
        mock_request.return_value = _fake_response(500, b"upstream failure")

        response = self.client.extract(url)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content, b"upstream failure")

    @mock.patch.object(Session, "request")
    def test_get_and_fetch_produce_byte_identical_request_calls(self, mock_request):
        """get() is documented as a deprecated alias for fetch() — assert they
        are actually identical calls, not just "both work"."""
        mock_request.return_value = _fake_response(200)

        self.client.get(url, params={"js_render": True}, headers={"X-Test": "1"})
        get_call = mock_request.call_args
        mock_request.reset_mock()

        self.client.fetch(url, params={"js_render": True}, headers={"X-Test": "1"})
        fetch_call = mock_request.call_args

        self.assertEqual(get_call, fetch_call)

    @mock.patch.object(Session, "request")
    def test_extract_accepts_standard_mode(self, mock_request):
        mock_request.return_value = _fake_response(200)

        self.client.extract(url, mode="standard")

        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["params"]["extract"], "standard")

    @mock.patch.object(Session, "request")
    def test_extract_does_not_validate_mode_value(self, mock_request):
        """There is no validation on `mode` in extract() — any string is passed
        straight through as the `extract` query param. This test documents that
        as current, intentional-until-decided behavior (server-side validates
        instead); if that ever changes, this test should be the one that fails.
        """
        mock_request.return_value = _fake_response(200)

        self.client.extract(url, mode="not-a-real-mode")

        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["params"]["extract"], "not-a-real-mode")

    @mock.patch.object(Session, "request")
    def test_extract_merges_extract_param_with_other_params(self, mock_request):
        mock_request.return_value = _fake_response(200)

        self.client.extract(url, params={"js_render": True}, mode="native")

        _, kwargs = mock_request.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "url": url,
                "apikey": apikey,
                "js_render": True,
                "extract": "native",
            },
        )

    @mock.patch.object(Session, "request")
    def test_extract_does_not_mutate_caller_supplied_params_dict(self, mock_request):
        """extract() copies `params` before adding `extract` — the caller's dict
        must come back untouched, otherwise a caller reusing a params dict
        across calls would leak `extract` into an unrelated fetch()."""
        mock_request.return_value = _fake_response(200)
        caller_params = {"js_render": True}

        self.client.extract(url, params=caller_params, mode="native")

        self.assertEqual(caller_params, {"js_render": True})


_DOMAIN_UNAVAILABLE = (
    (403, b'{"code": "REQS007", "reason": "domain_not_prepared"}'),
    (402, b'{"code": "AUTH010", "title": "Domain not enabled"}'),
)


class TestExtractNeverFallsBackToAutoparse(TestCase):
    """`extract()` returns 403 REQS007 and the legacy 402 AUTH010 as is, in
    one request. The deprecated `fallback_to_autoparse` has no effect beyond
    a DeprecationWarning."""

    def setUp(self):
        self.client = ZenRowsClient(apikey)

    @mock.patch.object(Session, "request")
    def test_returned_as_is_in_one_request(self, mock_request):
        for status, body in _DOMAIN_UNAVAILABLE:
            with self.subTest(status=status):
                mock_request.reset_mock()
                mock_request.side_effect = [_fake_response(status, body), _fake_response(200)]

                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    response = self.client.extract(url)

                self.assertEqual(response.status_code, status)
                self.assertEqual(mock_request.call_count, 1)
                _, kwargs = mock_request.call_args
                self.assertNotIn("autoparse", kwargs["params"])

    @mock.patch.object(Session, "request")
    def test_deprecated_option_has_no_effect_and_warns(self, mock_request):
        for status, body in _DOMAIN_UNAVAILABLE:
            with self.subTest(status=status):
                mock_request.reset_mock()
                mock_request.side_effect = [_fake_response(status, body), _fake_response(200)]

                with self.assertWarns(DeprecationWarning) as caught:
                    response = self.client.extract(url, fallback_to_autoparse=True)

                self.assertEqual(response.status_code, status)
                self.assertEqual(mock_request.call_count, 1)
                _, kwargs = mock_request.call_args
                self.assertNotIn("autoparse", kwargs["params"])
                self.assertIn("prepare_domain()", str(caught.warning))
                self.assertEqual(caught.filename, __file__)

    @mock.patch.object(Session, "request")
    def test_reason_is_readable_from_the_response(self, mock_request):
        mock_request.return_value = _fake_response(
            403, b'{"code": "REQS007", "reason": "page_not_recognised"}'
        )

        response = self.client.extract(url)

        self.assertEqual(response.json().get("reason"), "page_not_recognised")


class TestExtractAdaptiveStealth(TestCase):
    """extract() sends Adaptive Stealth Mode (mode="auto" at the wire level) by
    default, so targets needing js_render/premium_proxy (e.g. Zoopla) escalate
    automatically instead of failing with REQS002."""

    def setUp(self):
        self.client = ZenRowsClient(apikey)

    @mock.patch.object(Session, "request")
    def test_sends_adaptive_stealth_by_default(self, mock_request):
        mock_request.return_value = _fake_response(200)

        self.client.extract(url)

        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["params"]["mode"], "auto")

    @mock.patch.object(Session, "request")
    def test_omits_wire_mode_when_disabled(self, mock_request):
        mock_request.return_value = _fake_response(200)

        self.client.extract(url, adaptive_stealth=False)

        _, kwargs = mock_request.call_args
        self.assertNotIn("mode", kwargs["params"])

    @mock.patch.object(Session, "request")
    def test_skips_adaptive_stealth_when_js_render_is_set(self, mock_request):
        """The API rejects mode=auto combined with js_render, so a caller's
        fixed configuration is sent as is."""
        mock_request.return_value = _fake_response(200)

        self.client.extract(url, params={"js_render": True})

        _, kwargs = mock_request.call_args
        self.assertNotIn("mode", kwargs["params"])
        self.assertTrue(kwargs["params"]["js_render"])

    @mock.patch.object(Session, "request")
    def test_skips_adaptive_stealth_when_premium_proxy_is_set(self, mock_request):
        mock_request.return_value = _fake_response(200)

        self.client.extract(url, params={"premium_proxy": "true"})

        _, kwargs = mock_request.call_args
        self.assertNotIn("mode", kwargs["params"])

    @mock.patch.object(Session, "request")
    def test_false_js_render_keeps_adaptive_stealth(self, mock_request):
        mock_request.return_value = _fake_response(200)

        self.client.extract(url, params={"js_render": "false", "premium_proxy": False})

        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["params"]["mode"], "auto")

    @mock.patch.object(Session, "request")
    def test_keeps_a_wire_mode_the_caller_set(self, mock_request):
        mock_request.return_value = _fake_response(200)

        self.client.extract(url, params={"mode": "custom"})

        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["params"]["mode"], "custom")


class TestExtractAsyncNeverFallsBackToAutoparse(IsolatedAsyncioTestCase):
    """Async counterpart: one request, error returned as is, and a
    DeprecationWarning for `fallback_to_autoparse=True`."""

    def setUp(self):
        self.client = ZenRowsClient(apikey, concurrency=2)

    @mock.patch.object(Session, "request")
    async def test_returned_as_is_in_one_request(self, mock_request):
        mock_request.side_effect = [
            _fake_response(403, b'{"code": "REQS007"}'),
            _fake_response(402, b'{"code": "AUTH010"}'),
        ]

        first = await self.client.extract_async(url)
        second = await self.client.extract_async(url)

        self.assertEqual(first.status_code, 403)
        self.assertEqual(second.status_code, 402)
        self.assertEqual(mock_request.call_count, 2)

    @mock.patch.object(Session, "request")
    async def test_deprecated_option_has_no_effect_and_warns(self, mock_request):
        mock_request.side_effect = [
            _fake_response(403, b'{"code": "REQS007"}'),
            _fake_response(200),
        ]

        with self.assertWarns(DeprecationWarning):
            response = await self.client.extract_async(url, fallback_to_autoparse=True)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(mock_request.call_count, 1)


class TestFetchExtractAsync(IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = ZenRowsClient(apikey, concurrency=2)

    @mock.patch.object(Session, "request")
    async def test_fetch_async_returns_error_response_unchanged(self, mock_request):
        mock_request.return_value = _fake_response(429, b"rate limited")

        response = await self.client.fetch_async(url)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.content, b"rate limited")

    @mock.patch.object(Session, "request")
    async def test_extract_async_sets_mode(self, mock_request):
        mock_request.return_value = _fake_response(200)

        await self.client.extract_async(url, mode="native")

        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["params"]["extract"], "native")

    @mock.patch.object(Session, "request")
    async def test_get_async_and_fetch_async_produce_identical_calls(self, mock_request):
        mock_request.return_value = _fake_response(200)

        await self.client.get_async(url, params={"premium_proxy": True})
        get_call = mock_request.call_args
        mock_request.reset_mock()

        await self.client.fetch_async(url, params={"premium_proxy": True})
        fetch_call = mock_request.call_args

        self.assertEqual(get_call, fetch_call)


class TestClientConstructionAndLifecycle(TestCase):
    """Covers the constructor validation and close()/context-manager surface
    that every fetch()/extract() call depends on but that nothing exercised."""

    def test_empty_apikey_raises(self):
        with self.assertRaises(ValueError):
            ZenRowsClient("")

    def test_none_apikey_raises(self):
        with self.assertRaises(ValueError):
            ZenRowsClient(None)  # type: ignore[arg-type]

    def test_base_url_override_via_constructor(self):
        client = ZenRowsClient(apikey, base_url="https://custom.example/v1/")
        self.assertEqual(client.api_url, "https://custom.example/v1/")

    @mock.patch.dict("os.environ", {"ZENROWS_SCRAPER_BASE_URL": "https://env.example/v1/"})
    def test_base_url_override_via_env_var(self):
        client = ZenRowsClient(apikey)
        self.assertEqual(client.api_url, "https://env.example/v1/")

    def test_constructor_arg_wins_over_env_var(self):
        with mock.patch.dict("os.environ", {"ZENROWS_SCRAPER_BASE_URL": "https://env.example/v1/"}):
            client = ZenRowsClient(apikey, base_url="https://explicit.example/v1/")
        self.assertEqual(client.api_url, "https://explicit.example/v1/")

    def test_close_shuts_down_session_and_executor(self):
        client = ZenRowsClient(apikey)

        with (
            mock.patch.object(client.requests_session, "close") as mock_close,
            mock.patch.object(client.executor, "shutdown") as mock_shutdown,
        ):
            client.close()

        mock_close.assert_called_once()
        mock_shutdown.assert_called_once_with(wait=True)

    def test_context_manager_calls_close_on_exit(self):
        with mock.patch.object(ZenRowsClient, "close") as mock_close:
            with ZenRowsClient(apikey) as client:
                self.assertIsInstance(client, ZenRowsClient)
            mock_close.assert_called_once()

    def test_context_manager_calls_close_even_on_exception(self):
        with mock.patch.object(ZenRowsClient, "close") as mock_close:
            with self.assertRaises(RuntimeError), ZenRowsClient(apikey):
                raise RuntimeError("boom")
            mock_close.assert_called_once()
