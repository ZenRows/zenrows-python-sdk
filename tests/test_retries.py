"""The ZenRowsClient retry policy, end to end against a local HTTP server.

Retried: 429, 500, 502, 503, 504, a 422 other than `no_data_extracted`, and a
failure to connect. Never retried: any other status, a read timeout, or a
connection that drops after the request was sent. Only a GET, HEAD or OPTIONS
whose body is none, a string or bytes is retried; a POST or PUT is sent once,
with or without an `Idempotency-Key` header. Waits honour a whole-seconds
`Retry-After` (clamped to [0.25, 30]) or else use full jitter, and no retry
starts past `retry_budget`. Sleeps are patched out; the budget runs on a fake
clock.
"""

import asyncio
import io
import math
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from requests.adapters import HTTPAdapter

from zenrows import PreparedDomainError, ZenRowsClient, is_no_data_extracted
from zenrows.client import _jitter, _JitteredRetry, _retry_after_seconds

APIKEY = "APIKEY"
TARGET = "http://example.com"
KEY = {"Idempotency-Key": "a-unique-key"}

NO_DATA = b'{"code": "no_data_extracted", "title": "No data extracted"}'
NO_CONTENT = b'{"code": "RESP001", "title": "Could not get content"}'
DOMAIN_NOT_PREPARED = b'{"code": "REQS007", "title": "Domain not prepared"}'

# A scripted response: (status, headers, body). HANG answers only after the
# client's read timeout has passed. DROP reads the request and closes the
# connection without an answer.
HANG = (0, {}, b"")
DROP = (-1, {}, b"")


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    if handler.headers.get("Transfer-Encoding", "").lower() == "chunked":
        body = b""
        while True:
            size = int(handler.rfile.readline().strip() or b"0", 16)
            if size == 0:
                handler.rfile.readline()
                return body
            body += handler.rfile.read(size)
            handler.rfile.readline()
    length = int(handler.headers.get("Content-Length") or 0)
    return handler.rfile.read(length) if length else b""


class _Server:
    """Answers each request with the next scripted response (the last one
    repeats) and records the query and body of every request it got."""

    def __init__(self) -> None:
        self.script: list[tuple[int, dict[str, str], bytes]] = [(200, {}, b"ok")]
        self.queries: list[dict[str, list[str]]] = []
        self.bodies: list[bytes] = []
        self.on_request = lambda: None
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                server.bodies.append(_read_body(self))
                server.queries.append(parse_qs(urlparse(self.path).query))
                server.on_request()
                status, headers, body = (
                    server.script.pop(0) if len(server.script) > 1 else server.script[0]
                )
                if status == -1:
                    self.close_connection = True
                    return  # no status line: the client sees the connection close
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
        return len(self.queries)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[_Server]:
    srv = _Server()
    yield srv
    srv.close()


@pytest.fixture
def sleeps() -> Iterator[list[float]]:
    """Every wait between tries, in seconds; none of them actually waits."""
    waited: list[float] = []
    with patch("time.sleep", side_effect=waited.append):
        yield waited


def client(server: _Server, **kwargs) -> ZenRowsClient:
    return ZenRowsClient(APIKEY, base_url=server.url, **kwargs)


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]  # closed once the block ends: nothing listens


# ---- configuration ----


def test_default_is_two_retries() -> None:
    zr = ZenRowsClient(APIKEY)

    retry = zr.requests_session.get_adapter("https://api.zenrows.com/v1/").max_retries
    assert zr.retries == 2
    assert isinstance(retry, _JitteredRetry)
    assert (retry.total, retry.status, retry.connect, retry.other) == (2, 2, 2, 0)
    assert retry.read is False
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
    assert (retry.policy.retries, retry.policy.retry_max_wait, retry.policy.retry_budget) == (
        2,
        30.0,
        90.0,
    )


def test_retries_zero_mounts_no_retry(server: _Server, sleeps: list[float]) -> None:
    zr = client(server, retries=0)
    server.script = [(503, {}, b"")]

    response = zr.fetch(TARGET)

    assert type(zr.requests_session.get_adapter(server.url)) is HTTPAdapter
    assert response.status_code == 503
    assert server.calls == 1
    assert sleeps == []


def test_retries_zero_does_not_retry_a_422(server: _Server, sleeps: list[float]) -> None:
    server.script = [(422, {}, NO_CONTENT)]

    response = client(server, retries=0).fetch(TARGET)

    assert response.status_code == 422
    assert server.calls == 1
    assert sleeps == []


def test_default_retries_twice_then_returns_the_last_response(
    server: _Server, sleeps: list[float]
) -> None:
    server.script = [(503, {}, b"first"), (503, {}, b"second"), (503, {}, b"last")]

    response = client(server).fetch(TARGET)

    assert response.status_code == 503
    assert response.content == b"last"
    assert server.calls == 3
    assert len(sleeps) == 2


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("retries", -1),
        ("retries", 1.5),
        ("retries", True),
        ("retries", None),
        ("retries", "2"),
        ("retry_max_wait", 0),
        ("retry_max_wait", -1.0),
        ("retry_max_wait", math.inf),
        ("retry_max_wait", math.nan),
        ("retry_max_wait", "5"),
        ("retry_budget", 0),
        ("retry_budget", -1.0),
        ("retry_budget", math.inf),
        ("retry_budget", math.nan),
        ("retry_budget", None),
    ],
)
def test_invalid_retry_option_is_rejected(option: str, value: object) -> None:
    with pytest.raises(ValueError, match=option):
        ZenRowsClient(APIKEY, **{option: value})


def test_smallest_valid_retry_options_are_accepted() -> None:
    zr = ZenRowsClient(APIKEY, retries=0, retry_max_wait=0.1, retry_budget=0.1)

    assert (zr.retries, zr.retry_max_wait, zr.retry_budget) == (0, 0.1, 0.1)


# ---- what is retried ----


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_status_is_retried_until_success(
    server: _Server, sleeps: list[float], status: int
) -> None:
    server.script = [(status, {}, b""), (200, {}, b"ok")]

    response = client(server).fetch(TARGET)

    assert response.status_code == 200
    assert response.text == "ok"
    assert server.calls == 2


@pytest.mark.parametrize(
    ("status", "body"),
    [(400, b""), (401, b""), (402, b""), (403, b""), (404, b""), (422, NO_DATA)],
)
def test_other_statuses_are_not_retried(
    server: _Server, sleeps: list[float], status: int, body: bytes
) -> None:
    server.script = [(status, {}, body)]

    response = client(server, retries=3).fetch(TARGET)

    assert response.status_code == status
    assert server.calls == 1
    assert sleeps == []


@pytest.mark.parametrize("body", [NO_CONTENT, b"", b"not json"])
def test_422_other_than_no_data_extracted_is_retried(
    server: _Server, sleeps: list[float], body: bytes
) -> None:
    server.script = [(422, {}, body), (200, {}, b"ok")]

    response = client(server).fetch(TARGET)

    assert response.status_code == 200
    assert server.calls == 2
    assert len(sleeps) == 1


def test_422_is_returned_as_is_after_the_last_try(server: _Server, sleeps: list[float]) -> None:
    last = b'{"code": "RESP001", "title": "last"}'
    server.script = [(422, {}, NO_CONTENT), (422, {}, NO_CONTENT), (422, {}, last)]

    response = client(server).fetch(TARGET)

    assert response.status_code == 422
    assert response.content == last
    assert server.calls == 3
    assert len(sleeps) == 2


@pytest.mark.parametrize(
    "script",
    [
        [(503, {}, b""), (422, {}, NO_CONTENT), (422, {}, NO_CONTENT), (200, {}, b"ok")],
        [(422, {}, NO_CONTENT), (503, {}, b""), (503, {}, b""), (200, {}, b"ok")],
        [(422, {}, NO_CONTENT), (503, {}, b""), (422, {}, NO_CONTENT), (200, {}, b"ok")],
    ],
)
def test_422_and_5xx_share_one_retry_count(
    server: _Server, sleeps: list[float], script: list[tuple[int, dict[str, str], bytes]]
) -> None:
    server.script = script

    response = client(server).fetch(TARGET)

    # Two retries in all, whichever status asked for them.
    assert response.status_code != 200
    assert server.calls == 3
    assert len(sleeps) == 2


def test_422_and_5xx_share_one_attempt_index(server: _Server, sleeps: list[float]) -> None:
    server.script = [(503, {}, b""), (422, {}, NO_CONTENT), (200, {}, b"ok")]

    with patch("zenrows.client.random.random", return_value=1.0):
        response = client(server).fetch(TARGET)

    assert response.status_code == 200
    assert sleeps == [1.0, 2.0]


def test_413_with_retry_after_is_not_retried(server: _Server, sleeps: list[float]) -> None:
    server.script = [(413, {"Retry-After": "1"}, b"")]

    response = client(server).fetch(TARGET)

    assert response.status_code == 413
    assert server.calls == 1


def test_read_timeout_is_not_retried(server: _Server, sleeps: list[float]) -> None:
    server.script = [HANG]

    with pytest.raises(requests.exceptions.ReadTimeout):
        client(server, retries=3).fetch(TARGET, timeout=0.1)

    assert server.calls == 1
    assert sleeps == []


def test_connection_dropped_after_send_is_not_retried_for_get(
    server: _Server, sleeps: list[float]
) -> None:
    server.script = [DROP, (200, {}, b"ok")]

    with pytest.raises(requests.exceptions.ConnectionError):
        client(server, retries=3).fetch(TARGET)

    assert server.calls == 1
    assert sleeps == []


@pytest.mark.parametrize("headers", [None, KEY])
@pytest.mark.parametrize("verb", ["post", "put"])
def test_connection_dropped_after_send_is_not_retried_for_post_and_put(
    server: _Server, sleeps: list[float], verb: str, headers: dict[str, str] | None
) -> None:
    server.script = [DROP, (200, {}, b"ok")]

    with pytest.raises(requests.exceptions.ConnectionError):
        getattr(client(server, retries=3), verb)(TARGET, headers=headers, data="payload")

    assert server.calls == 1
    assert sleeps == []


def test_connection_refused_is_retried(sleeps: list[float]) -> None:
    zr = ZenRowsClient(APIKEY, base_url=f"http://127.0.0.1:{_closed_port()}/")
    with pytest.raises(requests.exceptions.ConnectionError):
        zr.fetch(TARGET)

    assert len(sleeps) == 2


# ---- which requests may be sent again ----


@pytest.mark.parametrize("status", [503, 429, 422, 500])
@pytest.mark.parametrize("headers", [None, KEY, {"idempotency-key": "k"}])
@pytest.mark.parametrize("verb", ["post", "put"])
def test_post_and_put_are_sent_once(
    server: _Server,
    sleeps: list[float],
    verb: str,
    headers: dict[str, str] | None,
    status: int,
) -> None:
    server.script = [(status, {}, NO_CONTENT), (200, {}, b"ok")]

    response = getattr(client(server, retries=3), verb)(TARGET, headers=headers, data="payload")

    # An `Idempotency-Key` changes nothing: the API does not deduplicate on it.
    assert response.status_code == status
    assert server.bodies == [b"payload"]
    assert sleeps == []


@pytest.mark.parametrize("headers", [None, KEY])
@pytest.mark.parametrize("verb", ["post", "put"])
def test_post_and_put_are_sent_once_after_a_failure_to_connect(
    sleeps: list[float], verb: str, headers: dict[str, str] | None
) -> None:
    zr = ZenRowsClient(APIKEY, retries=3, base_url=f"http://127.0.0.1:{_closed_port()}/")
    with pytest.raises(requests.exceptions.ConnectionError):
        getattr(zr, verb)(TARGET, headers=headers, data="payload")

    # A GET to the same closed port waits before each of its retries.
    assert sleeps == []


@pytest.mark.parametrize("verb", ["post_async", "put_async"])
def test_post_and_put_async_are_sent_once(server: _Server, sleeps: list[float], verb: str) -> None:
    server.script = [(503, {}, b""), (200, {}, b"ok")]

    response = asyncio.run(getattr(client(server), verb)(TARGET, headers=KEY, data="payload"))

    assert response.status_code == 503
    assert server.calls == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [422, 503])
def test_post_with_a_file_body_is_sent_once(
    server: _Server, sleeps: list[float], status: int
) -> None:
    # A 422 retry used to send this body again empty, after the first try had
    # read the file to its end.
    server.script = [(status, {}, NO_CONTENT), (200, {}, b"ok")]

    response = client(server).post(TARGET, headers=KEY, data=io.BytesIO(b"payload=1"))

    assert response.status_code == status
    assert response.content == NO_CONTENT
    assert server.bodies == [b"payload=1"]
    assert sleeps == []


@pytest.mark.parametrize("status", [503, 422])
@pytest.mark.parametrize("kind", ["file", "generator", "iterator"])
def test_get_with_a_one_shot_body_is_not_retried(
    server: _Server, sleeps: list[float], kind: str, status: int
) -> None:
    server.script = [(status, {}, NO_CONTENT), (200, {}, b"ok")]
    chunks = [b"chunk1-", b"chunk2"]
    data = {
        "file": lambda: io.BytesIO(b"".join(chunks)),
        "generator": lambda: (c for c in chunks),
        "iterator": lambda: iter(chunks),
    }[kind]()

    response = client(server).fetch(TARGET, data=data)

    assert response.status_code == status
    assert server.bodies == [b"chunk1-chunk2"]
    assert sleeps == []


@pytest.mark.parametrize("status", [503, 422])
@pytest.mark.parametrize(
    ("data", "sent"),
    [("payload", b"payload"), (b"payload", b"payload"), ({"a": "1"}, b"a=1")],
)
def test_get_with_a_string_or_bytes_body_is_retried(
    server: _Server, sleeps: list[float], data: object, sent: bytes, status: int
) -> None:
    server.script = [(status, {}, NO_CONTENT), (200, {}, b"ok")]

    response = client(server).fetch(TARGET, data=data)

    assert response.status_code == 200
    assert server.bodies == [sent, sent]
    assert len(sleeps) == 1


# ---- how long it waits ----


@pytest.mark.parametrize(
    ("value", "millis"),
    [
        ("5", 5000),
        ("0", 250),
        (" 7 ", 7000),
        ("99999", 30000),
        ("1.5", None),
        ("-1", None),
        ("1e3", None),
        ("0x10", None),
        ("", None),
        ("abc", None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
    ],
)
def test_retry_after_conformance(value: str, millis: int | None) -> None:
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
def test_jitter_conformance(attempt: int, retry_max_wait: float, low: int, high: int) -> None:
    with patch("zenrows.client.random.random", return_value=0.0):
        assert round(_jitter(attempt, retry_max_wait) * 1000) == low
    with patch("zenrows.client.random.random", return_value=1.0):
        assert round(_jitter(attempt, retry_max_wait) * 1000) == high


def test_jitter_ceiling_doubles_per_retry(server: _Server, sleeps: list[float]) -> None:
    server.script = [(503, {}, b"")] * 4 + [(200, {}, b"ok")]

    with patch("zenrows.client.random.random", return_value=1.0):
        response = client(server, retries=4).fetch(TARGET)

    assert response.status_code == 200
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_jitter_stays_in_bounds(server: _Server, sleeps: list[float]) -> None:
    for _ in range(20):
        sleeps.clear()
        server.script = [(503, {}, b"")] * 3 + [(200, {}, b"ok")]
        client(server, retries=3).fetch(TARGET)
        assert len(sleeps) == 3
        for attempt, waited in enumerate(sleeps):
            assert 0.25 <= waited <= 2.0**attempt


def test_tiny_retry_max_wait_still_waits_a_quarter_second(
    server: _Server, sleeps: list[float]
) -> None:
    server.script = [(503, {}, b""), (503, {}, b""), (200, {}, b"ok")]

    client(server, retry_max_wait=0.1).fetch(TARGET)

    assert sleeps == [0.25, 0.25]


@pytest.mark.parametrize(
    ("value", "waited"), [("7", 7.0), ("0", 0.25), ("99999", 30.0), (" 3 ", 3.0)]
)
def test_retry_after_is_honoured(
    server: _Server, sleeps: list[float], value: str, waited: float
) -> None:
    server.script = [(503, {"Retry-After": value}, b""), (200, {}, b"ok")]

    response = client(server).fetch(TARGET)

    assert response.status_code == 200
    assert sleeps == [waited]


def test_retry_after_on_a_422_is_honoured(server: _Server, sleeps: list[float]) -> None:
    server.script = [(422, {"Retry-After": "4"}, NO_CONTENT), (200, {}, b"ok")]

    client(server).fetch(TARGET)

    assert sleeps == [4.0]


@pytest.mark.parametrize("retry_max_wait", [5.0, 60.0])
def test_retry_after_does_not_depend_on_retry_max_wait(
    server: _Server, sleeps: list[float], retry_max_wait: float
) -> None:
    server.script = [
        (429, {"Retry-After": "20"}, b""),
        (429, {"Retry-After": "45"}, b""),
        (200, {}, b"ok"),
    ]

    client(server, retry_max_wait=retry_max_wait).fetch(TARGET)

    assert sleeps == [20.0, 30.0]


@pytest.mark.parametrize("value", ["soon", "1.5", "-5", "Wed, 21 Oct 2099 07:28:00 GMT"])
def test_retry_after_other_than_whole_seconds_falls_back_to_jitter(
    server: _Server, sleeps: list[float], value: str
) -> None:
    server.script = [(503, {"Retry-After": value}, b""), (200, {}, b"ok")]

    with patch("zenrows.client.random.random", return_value=0.5):
        response = client(server).fetch(TARGET)

    assert response.status_code == 200
    assert sleeps == [0.625]


# ---- the retry budget ----


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Iterator[tuple[_Clock, list[float]]]:
    """A fake clock for the retry budget, moved on by every wait, and the
    list of those waits."""
    fake = _Clock()
    waited: list[float] = []

    def wait(seconds: float) -> None:
        waited.append(seconds)
        fake.now += seconds

    with (
        patch("zenrows.client._monotonic", fake),
        patch("time.sleep", side_effect=wait),
    ):
        yield fake, waited


def test_no_retry_starts_past_the_budget(
    server: _Server, clock: tuple[_Clock, list[float]]
) -> None:
    _, sleeps = clock
    server.script = [(503, {"Retry-After": "20"}, b"")]

    response = client(server, retries=10).fetch(TARGET)

    # Waits end at 20, 40, 60, 80 s; a fifth would end at 100 s > 90 s.
    assert response.status_code == 503
    assert sleeps == [20.0, 20.0, 20.0, 20.0]
    assert server.calls == 5


def test_budget_counts_the_first_try(server: _Server, clock: tuple[_Clock, list[float]]) -> None:
    fake, sleeps = clock
    server.script = [(503, {"Retry-After": "10"}, b"")]

    def slow_first_try() -> None:
        if server.calls == 1:
            fake.now += 85.0

    server.on_request = slow_first_try

    response = client(server).fetch(TARGET)

    # 85 s spent + a 10 s wait would end past 90 s.
    assert response.status_code == 503
    assert server.calls == 1
    assert sleeps == []


def test_budget_is_per_call(server: _Server, clock: tuple[_Clock, list[float]]) -> None:
    _, sleeps = clock
    zr = client(server, retries=1)
    for _ in range(4):
        server.script = [(503, {"Retry-After": "30"}, b""), (200, {}, b"ok")]
        assert zr.fetch(TARGET).status_code == 200

    assert sleeps == [30.0] * 4


def test_custom_budget(server: _Server, clock: tuple[_Clock, list[float]]) -> None:
    _, sleeps = clock
    server.script = [(503, {"Retry-After": "10"}, b"")]

    response = client(server, retry_budget=5.0).fetch(TARGET)

    assert response.status_code == 503
    assert server.calls == 1
    assert sleeps == []


def test_422_and_5xx_share_one_budget(server: _Server, clock: tuple[_Clock, list[float]]) -> None:
    _, sleeps = clock
    server.script = [
        (503, {"Retry-After": "30"}, b""),
        (422, {"Retry-After": "30"}, NO_CONTENT),
        (200, {}, b"ok"),
    ]

    response = client(server, retries=5, retry_budget=50.0).fetch(TARGET)

    # The 503's wait ends at 30 s; the 422's would end at 60 s > 50 s.
    assert response.status_code == 422
    assert server.calls == 2
    assert sleeps == [30.0]


# ---- Extract ----


def test_no_data_extracted_is_not_retried(server: _Server, sleeps: list[float]) -> None:
    server.script = [(422, {}, NO_DATA)]

    response = client(server, retries=3).extract(TARGET)

    assert is_no_data_extracted(response)
    assert server.calls == 1


def test_extract_retries_another_422(server: _Server, sleeps: list[float]) -> None:
    server.script = [(422, {}, NO_CONTENT), (200, {}, b"{}")]

    response = client(server).extract(TARGET)

    assert response.status_code == 200
    assert server.calls == 2


def test_extract_returns_a_403_as_is_in_one_request(server: _Server, sleeps: list[float]) -> None:
    server.script = [(403, {"Retry-After": "1"}, DOMAIN_NOT_PREPARED), (200, {}, b"{}")]

    response = client(server).extract(TARGET)

    assert response.status_code == 403
    assert server.calls == 1
    assert sleeps == []


# ---- async calls ----


def test_fetch_async_shares_one_retry_count(server: _Server, sleeps: list[float]) -> None:
    server.script = [(503, {}, b""), (422, {}, NO_CONTENT), (422, {}, NO_CONTENT), (200, {}, b"ok")]

    response = asyncio.run(client(server).fetch_async(TARGET))

    # Two retries in all, on the client's thread pool too.
    assert response.status_code == 422
    assert server.calls == 3
    assert len(sleeps) == 2


def test_fetch_async_budget_is_per_call(server: _Server, clock: tuple[_Clock, list[float]]) -> None:
    _, sleeps = clock
    zr = client(server, retries=1, retry_budget=40.0)

    async def two_calls() -> list[int]:
        statuses = []
        for _ in range(2):
            server.script = [(503, {"Retry-After": "30"}, b""), (200, {}, b"ok")]
            statuses.append((await zr.fetch_async(TARGET)).status_code)
        return statuses

    # A budget shared across calls would stop the second retry at 60 s > 40 s.
    assert asyncio.run(two_calls()) == [200, 200]
    assert sleeps == [30.0, 30.0]


# ---- prepared domains ----


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("call", ["prepare_domain", "prepared_domain_status"])
def test_prepared_domain_requests_are_never_retried(
    server: _Server, sleeps: list[float], status: int, use_async: bool, call: str
) -> None:
    server.script = [(status, {"Retry-After": "1"}, b'{"code": "busy"}'), (200, {}, b"{}")]
    zr = client(server, retries=3)
    args = ("example.com", [TARGET]) if call == "prepare_domain" else ("example.com",)
    method = getattr(zr, f"{call}_async" if use_async else call)

    with pytest.raises(PreparedDomainError) as caught:
        asyncio.run(method(*args)) if use_async else method(*args)

    # The first answer is the result: no second request and no wait.
    assert caught.value.status_code == status
    assert server.calls == 1
    assert sleeps == []


# ---- is_no_data_extracted ----


def _fake_response(status_code: int, body: bytes = b"") -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = body
    return response


def test_is_no_data_extracted_matches_any_case() -> None:
    assert is_no_data_extracted(_fake_response(422, NO_DATA))
    assert is_no_data_extracted(_fake_response(422, b'{"code": "NO_DATA_EXTRACTED"}'))


def test_is_no_data_extracted_rejects_other_codes_statuses_and_bodies() -> None:
    assert not is_no_data_extracted(_fake_response(422, NO_CONTENT))
    assert not is_no_data_extracted(_fake_response(400, NO_DATA))
    assert not is_no_data_extracted(_fake_response(422, b"not json"))
    assert not is_no_data_extracted(_fake_response(422, b"[]"))
