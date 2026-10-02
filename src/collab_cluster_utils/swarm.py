"""The parts of a collab-cluster node's HTTP API the tools here use: who the
peers are, what each node holds or newly takes (followed by cursor), a
dataset's .torrent, and telling the local node to add or remove one. Standard library only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 10.0


class Resync(Exception):
    """The node can't answer from this cursor (it restarted, or the cursor is
    too old): list everything again."""


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        return response.read()


def _post(url: str, body: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read())


def peers(base: str) -> dict:
    """{"self": {"node": key, ...}, "peers": [{"node", "ip", "http", ...}]}"""
    return json.loads(_get(f"{base}/peers"))


def cursor(base: str) -> str:
    """Where the node's holdings stream is now: follow it from here to see
    only what changes from now on."""
    return json.loads(_get(f"{base}/stats"))["cursor"]


def node_key(base: str) -> str:
    """The node's swarm-wide identity."""
    return json.loads(_get(f"{base}/stats"))["node_key"]


def holdings(base: str, cursor: str | None) -> dict:
    """One page: {"cursor", "more", "holdings": [{info_hash, state, name, ...}]}.
    Without a cursor the first page of everything held; with one, the next page
    or what changed since."""
    url = f"{base}/holdings"
    if cursor:
        url += "?since=" + urllib.parse.quote(cursor)
    try:
        return json.loads(_get(url))
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise Resync(cursor) from exc
        raise


def torrent(base: str, info_hash: str) -> bytes:
    return _get(f"{base}/dataset/{info_hash}.torrent")


def add(base: str, info_hash: str) -> dict:
    return _post(f"{base}/add", {"info_hash": info_hash})


def remove(base: str, info_hash: str) -> dict:
    return _post(f"{base}/remove", {"info_hash": info_hash})


def error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode(errors="replace").strip()
    except OSError:
        return exc.reason
