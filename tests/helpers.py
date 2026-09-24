"""Write v2 .torrent files for tests without libtorrent. Piece data is fake:
only the structure reconcile.py and torrent.py read has to be right."""

from __future__ import annotations

import hashlib
from pathlib import Path


def bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        items = sorted((k.encode() if isinstance(k, str) else k, v) for k, v in value.items())
        return b"d" + b"".join(bencode(k) + bencode(v) for k, v in items) + b"e"
    raise TypeError(value)


def _leaf(size: int) -> dict:
    return {"": {"length": size, "pieces root": b"\0" * 32}}


def write_torrent(path: Path, name: str, files: dict[str, int]) -> str:
    """A torrent of the directory `name` holding `files`; with exactly one file
    it's shaped like libtorrent makes one for such a directory (the file at
    the top of the tree). Returns its v2 info-hash."""
    tree = {rel: _leaf(size) for rel, size in files.items()}
    info = {"name": name, "meta version": 2, "piece length": 16384, "file tree": tree}
    path.write_bytes(bencode({"info": info, "piece layers": {}}))
    return hashlib.sha256(bencode(info)).hexdigest()
