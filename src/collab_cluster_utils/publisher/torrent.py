"""Identify a v2 .torrent without libtorrent: its info-hash (SHA-256 of the
info dict, byte for byte as it sits in the file), its name, and whether it is
a single file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..bencode import entries, string


@dataclass(frozen=True)
class Torrent:
    info_hash: str
    name: str
    single_file: bool


def read_torrent(path: Path) -> Torrent:
    try:
        info = entries(path.read_bytes())[b"info"]
        fields = entries(info)
        if fields.get(b"meta version") != b"i2e":
            raise ValueError("not a v2 torrent")
        name = string(fields[b"name"], 0)[0].decode()
        tree = entries(fields[b"file tree"])
        # A single file sits at the top of the tree as {name: {"": {...}}};
        # a directory's files sit below it.
        single_file = len(tree) == 1 and b"" in entries(next(iter(tree.values())))
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"{path}: not a readable v2 .torrent: {exc}") from exc
    return Torrent(hashlib.sha256(info).hexdigest(), name, single_file)
