"""prepare_domain() / prepared_domain_status() against a stubbed HTTP server:
the request each one sends, the parsed status it returns, and the
PreparedDomainError it raises on every documented error answer."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import IsolatedAsyncioTestCase, TestCase

from zenrows import PreparedDomainError, ZenRowsClient

apikey = "APIKEY"

PREPARING = {
    "domain": "www.example.com",
    "status": "preparing",
    "state": "received",
    "submitted_at": "2026-10-15T10:00:00Z",
    "updated_at": "2026-10-15T10:00:00Z",
}


def _problem(status: int, code: str, **extra: str) -> dict:
    return {
        "code": code,
        "detail": f"detail for {code}",
        "status": status,
        "title": f"Title ({code})",
        "type": f"https://docs.zenrows.com/api-error-codes#{code}",
        **extra,
    }


class _StubAPI:
    """A local HTTP server that records each request and answers with the
    next canned (status, body, headers) reply."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.replies: list[tuple[int, object, dict]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                stub.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": json.loads(raw) if raw else None,
                    }
                )
                status, body, headers = stub.replies.pop(0)
                payload = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                content_type = "application/json" if status < 400 else "application/problem+json"
                self.send_header("Content-Type", content_type)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _answer
            do_POST = _answer

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def reply(self, status: int, body: object, headers: dict | None = None) -> None:
        self.replies.append((status, body, headers or {}))

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TestPreparedDomains(TestCase):
    def setUp(self):
        self.api = _StubAPI()
        self.addCleanup(self.api.close)
        self.client = ZenRowsClient(apikey, base_url=self.api.base_url)
        self.addCleanup(self.client.close)

    def test_prepare_domain_posts_domain_and_example_urls(self):
        self.api.reply(202, PREPARING)

        status = self.client.prepare_domain(
            "www.example.com", ["https://www.example.com/a", "https://www.example.com/b"]
        )

        self.assertEqual(status, PREPARING)
        self.assertEqual(len(self.api.requests), 1)
        sent = self.api.requests[0]
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(sent["path"], "/v1/extract/prepared-domains")
        self.assertEqual(
            sent["body"],
            {
                "domain": "www.example.com",
                "example_urls": ["https://www.example.com/a", "https://www.example.com/b"],
            },
        )
        self.assertEqual(sent["headers"]["X-API-Key"], apikey)
        self.assertNotIn(apikey, sent["path"])

    def test_prepared_domain_status_gets_the_domain_and_returns_the_status(self):
        ready = {
            **PREPARING,
            "status": "ready",
            "state": "ready",
            "roles": [{"role": "listing", "status": "ready", "url": "https://www.example.com/a"}],
        }
        self.api.reply(200, ready)

        status = self.client.prepared_domain_status("www.example.com")

        self.assertEqual(status, ready)
        sent = self.api.requests[0]
        self.assertEqual(sent["method"], "GET")
        self.assertEqual(sent["path"], "/v1/extract/prepared-domains/www.example.com")
        self.assertEqual(sent["headers"]["X-API-Key"], apikey)

    def test_status_returns_a_failed_preparation_without_raising(self):
        failed = {
            **PREPARING,
            "status": "failed",
            "state": "failed",
            "failure": {"code": "unsupported", "detail": "x", "slot_consumed": True},
        }
        self.api.reply(200, failed)

        status = self.client.prepared_domain_status("www.example.com")

        self.assertEqual(status["failure"]["code"], "unsupported")

    def test_status_escapes_the_domain_in_the_path(self):
        self.api.reply(200, PREPARING)

        self.client.prepared_domain_status("bad/domain?x")

        self.assertEqual(
            self.api.requests[0]["path"], "/v1/extract/prepared-domains/bad%2Fdomain%3Fx"
        )

    def test_prepare_domain_does_not_poll(self):
        self.api.reply(202, PREPARING)

        self.client.prepare_domain("www.example.com", ["https://www.example.com/"])

        self.assertEqual(len(self.api.requests), 1)

    def test_error_answers_raise_prepared_domain_error(self):
        cases = [
            (400, "invalid_argument", {"reason": "invalid_domain"}),
            (409, "in_progress", {}),
            (429, "out_of_allowance", {}),
            (503, "overloaded", {}),
            (503, "disabled", {}),
        ]
        for status, code, extra in cases:
            with self.subTest(code=code):
                self.api.reply(status, _problem(status, code, **extra), {"Retry-After": "7"})

                with self.assertRaises(PreparedDomainError) as caught:
                    self.client.prepare_domain("www.example.com", ["https://www.example.com/"])

                err = caught.exception
                self.assertEqual(err.status_code, status)
                self.assertEqual(err.code, code)
                self.assertEqual(err.detail, f"detail for {code}")
                self.assertEqual(err.reason, extra.get("reason"))
                self.assertEqual(err.retry_after, 7.0)
                self.assertEqual(err.problem["status"], status)
                self.assertIn(code, str(err))

    def test_status_of_an_unknown_domain_raises_not_found(self):
        self.api.reply(404, _problem(404, "not_found"))

        with self.assertRaises(PreparedDomainError) as caught:
            self.client.prepared_domain_status("www.example.com")

        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.code, "not_found")

    def test_error_without_a_json_body(self):
        self.api.reply(502, b"bad gateway")

        with self.assertRaises(PreparedDomainError) as caught:
            self.client.prepared_domain_status("www.example.com")

        self.assertEqual(caught.exception.status_code, 502)
        self.assertIsNone(caught.exception.code)
        self.assertIsNone(caught.exception.problem)

    def test_empty_domain_is_rejected_before_any_request(self):
        for domain in ("", "   ", "\t\n"):
            with self.subTest(domain=domain):
                with self.assertRaises(ValueError):
                    self.client.prepared_domain_status(domain)
                with self.assertRaises(ValueError):
                    self.client.prepare_domain(domain, ["https://www.example.com/"])

        self.assertEqual(self.api.requests, [])


class TestPreparedDomainsIgnoreRetries(TestCase):
    """The client's `retries` apply to scraping only: a 429 or 503 from the
    prepared-domains endpoints is returned on the first answer."""

    def setUp(self):
        self.api = _StubAPI()
        self.addCleanup(self.api.close)
        self.client = ZenRowsClient(apikey, retries=1, base_url=self.api.base_url)
        self.addCleanup(self.client.close)

    def test_status_503_is_not_retried(self):
        self.api.reply(503, _problem(503, "overloaded"))
        self.api.reply(200, PREPARING)

        with self.assertRaises(PreparedDomainError) as caught:
            self.client.prepared_domain_status("www.example.com")

        self.assertEqual(caught.exception.code, "overloaded")
        self.assertEqual(len(self.api.requests), 1)

    def test_prepare_429_is_not_retried(self):
        self.api.reply(429, _problem(429, "out_of_allowance"))
        self.api.reply(202, PREPARING)

        with self.assertRaises(PreparedDomainError) as caught:
            self.client.prepare_domain("www.example.com", ["https://www.example.com/"])

        self.assertEqual(caught.exception.code, "out_of_allowance")
        self.assertEqual(len(self.api.requests), 1)

    def test_scraping_keeps_its_retries(self):
        """Control: the same client still retries a 503 on a scraping request."""
        self.api.reply(503, b"busy")
        self.api.reply(200, b"ok")

        response = self.client.fetch("https://www.example.com/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.api.requests), 2)


class TestPreparedDomainsAsync(IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = _StubAPI()
        self.addCleanup(self.api.close)
        self.client = ZenRowsClient(apikey, concurrency=2, base_url=self.api.base_url)
        self.addCleanup(self.client.close)

    async def test_prepare_domain_async_returns_the_status(self):
        self.api.reply(202, PREPARING)

        status = await self.client.prepare_domain_async(
            "www.example.com", ["https://www.example.com/a"]
        )

        self.assertEqual(status, PREPARING)
        sent = self.api.requests[0]
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(
            sent["body"],
            {"domain": "www.example.com", "example_urls": ["https://www.example.com/a"]},
        )

    async def test_prepared_domain_status_async_returns_the_status(self):
        self.api.reply(200, {**PREPARING, "status": "ready"})

        status = await self.client.prepared_domain_status_async("www.example.com")

        self.assertEqual(status["status"], "ready")
        self.assertEqual(
            self.api.requests[0]["path"], "/v1/extract/prepared-domains/www.example.com"
        )

    async def test_async_errors_raise_prepared_domain_error(self):
        self.api.reply(404, _problem(404, "not_found"))

        with self.assertRaises(PreparedDomainError) as caught:
            await self.client.prepared_domain_status_async("www.example.com")

        self.assertEqual(caught.exception.code, "not_found")

    async def test_async_rejects_an_empty_domain(self):
        with self.assertRaises(ValueError):
            await self.client.prepared_domain_status_async(" ")
        with self.assertRaises(ValueError):
            await self.client.prepare_domain_async("", ["https://www.example.com/"])

        self.assertEqual(self.api.requests, [])
