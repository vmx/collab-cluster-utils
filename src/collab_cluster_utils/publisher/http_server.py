"""A read-only file server over the watched directory: the source of each
.torrent, and the web seed (BEP 19) a node downloads the data from.

Regular files only, no directory listings, one byte range per request --
all libtorrent asks for. One request per connection (HTTP/1.0) keeps the
handling simple; libtorrent opens a new connection as it needs one.
"""

from __future__ import annotations

import os
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def handle_one_request(self) -> None:
        # A node may drop a web seed connection mid-response; that isn't an error.
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def do_GET(self) -> None:
        self._serve(body=True)

    def do_HEAD(self) -> None:
        self._serve(body=False)

    def _serve(self, body: bool) -> None:
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            header = self.headers.get("Range")
            if header is None:
                start, end = 0, size - 1
                self.send_response(200)
            else:
                span = _parse_range(header, size)
                if span is None:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start, end = span
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            length = end - start + 1
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if body and length:
                self.connection.sendfile(f, start, length)


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """(start, end) inclusive for a single "bytes=" range, or None if it
    can't be satisfied."""
    match = _RANGE.match(header.strip())
    if not match or not (match.group(1) or match.group(2)):
        return None
    first, last = match.group(1), match.group(2)
    if not first:  # suffix: the last N bytes
        n = int(last)
        return (max(size - n, 0), size - 1) if n and size else None
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    return (start, end) if start <= end else None


def serve(watch_dir: Path, bind_host: str, http_port: int) -> ThreadingHTTPServer:
    """Start serving on a daemon thread and return the server."""
    handler = partial(_Handler, directory=str(watch_dir))
    server = ThreadingHTTPServer((bind_host, http_port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
