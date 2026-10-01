"""Just enough bencode to read a .torrent without libtorrent: a dict's values
are handed back as their raw bytes, so a value can be hashed exactly as it
sits in the file (a v2 info-hash is the SHA-256 of the info dict) or read
further only where it's needed.
"""

from __future__ import annotations


def string(data: bytes, i: int = 0) -> tuple[bytes, int]:
    """The byte string that starts at i, and the index just past it."""
    colon = data.index(b":", i)
    start = colon + 1
    end = start + int(data[i:colon])
    if end > len(data):
        raise ValueError("truncated string")
    return data[start:end], end


def skip(data: bytes, i: int) -> int:
    """Index just past the bencoded value that starts at i."""
    c = data[i:i + 1]
    if c == b"i":
        return data.index(b"e", i) + 1
    if c in (b"l", b"d"):
        i += 1
        while data[i:i + 1] != b"e":
            i = skip(data, i)
        return i + 1
    if c.isdigit():
        return string(data, i)[1]
    raise ValueError(f"not bencode at offset {i}")


def entries(data: bytes) -> dict[bytes, bytes]:
    """A bencoded dict's keys, each mapped to its value's raw bytes."""
    if data[:1] != b"d":
        raise ValueError("expected a dict")
    out, i = {}, 1
    while data[i:i + 1] != b"e":
        key, i = string(data, i)
        end = skip(data, i)
        out[key] = data[i:end]
        i = end
    return out
