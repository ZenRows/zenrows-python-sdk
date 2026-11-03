"""The ZenRowsClient retry policy, against a local HTTP server.

Retries are off by default. Sleeps are patched out, and the retry budget runs
on a fake clock. `tests/test_prepared_domains.py` checks that the
prepared-domains calls are never retried.
"""

import asyncio
import io
import math
import socket
import sys
import threading
import warnings
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import TestCase
from unittest.mock import patch

import pytest
import requests
from requests import Response
from requests.adapters import HTTPAdapter

from zenrows import ZenRowsClient, is_no_data_extracted
from zenrows.client import _jitter, _retry_after_seconds

APIKEY = "APIKEY"
TARGET = "http://example.com"
KEY = {"Idempotency-Key": "a-unique-key"}

NO_DATA = b'{"code": "no_data_extracted", "title": "No data extracted"}'
NO_CONTENT = b'{"code": "RESP001", "title": "Could not get content"}'

# Scripted answers: (status, headers, body). HANG answers after the client's
# read timeout; DROP closes the connection without an answer.
HANG = (0, {}, b"")
DROP = (-1, {}, b"")


class _Server:
    """Answers each request with the next scripted answer (the last one
    repeats) and records the body of every request."""

    def __init__(self) -> None:
        self.script: list[tuple[int, dict[str, str], bytes]] = [(200, {}, b"ok")]
        self.bodies: list[bytes] = []
        self.on_request = lambda: None
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                server.bodies.append(self._body())
                server.on_request()
                status, headers, body = (
                    server.script.pop(0) if len(server.script) > 1 else server.script[0]
                )
                if status == -1:
                    self.close_connection = True
                    return
                if status == 0:
                    threading.Event().wait(0.5)  # not time.sleep: tests patch it
                    status = 200
                try:
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass  # the client gave up first

            def _body(self) -> bytes:
                if self.headers.get("Transfer-Encoding", "").lower() != "chunked":
                    return self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = b""
                while size := int(self.rfile.readline().strip() or b"0", 16):
                    body += self.rfile.read(size)
                    self.rfile.readline()
                self.rfile.readline()
                return body

            do_GET = do_POST = do_PUT = _answer

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()

    @property
    def calls(self) -> int:
        return len(self.bodies)


@pytest.fixture
def server() -> Iterator[_Server]:
    srv = _Server()
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


@pytest.fixture
def sleeps() -> Iterator[list[float]]:
    """Every wait between tries, in seconds; none of them actually waits."""
    waited: list[float] = []
    with patch("time.sleep", side_effect=waited.append):
        yield waited


def client(server: _Server, **kwargs) -> ZenRowsClient:
    return ZenRowsClient(APIKEY, base_url=server.url, **kwargs)


def closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/"  # nothing listens there


# ---- off by default ----


@pytest.mark.parametrize("options", [{}, {"retries": 0}])
@pytest.mark.parametrize("status", [503, 422])
def test_default_sends_once(server: _Server, sleeps: list[float], options: dict, status: int):
    server.script = [(status, {}, NO_CONTENT), (200, {}, b"ok")]
    zr = client(server, **options)

    response = zr.fetch(TARGET)

    assert response.status_code == status
    assert server.calls == 1
    assert sleeps == []
    assert type(zr.requests_session.get_adapter(server.url)) is HTTPAdapter
    assert zr.requests_session.get_adapter(server.url).max_retries.total == 0


# ---- the options ----


@pytest.mark.parametrize("set_later", [False, True])
@pytest.mark.parametrize(
    ("value", "count", "warns"),
    [
        (3, 3, False),
        (0, 0, False),
        (-1, 0, False),
        (False, 0, True),
        (0.0, 0, True),
        (True, 1, True),
        (2.5, 2, True),
        (math.nan, 0, True),
        (math.inf, sys.maxsize, True),
    ],
)
def test_legacy_retries_values_still_work(value: object, count: int, warns: bool, set_later: bool):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if set_later:
            zr = ZenRowsClient(APIKEY)
            zr.retries = value
        else:
            zr = ZenRowsClient(APIKEY, retries=value)

    assert zr.retries == count
    deprecations = [w for w in caught if w.category is DeprecationWarning]
    assert len(deprecations) == (1 if warns else 0)
    assert all(w.filename == __file__ for w in deprecations)  # points at the caller


@pytest.mark.parametrize("value", [None, "2"])
def test_retries_that_is_not_a_number_raises_type_error(value: object):
    with pytest.raises(TypeError, match="retries"):
        ZenRowsClient(APIKEY, retries=value)


def test_setting_retries_later_turns_retries_on_and_off(server: _Server, sleeps: list[float]):
    zr = client(server)

    zr.retries = 2
    server.script = [(503, {}, b""), (422, {}, NO_CONTENT), (200, {}, b"ok")]
    assert zr.fetch(TARGET).status_code == 200
    assert server.calls == 3

    zr.retries = 0
    server.script = [(503, {}, b""), (200, {}, b"ok")]
    assert zr.fetch(TARGET).status_code == 503
    assert server.calls == 4


@pytest.mark.parametrize("option", ["retry_max_wait", "retry_budget"])
@pytest.mark.parametrize("value", [0, -1.0, math.inf, math.nan, "5", None, True])
def test_invalid_wait_option_is_rejected(option: str, value: object):
    with pytest.raises(ValueError, match=option):
        ZenRowsClient(APIKEY, **{option: value})


# ---- what is retried ----


@pytest.mark.parametrize(
    ("status", "body"),
    [(429, b""), (500, b""), (502, b""), (503, b""), (504, b""), (422, NO_CONTENT), (422, b"x")],
)
def test_retryable_status_is_retried(
    server: _Server, sleeps: list[float], status: int, body: bytes
):
    server.script = [(status, {}, body), (200, {}, b"ok")]

    response = client(server, retries=2).fetch(TARGET)

    assert response.status_code == 200
    assert server.calls == 2
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    ("status", "headers", "body"),
    [
        (400, {}, b""),
        (401, {}, b""),
        (403, {"Retry-After": "1"}, b""),
        (404, {}, b""),
        (413, {"Retry-After": "1"}, b""),
        (422, {}, NO_DATA),
    ],
)
def test_other_status_is_not_retried(
    server: _Server, sleeps: list[float], status: int, headers: dict, body: bytes
):
    server.script = [(status, headers, body), (200, {}, b"ok")]

    response = client(server, retries=3).fetch(TARGET)

    assert response.status_code == status
    assert server.calls == 1
    assert sleeps == []


@pytest.mark.parametrize("use_async", [False, True])
def test_extract_does_not_retry_no_data_extracted(
    server: _Server, sleeps: list[float], use_async: bool
):
    server.script = [(422, {}, NO_DATA), (200, {}, b"{}")]
    zr = client(server, retries=3)

    response = asyncio.run(zr.extract_async(TARGET)) if use_async else zr.extract(TARGET)

    assert is_no_data_extracted(response)
    assert server.calls == 1


@pytest.mark.parametrize("status", [503, 422])
def test_last_response_is_returned_when_retries_run_out(
    server: _Server, sleeps: list[float], status: int
):
    server.script = [(status, {}, b"first"), (status, {}, b"second"), (status, {}, b"last")]

    response = client(server, retries=2).fetch(TARGET)

    assert response.content == b"last"
    assert server.calls == 3
    assert len(sleeps) == 2


def test_422_and_5xx_share_one_retry_count(server: _Server, sleeps: list[float]):
    server.script = [(503, {}, b""), (422, {}, NO_CONTENT), (503, {}, b""), (200, {}, b"ok")]

    with patch("zenrows.client.random.random", return_value=1.0):
        response = client(server, retries=2).fetch(TARGET)

    assert response.status_code == 503
    assert server.calls == 3
    assert sleeps == [1.0, 2.0]  # the attempt index runs on across both kinds


def test_read_timeout_is_not_retried(server: _Server, sleeps: list[float]):
    server.script = [HANG]

    with pytest.raises(requests.ReadTimeout):
        client(server, retries=3).fetch(TARGET, timeout=0.1)

    assert server.calls == 1


def test_connection_dropped_after_sending_is_not_retried(server: _Server, sleeps: list[float]):
    server.script = [DROP, (200, {}, b"ok")]

    with pytest.raises(requests.ConnectionError):
        client(server, retries=3).fetch(TARGET)

    assert server.calls == 1


@pytest.mark.parametrize(
    ("verb", "params", "retried"),
    [
        ("fetch", None, 2),
        ("fetch", {"original_status": "true", "allowed_status_codes": "503"}, 2),
        ("post", None, 0),
        ("put", None, 0),
    ],
)
def test_failure_to_connect_is_retried_for_a_get_only(
    sleeps: list[float], verb: str, params: dict | None, retried: int
):
    zr = ZenRowsClient(APIKEY, base_url=closed_port_url(), retries=2)

    with pytest.raises(requests.ConnectionError):
        getattr(zr, verb)(TARGET, params=params)

    assert len(sleeps) == retried


# ---- which requests are sent again ----


@pytest.mark.parametrize("status", [429, 500, 503, 422])
@pytest.mark.parametrize("headers", [None, KEY])
@pytest.mark.parametrize("verb", ["post", "put"])
def test_post_and_put_are_sent_once(
    server: _Server, sleeps: list[float], verb: str, headers: dict | None, status: int
):
    server.script = [(status, {}, NO_CONTENT), (200, {}, b"ok")]

    response = getattr(client(server, retries=3), verb)(TARGET, headers=headers, data="payload")

    # An `Idempotency-Key` changes nothing: the API does not deduplicate on it.
    assert response.status_code == status
    assert server.bodies == [b"payload"]
    assert sleeps == []


@pytest.mark.parametrize("status", [503, 422])
@pytest.mark.parametrize(
    "body",
    [
        lambda: {"data": io.BytesIO(b"chunk1-chunk2")},
        lambda: {"data": (c for c in [b"chunk1-", b"chunk2"])},
        lambda: {"data": iter([b"chunk1-", b"chunk2"])},
        lambda: {"files": {"f": ("f.txt", io.BytesIO(b"chunk1-chunk2"))}},
    ],
    ids=["file", "generator", "iterator", "files"],
)
def test_get_with_a_one_shot_body_is_sent_once(
    server: _Server, sleeps: list[float], body: Callable[[], dict], status: int
):
    server.script = [(status, {}, NO_CONTENT), (200, {}, b"ok")]

    response = client(server, retries=2).fetch(TARGET, **body())

    assert response.status_code == status
    assert len(server.bodies) == 1
    assert b"chunk1-chunk2" in server.bodies[0]
    assert sleeps == []


@pytest.mark.parametrize("status", [503, 422])
@pytest.mark.parametrize(
    ("data", "sent"), [("payload", b"payload"), (b"payload", b"payload"), ({"a": "1"}, b"a=1")]
)
def test_get_with_a_string_bytes_or_dict_body_is_retried(
    server: _Server, sleeps: list[float], data: object, sent: bytes, status: int
):
    server.script = [(status, {}, NO_CONTENT), (200, {}, b"ok")]

    response = client(server, retries=2).fetch(TARGET, data=data)

    assert response.status_code == 200
    assert server.bodies == [sent, sent]


# ---- statuses from the target site ----


@pytest.mark.parametrize(
    ("params", "status", "retried"),
    [
        ({"original_status": True}, 503, False),
        ({"original_status": "true"}, 422, False),
        ({"original_status": "TRUE"}, 429, False),
        ({"original_status": 1}, 500, False),
        ({"original_status": "1"}, 504, False),
        ({"original_status": False}, 503, True),
        ({"original_status": "false"}, 422, True),
        ({"original_status": 0}, 503, True),
        ({"original_status": ""}, 503, True),
        ({"allowed_status_codes": "503"}, 503, False),
        ({"allowed_status_codes": " 404 , 503 "}, 503, False),
        ({"allowed_status_codes": ["404", "503"]}, 503, False),
        ({"allowed_status_codes": [404, 422]}, 422, False),
        ({"allowed_status_codes": "404,503"}, 502, True),
        ({"allowed_status_codes": "404,503"}, 422, True),
    ],
)
def test_status_from_the_target_site_is_not_retried(
    server: _Server, sleeps: list[float], params: dict, status: int, retried: bool
):
    server.script = [(status, {}, NO_CONTENT), (200, {}, b"ok")]

    response = client(server, retries=2).fetch(TARGET, params=params)

    assert response.status_code == (200 if retried else status)
    assert server.calls == (2 if retried else 1)


# ---- how long it waits ----


@pytest.mark.parametrize(
    ("value", "millis"),
    [
        ("5", 5000),
        ("0", 250),
        (" 7 ", 7000),
        ("99999", 30000),
        pytest.param("9" * 5000, 30000, id="5000-nines-30000"),
        ("100", 30000),
        ("0005", 5000),
        ("000", 250),
        ("1.5", None),
        ("-1", None),
        ("1e3", None),
        ("0x10", None),
        ("", None),
        ("abc", None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
    ],
)
def test_retry_after_conformance(value: str, millis: int | None):
    seconds = _retry_after_seconds(value)

    assert (None if seconds is None else round(seconds * 1000)) == millis


@pytest.mark.parametrize(
    ("attempt", "retry_max_wait", "low", "high"),
    [
        (0, 30.0, 250, 1000),
        (1, 30.0, 250, 2000),
        (2, 30.0, 250, 4000),
        (5, 30.0, 250, 30000),
        (9, 30.0, 250, 30000),
        (4, 5.0, 250, 5000),
        (0, 0.1, 250, 250),
        (6, 0.1, 250, 250),
    ],
)
def test_jitter_conformance(attempt: int, retry_max_wait: float, low: int, high: int):
    with patch("zenrows.client.random.random", return_value=0.0):
        assert round(_jitter(attempt, retry_max_wait) * 1000) == low
    with patch("zenrows.client.random.random", return_value=1.0):
        assert round(_jitter(attempt, retry_max_wait) * 1000) == high


def test_jitter_ceiling_doubles_per_retry(server: _Server, sleeps: list[float]):
    server.script = [(503, {}, b"")] * 4 + [(200, {}, b"ok")]

    with patch("zenrows.client.random.random", return_value=1.0):
        client(server, retries=4).fetch(TARGET)

    assert sleeps == [1.0, 2.0, 4.0, 8.0]


@pytest.mark.parametrize(
    ("status", "value", "waited"),
    [
        (503, "20", 20.0),  # above retry_max_wait=5: Retry-After has its own cap
        (429, "45", 30.0),
        (422, "4", 4.0),
        (503, "1.5", 0.625),  # not whole seconds: the jittered wait instead
        (503, "Wed, 21 Oct 2099 07:28:00 GMT", 0.625),
    ],
)
def test_retry_after_sets_the_wait(
    server: _Server, sleeps: list[float], status: int, value: str, waited: float
):
    server.script = [(status, {"Retry-After": value}, NO_CONTENT), (200, {}, b"ok")]

    with patch("zenrows.client.random.random", return_value=0.5):
        response = client(server, retries=2, retry_max_wait=5).fetch(TARGET)

    assert response.status_code == 200
    assert sleeps == [waited]


# ---- the retry budget ----


@pytest.fixture
def clock() -> Iterator[tuple[list[float], list[float]]]:
    """A fake clock for the budget that every wait moves on: ([now], waits)."""
    now = [0.0]
    waited: list[float] = []

    def wait(seconds: float) -> None:
        waited.append(seconds)
        now[0] += seconds

    with patch("zenrows.client._monotonic", lambda: now[0]), patch("time.sleep", wait):
        yield now, waited


@pytest.mark.parametrize(
    ("retry_budget", "first_try_takes", "waits"),
    [
        (90.0, 0.0, [20.0] * 4),  # a fifth wait would end at 100 s
        (90.0, 75.0, []),  # the budget counts the first try
        (15.0, 0.0, []),
    ],
)
def test_no_retry_starts_past_the_budget(
    server: _Server, clock: tuple, retry_budget: float, first_try_takes: float, waits: list
):
    now, waited = clock
    server.script = [(503, {"Retry-After": "20"}, b"")]

    def slow_first_try() -> None:
        if server.calls == 1:
            now[0] += first_try_takes

    server.on_request = slow_first_try

    response = client(server, retries=10, retry_budget=retry_budget).fetch(TARGET)

    assert response.status_code == 503
    assert waited == waits
    assert server.calls == len(waits) + 1


def test_each_async_call_has_its_own_budget(server: _Server, clock: tuple):
    _, waited = clock
    zr = client(server, retries=1, retry_budget=40.0)

    async def two_calls() -> list[int]:
        statuses = []
        for _ in range(2):
            server.script = [(503, {"Retry-After": "30"}, b""), (200, {}, b"ok")]
            statuses.append((await zr.fetch_async(TARGET)).status_code)
        return statuses

    assert asyncio.run(two_calls()) == [200, 200]
    assert waited == [30.0, 30.0]


# ---- is_no_data_extracted ----


def _fake_response(status_code: int, body: bytes = b"") -> Response:
    response = Response()
    response.status_code = status_code
    response._content = body
    return response


class TestIsNoDataExtracted(TestCase):
    def test_matches_422_no_data_extracted_any_case(self):
        self.assertTrue(is_no_data_extracted(_fake_response(422, NO_DATA)))
        self.assertTrue(is_no_data_extracted(_fake_response(422, b'{"code": "NO_DATA_EXTRACTED"}')))

    def test_rejects_other_codes_statuses_and_bodies(self):
        self.assertFalse(is_no_data_extracted(_fake_response(422, NO_CONTENT)))
        self.assertFalse(is_no_data_extracted(_fake_response(400, NO_DATA)))
        self.assertFalse(is_no_data_extracted(_fake_response(422, b"not json")))
        self.assertFalse(is_no_data_extracted(_fake_response(422, b"[]")))
