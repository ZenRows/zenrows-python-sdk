"""Real behavior tests for fetch()/extract() and the client-level gaps they share.

Existing tests in test_client.py only assert on *call arguments* passed to
`Session.request` — none of them exercise what the client actually does with
a response, an error status, an unvalidated mode string, or the constructor/
context-manager surface. These tests close that gap: they assert on what
`fetch()`/`extract()`/`get()` actually *return* and *raise* (or don't).
"""

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
                "mode": "auto",
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


class TestExtractAutoparseFallback(TestCase):
    """`extract(mode="auto")` is a domain-gated open beta: AUTH010 means the
    target domain isn't enabled yet. By default this retries once with
    Autoparse instead of raising - same behavior as the CLI's extract
    adapter."""

    def setUp(self):
        self.client = ZenRowsClient(apikey)

    @mock.patch.object(Session, "request")
    def test_falls_back_to_autoparse_on_auth010(self, mock_request):
        mock_request.side_effect = [
            _fake_response(402, b'{"code": "AUTH010", "title": "Domain not enabled"}'),
            _fake_response(200, b'[{"found": "via autoparse"}]'),
        ]

        response = self.client.extract(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_request.call_count, 2)
        _, fallback_kwargs = mock_request.call_args
        self.assertTrue(fallback_kwargs["params"].get("autoparse"))
        self.assertNotIn("extract", fallback_kwargs["params"])

    @mock.patch.object(Session, "request")
    def test_fallback_disabled_returns_error_response(self, mock_request):
        mock_request.return_value = _fake_response(402, b'{"code": "AUTH010"}')

        response = self.client.extract(url, fallback_to_autoparse=False)

        self.assertEqual(response.status_code, 402)
        self.assertEqual(mock_request.call_count, 1)

    @mock.patch.object(Session, "request")
    def test_402_without_auth010_does_not_fall_back(self, mock_request):
        """A real credits-exhausted 402 (e.g. AUTH004) must not be mistaken
        for the domain-gating error."""
        mock_request.return_value = _fake_response(
            402, b'{"code": "AUTH004", "title": "No credit available"}'
        )

        response = self.client.extract(url)

        self.assertEqual(response.status_code, 402)
        self.assertEqual(mock_request.call_count, 1)

    @mock.patch.object(Session, "request")
    def test_no_fallback_for_non_auto_mode(self, mock_request):
        """AUTH010 shouldn't trigger a fallback for native/standard modes -
        only "auto" is the domain-gated beta path."""
        mock_request.return_value = _fake_response(402, b'{"code": "AUTH010"}')

        response = self.client.extract(url, mode="native")

        self.assertEqual(response.status_code, 402)
        self.assertEqual(mock_request.call_count, 1)

    @mock.patch.object(Session, "request")
    def test_fallback_does_not_mutate_caller_supplied_params_dict(self, mock_request):
        mock_request.side_effect = [
            _fake_response(402, b'{"code": "AUTH010"}'),
            _fake_response(200, b"{}"),
        ]
        caller_params = {"js_render": True}

        self.client.extract(url, params=caller_params)

        self.assertEqual(caller_params, {"js_render": True})


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
    def test_fallback_request_also_carries_adaptive_stealth(self, mock_request):
        mock_request.side_effect = [
            _fake_response(402, b'{"code": "AUTH010"}'),
            _fake_response(200, b"{}"),
        ]

        self.client.extract(url)

        _, fallback_kwargs = mock_request.call_args
        self.assertEqual(fallback_kwargs["params"]["mode"], "auto")


class TestExtractAsyncAutoparseFallback(IsolatedAsyncioTestCase):
    """Async counterpart - same AUTH010 -> Autoparse behavior."""

    def setUp(self):
        self.client = ZenRowsClient(apikey, concurrency=2)

    @mock.patch.object(Session, "request")
    async def test_falls_back_to_autoparse_on_auth010(self, mock_request):
        mock_request.side_effect = [
            _fake_response(402, b'{"code": "AUTH010"}'),
            _fake_response(200, b'[{"found": "via autoparse"}]'),
        ]

        response = await self.client.extract_async(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_request.call_count, 2)


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
