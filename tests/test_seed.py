"""End-to-end: SeedStore actually serves bytes to a real BitTorrent peer.

Deliberately doesn't touch http_server.py or node.py -- the "consumer" here
is a bare libtorrent session standing in for what a real node's take() does
once it already has the .torrent bytes (however it got them) and is told to
connect_peer() at the seed's address. That's the one part with real risk:
whether save_path resolution and the session settings actually let a byte-
for-byte transfer complete.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import libtorrent as lt
import pytest

from collab_cluster_utils.publisher.seed import SeedStore


def _build_sample_torrent(item_dir: Path, torrent_path: Path, content: bytes) -> None:
    """Mirrors collab-cluster-torrentizer's create_torrent (v2-only, private),
    minus the embedded-metadata key, which seed.py never looks at."""
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "file.bin").write_bytes(content)
    files = lt.list_files(str(item_dir))
    creator = lt.create_torrent(files, flags=lt.create_torrent.v2_only)
    creator.set_priv(True)
    lt.set_piece_hashes(creator, str(item_dir.parent))
    torrent_path.write_bytes(lt.bencode(creator.generate()))


def test_seed_store_serves_a_real_transfer(tmp_path):
    content = os.urandom(64 * 1024)
    watch_dir = tmp_path / "watch"
    item_dir = watch_dir / "item-a"
    torrent_path = watch_dir / "item-a.torrent"
    _build_sample_torrent(item_dir, torrent_path, content)

    store = SeedStore("127.0.0.1", 0)
    added, dropped = store.sync({torrent_path})
    assert len(added) == 1
    assert dropped == set()

    seed_port = store.session.listen_port()
    assert seed_port != 0

    # Stands in for a node that already has the .torrent bytes (in reality,
    # fetched over HTTP from http_server.py) and is told to connect_peer()
    # at the seed's address, exactly as take() does.
    consumer = lt.session({
        "listen_interfaces": "127.0.0.1:0",
        "enable_dht": False, "enable_lsd": False,
        "enable_upnp": False, "enable_natpmp": False,
    })
    consumer_root = tmp_path / "consumer"
    consumer_root.mkdir()

    ti = lt.torrent_info(lt.bdecode(torrent_path.read_bytes()))
    atp = lt.add_torrent_params()
    atp.ti = ti
    atp.save_path = str(consumer_root)
    handle = consumer.add_torrent(atp)
    handle.connect_peer(("127.0.0.1", seed_port))

    deadline = time.time() + 15
    while time.time() < deadline:
        st = handle.status()
        if st.is_seeding or st.progress >= 1.0:
            break
        time.sleep(0.1)
    else:
        pytest.fail("transfer did not complete in time")

    downloaded = list(consumer_root.rglob("file.bin"))
    assert len(downloaded) == 1
    assert downloaded[0].read_bytes() == content


def test_sync_drops_items_removed_from_disk(tmp_path):
    watch_dir = tmp_path / "watch"
    item_dir = watch_dir / "item-a"
    torrent_path = watch_dir / "item-a.torrent"
    _build_sample_torrent(item_dir, torrent_path, os.urandom(4096))

    store = SeedStore("127.0.0.1", 0)
    added, _dropped = store.sync({torrent_path})
    info_hash = next(iter(added))
    assert store.info_hashes() == [info_hash]

    _added, dropped = store.sync(set())
    assert dropped == {info_hash}
    assert store.info_hashes() == []


def test_sync_does_not_reread_an_already_tracked_path(tmp_path, monkeypatch):
    """The cost of a tick has to stay proportional to what changed, not to
    everything already resident -- this is what makes that true."""
    watch_dir = tmp_path / "watch"
    item_dir = watch_dir / "item-a"
    torrent_path = watch_dir / "item-a.torrent"
    _build_sample_torrent(item_dir, torrent_path, os.urandom(4096))

    store = SeedStore("127.0.0.1", 0)
    store.sync({torrent_path})

    reads = []
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self):
        reads.append(self)
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    added, dropped = store.sync({torrent_path})
    assert added == set()
    assert dropped == set()
    assert reads == []  # never opened -- it was already tracked by path


def test_sync_skips_an_unreadable_torrent_and_loads_the_rest(tmp_path):
    watch_dir = tmp_path / "watch"
    good = watch_dir / "item-a.torrent"
    _build_sample_torrent(watch_dir / "item-a", good, os.urandom(4096))
    bad = watch_dir / "item-b.torrent"
    bad.write_bytes(b"not a torrent")

    store = SeedStore("127.0.0.1", 0)
    added, dropped = store.sync({good, bad})
    assert len(added) == 1
    assert dropped == set()


def test_sync_serves_without_rehashing(tmp_path):
    watch_dir = tmp_path / "watch"
    torrent_path = watch_dir / "item-a.torrent"
    _build_sample_torrent(watch_dir / "item-a", torrent_path, os.urandom(4096))

    store = SeedStore("127.0.0.1", 0)
    added, _ = store.sync({torrent_path})
    handle = store.items[next(iter(added))]["handle"]
    assert handle.flags() & lt.torrent_flags.seed_mode
