"""End to end: a libtorrent client downloads torrentizer-shaped items from
http_server.py as its only source, using the web seed URL reconcile.py
would hand a node."""

from __future__ import annotations

import os
import time

import pytest

from collab_cluster_utils.publisher.http_server import serve
from collab_cluster_utils.publisher.reconcile import web_seed_url
from collab_cluster_utils.publisher.torrent import read_torrent

lt = pytest.importorskip("libtorrent")

# Older versions fail piece hashes at file boundaries of v2 web seeds.
MULTI_FILE_OK = tuple(int(x) for x in lt.__version__.split(".")[:3]) >= (2, 1, 2)


def _item(watch_dir, item_id, files):
    item_dir = watch_dir / item_id
    for rel, size in files.items():
        (item_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (item_dir / rel).write_bytes(os.urandom(size))
    fs = lt.file_storage()
    lt.add_files(fs, str(item_dir))
    creator = lt.create_torrent(fs, flags=lt.create_torrent.v2_only)
    creator.set_priv(True)
    lt.set_piece_hashes(creator, str(watch_dir))
    (watch_dir / f"{item_id}.torrent").write_bytes(lt.bencode(creator.generate()))


@pytest.mark.parametrize("files", [
    {"image.tif": 3_000_001},
    pytest.param({"image.tif": 3_000_000, "meta.json": 777, "bands/b04.tif": 65_537},
                 marks=pytest.mark.skipif(not MULTI_FILE_OK, reason="needs libtorrent >= 2.1.2")),
], ids=["single-file", "multi-file"])
def test_download_via_web_seed(tmp_path, files):
    watch_dir = tmp_path / "watch"
    _item(watch_dir, "item", files)
    torrent = read_torrent(watch_dir / "item.torrent")

    server = serve(watch_dir, "127.0.0.1", 0)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        session = lt.session({"listen_interfaces": "127.0.0.1:0", "enable_dht": False,
                              "enable_lsd": False, "enable_upnp": False, "enable_natpmp": False})
        atp = lt.add_torrent_params()
        atp.ti = lt.torrent_info(str(watch_dir / "item.torrent"))
        atp.save_path = str(tmp_path / "out")
        atp.url_seeds = [web_seed_url(base, "item", torrent.single_file)]
        handle = session.add_torrent(atp)

        deadline = time.time() + 30
        while not handle.status().is_seeding and time.time() < deadline:
            time.sleep(0.05)
        assert handle.status().is_seeding
    finally:
        server.shutdown()

    for rel in files:
        downloaded = next((tmp_path / "out").rglob(os.path.basename(rel)))
        assert downloaded.read_bytes() == (watch_dir / "item" / rel).read_bytes()
