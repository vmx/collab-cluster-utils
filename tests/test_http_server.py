from __future__ import annotations

import os
import urllib.error
import urllib.request

from collab_cluster_utils.publisher.http_server import serve
from collab_cluster_utils.publisher.seed import SeedStore

from .test_seed import _build_sample_torrent


def _start(tmp_path):
    watch_dir = tmp_path / "watch"
    item_dir = watch_dir / "item-a"
    torrent_path = watch_dir / "item-a.torrent"
    content = os.urandom(4096)
    _build_sample_torrent(item_dir, torrent_path, content)

    store = SeedStore("127.0.0.1", 0)
    store.sync({torrent_path})
    info_hash = store.info_hashes()[0]

    server = serve(store, "127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    return server, base, info_hash, torrent_path.read_bytes()


def test_serves_a_known_torrent(tmp_path):
    server, base, info_hash, expected = _start(tmp_path)
    try:
        with urllib.request.urlopen(f"{base}/dataset/{info_hash}.torrent", timeout=5) as r:
            assert r.read() == expected
    finally:
        server.shutdown()


def test_404s_an_unknown_hash(tmp_path):
    server, base, _info_hash, _expected = _start(tmp_path)
    try:
        unknown = "0" * 64
        try:
            urllib.request.urlopen(f"{base}/dataset/{unknown}.torrent", timeout=5)
            assert False, "expected a 404"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()


def test_404s_a_malformed_path(tmp_path):
    server, base, _info_hash, _expected = _start(tmp_path)
    try:
        try:
            urllib.request.urlopen(f"{base}/not-a-dataset-route", timeout=5)
            assert False, "expected a 404"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
