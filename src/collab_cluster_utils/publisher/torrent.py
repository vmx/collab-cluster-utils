"""Just enough bencode to identify a v2 .torrent without libtorrent: its
info-hash (SHA-256 of the info dict, byte for byte as it sits in the file),
its name, and whether it is a single file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Torrent:
    info_hash: str
    name: str
    single_file: bool


def _string(data: bytes, i: int) -> tuple[bytes, int]:
    colon = data.index(b":", i)
    start = colon + 1
    end = start + int(data[i:colon])
    if end > len(data):
        raise ValueError("truncated string")
    return data[start:end], end


def _skip(data: bytes, i: int) -> int:
    """Index just past the bencoded value that starts at i."""
    c = data[i:i + 1]
    if c == b"i":
        return data.index(b"e", i) + 1
    if c in (b"l", b"d"):
        i += 1
        while data[i:i + 1] != b"e":
            i = _skip(data, i)
        return i + 1
    if c.isdigit():
        return _string(data, i)[1]
    raise ValueError(f"not bencode at offset {i}")


def _entries(data: bytes) -> dict[bytes, bytes]:
    """A bencoded dict's keys, each mapped to its value's raw bytes."""
    if data[:1] != b"d":
        raise ValueError("expected a dict")
    out, i = {}, 1
    while data[i:i + 1] != b"e":
        key, i = _string(data, i)
        end = _skip(data, i)
        out[key] = data[i:end]
        i = end
    return out


def read_torrent(path: Path) -> Torrent:
    try:
        info = _entries(path.read_bytes())[b"info"]
        fields = _entries(info)
        if fields.get(b"meta version") != b"i2e":
            raise ValueError("not a v2 torrent")
        name = _string(fields[b"name"], 0)[0].decode()
        tree = _entries(fields[b"file tree"])
        # A single file sits at the top of the tree as {name: {"": {...}}};
        # a directory's files sit below it.
        single_file = len(tree) == 1 and b"" in _entries(next(iter(tree.values())))
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"{path}: not a readable v2 .torrent: {exc}") from exc
    return Torrent(hashlib.sha256(info).hexdigest(), name, single_file)
