"""Local relay that lets ffmpeg read YouTube streams at full speed for trimmed downloads.

YouTube throttles one long request (how ffmpeg reads) to about playback speed,
but serves small range= requests (how yt-dlp reads) at full speed. ffmpeg reads
from this relay instead, and the relay fetches what it asks for in chunks.
"""

import http.server
import itertools
import socketserver
import threading
import time

import requests

CHUNK_SIZE = 10 * 1024 * 1024
CHUNK_ATTEMPTS = 3
READ_SIZE = 64 * 1024


class _ClientGone(Exception):
    """ffmpeg closed the connection; not an upstream error, so never retried."""


class _Stream:
    """One upstream file the relay serves: its link, headers, size and refresher."""

    def __init__(self, url, headers, size, refresh):
        """
        Stores the stream details.

        Parameters:
        url (str): The upstream link; must accept a range=a-b query parameter.
        headers (dict): HTTP headers to send upstream.
        size (int): Exact file size in bytes.
        refresh (callable/None): Returns a fresh (url, headers) when the link stops working.
        """
        self.url, self.headers, self.size, self.refresh = url, headers, size, refresh
        self.refreshed = False


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """HTTP server with one thread per request, so ffmpeg can read streams side by side."""

    daemon_threads = True


class TrimRelay:
    """Serves added streams on 127.0.0.1, fetching each ranged read upstream in chunks."""

    def __init__(self, chunk_size=CHUNK_SIZE, retry_delay=1.0):
        """
        Starts the relay on a free local port.

        Parameters:
        chunk_size (int): Bytes per upstream request.
        retry_delay (int/float): Seconds to wait before retrying a refused chunk.
        """
        self.chunk_size = chunk_size
        self.retry_delay = retry_delay
        self.bytes_fetched = 0
        self.failed = False
        self.watch_error = None
        self._streams = {}
        self._ids = itertools.count()
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._watch_stopped = threading.Event()
        self._session = requests.Session()
        self._server = _Server(("127.0.0.1", 0), self._handler_class())
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, args=(0.05,), daemon=True).start()

    def __enter__(self):
        """
        Returns the relay for a with block.

        Returns:
        TrimRelay: This relay.
        """
        return self

    def __exit__(self, *exc):
        """
        Closes the relay when the with block ends.

        Parameters:
        exc (tuple): Exception details, unused.
        """
        self.close()

    def add(self, url, headers, size, refresh=None):
        """
        Registers an upstream stream and gives the local link ffmpeg should read.

        Parameters:
        url (str): The upstream link; must accept a range=a-b query parameter.
        headers (dict): HTTP headers to send upstream.
        size (int): Exact file size in bytes.
        refresh (callable/None): Returns a fresh (url, headers) when the link stops working.

        Returns:
        str: The local link.
        """
        stream_id = str(next(self._ids))
        self._streams[stream_id] = _Stream(url, dict(headers or {}), int(size), refresh)
        return f"{self.base_url}/{stream_id}"

    def watch(self, callback, interval=0.5):
        """
        Calls callback(bytes_fetched, speed) every interval until the relay closes.

        If the callback raises (e.g. the job was cancelled), the error is kept in
        watch_error and the relay stops serving, which makes ffmpeg fail.

        Parameters:
        callback (callable): Receives (bytes fetched so far, bytes per second or None).
        interval (int/float): Seconds between calls.
        """
        def run():
            last_bytes, last_time, speed = 0, time.monotonic(), None
            while not self._closed.wait(interval):
                if self._watch_stopped.is_set():
                    return

                # Smooth the speed so the page doesn't flicker
                now, fetched = time.monotonic(), self.bytes_fetched
                current = (fetched - last_bytes) / max(now - last_time, 1e-6)
                speed = current if speed is None else 0.7 * speed + 0.3 * current
                last_bytes, last_time = fetched, now

                try:
                    callback(fetched, speed or None)
                except Exception as e:
                    self.watch_error = e
                    self.abort()
                    return

        threading.Thread(target=run, daemon=True).start()

    def stop_watching(self):
        """Stops the watch() reports; the relay keeps serving."""
        self._watch_stopped.set()

    def abort(self):
        """Marks the relay failed and stops every response in progress."""
        self.failed = True
        self._closed.set()

    def close(self):
        """Stops the relay and frees its port."""
        self._closed.set()
        self._server.shutdown()
        self._server.server_close()
        self._session.close()

    def _fetch_chunk(self, stream, start, end, write):
        """
        Copies upstream bytes start..end to write(), retrying and refreshing the link on errors.

        Parameters:
        stream (_Stream): The stream to read.
        start (int): First byte.
        end (int): Last byte, inclusive.
        write (callable): Receives each piece of the body.

        Raises:
        IOError: When every attempt failed.
        """
        attempts = CHUNK_ATTEMPTS
        while True:
            # Bytes already passed on are skipped on a retry
            try:
                response = self._session.get(
                    f"{stream.url}&range={start}-{end}",
                    headers=stream.headers, stream=True, timeout=30,
                )
                with response:
                    response.raise_for_status()
                    for piece in response.iter_content(READ_SIZE):
                        if self._closed.is_set():
                            raise IOError("Relay closed")
                        try:
                            write(piece)
                        except OSError as e:
                            raise _ClientGone() from e
                        start += len(piece)
                if start > end:
                    return
                raise IOError("Upstream ended early")
            except (requests.RequestException, IOError) as e:
                if self._closed.is_set():
                    raise IOError("Relay closed") from e
                attempts -= 1

            # Out of tries: get a fresh link once, then give up
            if attempts == 0:
                if stream.refresh and not stream.refreshed:
                    stream.refreshed = True
                    stream.url, stream.headers = stream.refresh()
                    attempts = CHUNK_ATTEMPTS
                    continue
                raise IOError("Upstream keeps refusing the stream")
            time.sleep(self.retry_delay)

    def _handler_class(self):
        """
        Builds the request handler bound to this relay.

        Returns:
        type: A BaseHTTPRequestHandler subclass.
        """
        relay = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _stream(self):
                stream = relay._streams.get(self.path.strip("/"))
                if stream is None:
                    self.send_error(404)
                return stream

            def _range(self, size):
                header = self.headers.get("Range")
                if not header or not header.startswith("bytes="):
                    return None
                first, _, last = header[len("bytes="):].partition("-")
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
                return start, end

            def do_HEAD(self):
                stream = self._stream()
                if stream:
                    self.send_response(200)
                    self.send_header("Content-Length", str(stream.size))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()

            def do_GET(self):
                stream = self._stream()
                if stream is None:
                    return

                # Answer the range ffmpeg asked for (or the whole file)
                byte_range = self._range(stream.size)
                start, end = byte_range or (0, stream.size - 1)
                self.send_response(206 if byte_range else 200)
                if byte_range:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{stream.size}")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                def write(piece):
                    self.wfile.write(piece)
                    with relay._lock:
                        relay.bytes_fetched += len(piece)

                # Fetch it upstream one chunk at a time
                try:
                    while start <= end:
                        stop = min(start + relay.chunk_size - 1, end)
                        relay._fetch_chunk(stream, start, stop, write)
                        start = stop + 1
                except _ClientGone:
                    pass  # ffmpeg stops reading once it has its part
                except Exception:
                    relay.failed = True
                    self.close_connection = True

        return Handler
