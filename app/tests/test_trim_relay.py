"""Tests for trim_relay.py against a fake YouTube server on localhost."""

import http.client
import http.server
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from trim_relay import TrimRelay

BLOB = bytes(range(256)) * 400  # 102,400 bytes


class FakeUpstream:
    """A local server that serves BLOB in parts chosen by a range=a-b query, like googlevideo."""

    def __init__(self):
        """Starts the server on a free port."""
        self.requests = []
        self.fail_next = 0
        self.fail_always = False
        upstream = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                upstream.requests.append((self.path, query.get("range", [None])[0]))
                if upstream.fail_always or upstream.fail_next:
                    upstream.fail_next = max(0, upstream.fail_next - 1)
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = (int(x) for x in query["range"][0].split("-"))
                body = BLOB[start:end + 1]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def url(self, name="media"):
        """
        Builds a stream URL on this server.

        Parameters:
        name (str): Path part, so refreshed links can be told apart.

        Returns:
        str: The URL, already carrying a query string like googlevideo links.
        """
        return f"http://127.0.0.1:{self.server.server_address[1]}/{name}?id=1"

    def close(self):
        """Stops the server."""
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstream():
    """
    Runs a fake upstream for one test.

    Returns:
    FakeUpstream: The running server.
    """
    server = FakeUpstream()
    yield server
    server.close()


@pytest.fixture
def relay():
    """
    Runs a relay with small chunks and no retry wait.

    Returns:
    TrimRelay: The running relay.
    """
    with TrimRelay(chunk_size=10_000, retry_delay=0) as running:
        yield running


def get(url, byte_range=None):
    """
    Fetches a URL, optionally with a Range header.

    Parameters:
    url (str): The URL to fetch.
    byte_range (str/None): A Range header value such as "bytes=0-99".

    Returns:
    tuple: (status code, headers, body bytes).
    """
    request = urllib.request.Request(url, headers={"Range": byte_range} if byte_range else {})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.headers, response.read()


def test_serves_a_byte_range_in_chunks(upstream, relay):
    """Checks a ranged request returns the right bytes and is fetched as several small chunks."""
    local = relay.add(upstream.url(), {}, len(BLOB))
    status, headers, body = get(local, "bytes=5000-34999")
    assert status == 206
    assert headers["Content-Range"] == f"bytes 5000-34999/{len(BLOB)}"
    assert body == BLOB[5000:35000]
    assert [r for _, r in upstream.requests] == ["5000-14999", "15000-24999", "25000-34999"]


def test_open_ended_range_runs_to_the_end(upstream, relay):
    """Checks "bytes=a-" is served up to the last byte."""
    local = relay.add(upstream.url(), {}, len(BLOB))
    status, _, body = get(local, "bytes=100000-")
    assert status == 206
    assert body == BLOB[100000:]


def test_no_range_serves_the_whole_file(upstream, relay):
    """Checks a plain GET returns every byte with status 200."""
    local = relay.add(upstream.url(), {}, len(BLOB))
    status, headers, body = get(local)
    assert status == 200
    assert int(headers["Content-Length"]) == len(BLOB)
    assert body == BLOB


def test_counts_fetched_bytes(upstream, relay):
    """Checks bytes_fetched adds up what was sent to the client."""
    local = relay.add(upstream.url(), {}, len(BLOB))
    get(local, "bytes=0-999")
    get(local, "bytes=2000-2499")
    assert relay.bytes_fetched == 1500


def test_streams_are_kept_apart(upstream, relay):
    """Checks two added streams each read their own upstream link."""
    first = relay.add(upstream.url("video"), {}, len(BLOB))
    second = relay.add(upstream.url("audio"), {}, len(BLOB))
    get(first, "bytes=0-9")
    get(second, "bytes=0-9")
    assert [p.split("?")[0] for p, _ in upstream.requests] == ["/video", "/audio"]


def test_retries_a_refused_chunk(upstream, relay):
    """Checks a 403 on one chunk is retried and the body still comes out whole."""
    local = relay.add(upstream.url(), {}, len(BLOB))
    upstream.fail_next = 2
    _, _, body = get(local, "bytes=0-19999")
    assert body == BLOB[:20000]
    assert not relay.failed


def test_refreshes_the_link_when_retries_run_out(upstream, relay):
    """Checks a stream that keeps failing asks for a fresh link and carries on from the same byte."""
    calls = []

    def refresh():
        calls.append(1)
        upstream.fail_always = False
        return upstream.url("fresh"), {}

    local = relay.add(upstream.url(), {}, len(BLOB), refresh=refresh)
    upstream.fail_always = True
    _, _, body = get(local, "bytes=0-9999")
    assert body == BLOB[:10000]
    assert calls == [1]
    assert upstream.requests[-1][0].startswith("/fresh")


def test_marks_failed_when_nothing_works(upstream, relay):
    """Checks the relay gives up, cuts the response short and sets failed."""
    local = relay.add(upstream.url(), {}, len(BLOB))
    upstream.fail_always = True
    with pytest.raises((urllib.error.URLError, ConnectionError, http.client.IncompleteRead)):
        status, headers, body = get(local, "bytes=0-9999")
        if len(body) < 10000:
            raise ConnectionError("short body")
    assert relay.failed


def test_unknown_stream_is_404(relay):
    """Checks a path that was never added is refused."""
    with pytest.raises(urllib.error.HTTPError) as error:
        get(relay.base_url + "/nope")
    assert error.value.code == 404


def test_watch_reports_bytes_and_speed(upstream, relay):
    """Checks the watcher is called with the running byte count and a speed."""
    seen = []
    relay.watch(lambda fetched, speed: seen.append((fetched, speed)), interval=0.02)
    local = relay.add(upstream.url(), {}, len(BLOB))
    get(local, "bytes=0-49999")
    time.sleep(0.1)
    assert seen and seen[-1][0] == 50000
    assert any(speed for _, speed in seen)


def test_watch_error_aborts_the_relay(upstream, relay):
    """Checks an exception from the watcher (e.g. a cancelled job) is kept and stops serving."""
    class Cancelled(Exception):
        pass

    def watcher(fetched, speed):
        raise Cancelled()

    relay.watch(watcher, interval=0.01)
    time.sleep(0.05)
    assert isinstance(relay.watch_error, Cancelled)
    assert relay.failed


def test_client_hanging_up_is_not_a_failure(upstream, relay):
    """Checks ffmpeg closing the connection early (it has its part) doesn't count as an upstream failure."""
    local = relay.add(upstream.url(), {}, len(BLOB))
    request = urllib.request.Request(local, headers={"Range": "bytes=0-"})
    response = urllib.request.urlopen(request, timeout=10)
    response.read(1000)
    response.close()
    time.sleep(0.2)
    assert not relay.failed
    assert all(r == f"{i * 10_000}-{i * 10_000 + 9_999}" for i, (_, r) in enumerate(upstream.requests))


def test_stop_watching_ends_reports(upstream, relay):
    """Checks no more reports arrive after stop_watching, even while bytes keep flowing."""
    seen = []
    relay.watch(lambda fetched, speed: seen.append(fetched), interval=0.01)
    time.sleep(0.05)
    relay.stop_watching()
    time.sleep(0.03)
    count = len(seen)
    get(relay.add(upstream.url(), {}, len(BLOB)), "bytes=0-999")
    time.sleep(0.05)
    assert len(seen) == count
