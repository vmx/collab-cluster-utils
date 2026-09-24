from __future__ import annotations

import os

import pytest

from collab_cluster_utils.publisher.torrent import read_torrent

from .helpers import bencode, write_torrent


def test_single_file(tmp_path):
    path = tmp_path / "item-a.torrent"
    info_hash = write_torrent(path, "item-a", {"image.tif": 5})
    torrent = read_torrent(path)
    assert torrent.info_hash == info_hash
    assert torrent.single_file


def test_directory(tmp_path):
    path = tmp_path / "item-b.torrent"
    info_hash = write_torrent(path, "item-b", {"image.tif": 5, "meta.json": 7})
    torrent = read_torrent(path)
    assert torrent.info_hash == info_hash
    assert torrent.name == "item-b"
    assert not torrent.single_file


@pytest.mark.parametrize("content", [
    b"not a torrent",
    b"d4:infod4:name1:x",                      # truncated
    bencode({"info": {"name": "x", "length": 1, "pieces": b"\0" * 20}}),  # v1
    bencode({"announce": "x"}),                 # no info
])
def test_rejects_what_isnt_a_readable_v2_torrent(tmp_path, content):
    path = tmp_path / "bad.torrent"
    path.write_bytes(content)
    with pytest.raises(ValueError):
        read_torrent(path)


def test_agrees_with_libtorrent(tmp_path):
    lt = pytest.importorskip("libtorrent")
    for item, files in (("single", {"image.tif": 300_001}),
                        ("multi", {"image.tif": 300_000, "meta.json": 777, "bands/b04.tif": 65_537})):
        item_dir = tmp_path / item
        for rel, size in files.items():
            (item_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (item_dir / rel).write_bytes(os.urandom(size))
        fs = lt.file_storage()
        lt.add_files(fs, str(item_dir))
        creator = lt.create_torrent(fs, flags=lt.create_torrent.v2_only)
        creator.set_priv(True)
        lt.set_piece_hashes(creator, str(tmp_path))
        path = tmp_path / f"{item}.torrent"
        path.write_bytes(lt.bencode(creator.generate()))

        ti = lt.torrent_info(str(path))
        torrent = read_torrent(path)
        assert torrent.info_hash == str(ti.info_hashes().v2)
        assert torrent.single_file == (item == "single")
        assert torrent.name == item
