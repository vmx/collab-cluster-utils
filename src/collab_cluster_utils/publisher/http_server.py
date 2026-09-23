"""One read-only route, nothing else: GET /dataset/<info_hash>.torrent.

This is the exact route collab-cluster-experiment's node.py already exposes
and already has a client for (node_client.fetch_torrent_bytes) -- a node
fetching a .torrent from an external producer is meant to look, on the wire,
identical to it fetching one from another node. Nothing else about a node's
HTTP API is implemented here; a publisher isn't a node and isn't discoverable
except by being told its address directly (see reconcile.py).
"""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from .seed import SeedStore

_DATASET_RE = re.compile(r"^/dataset/([0-9a-f]{64})\.torrent$")


class _Handler(BaseHTTPRequestHandler):
    store: SeedStore

    def do_GET(self) -> None:
        match = _DATASET_RE.match(self.path)
        blob = self.store.torrent_bytes(match.group(1)) if match else None
        if blob is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-bittorrent")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def log_message(self, *args) -> None:
        pass


def serve(store: SeedStore, bind_host: str, http_port: int) -> HTTPServer:
    """Start the server on its own daemon thread and return it (for tests
    that want to shut it down; the running service just lets it live for the
    process's lifetime).

    Single-threaded HTTPServer, not ThreadingHTTPServer: there is only ever
    one caller (the target node), occasionally -- no concurrent requests to
    justify per-request threads, and one thread means SeedStore.lock only
    ever has to arbitrate against the reconcile loop, not against itself.
    """
    handler = type("Handler", (_Handler,), {"store": store})
    server = HTTPServer((bind_host, http_port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
