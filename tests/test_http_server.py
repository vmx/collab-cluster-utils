from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

from collab_cluster_utils.publisher.http_server import _parse_range, serve

CONTENT = os.urandom(10_000)


@pytest.fixture
def base(tmp_path):
    (tmp_path / "item").mkdir()
    (tmp_path / "item" / "data.bin").write_bytes(CONTENT)
    (tmp_path / "empty.bin").write_bytes(b"")
    server = serve(tmp_path, "127.0.0.1", 0)
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def get(url, range_header=None):
    request = urllib.request.Request(url, headers={"Range": range_header} if range_header else {})
    with urllib.request.urlopen(request, timeout=5) as r:
        return r.status, dict(r.headers), r.read()


def status_of(url, range_header=None):
    try:
        return get(url, range_header)[0]
    except urllib.error.HTTPError as exc:
        return exc.code


def test_whole_file(base):
    status, headers, body = get(f"{base}/item/data.bin")
    assert (status, body) == (200, CONTENT)
    assert headers["Accept-Ranges"] == "bytes"


def test_range(base):
    status, headers, body = get(f"{base}/item/data.bin", "bytes=100-199")
    assert (status, body) == (206, CONTENT[100:200])
    assert headers["Content-Range"] == f"bytes 100-199/{len(CONTENT)}"


def test_open_ended_and_overlong_ranges_stop_at_the_end(base):
    assert get(f"{base}/item/data.bin", "bytes=9990-")[2] == CONTENT[9990:]
    assert get(f"{base}/item/data.bin", "bytes=9990-99999")[2] == CONTENT[9990:]


def test_suffix_range(base):
    assert get(f"{base}/item/data.bin", "bytes=-10")[2] == CONTENT[-10:]


def test_unsatisfiable_range(base):
    assert status_of(f"{base}/item/data.bin", "bytes=10000-") == 416


def test_empty_file(base):
    assert get(f"{base}/empty.bin")[:1] == (200,)


@pytest.mark.parametrize("path", ["/item", "/item/", "/", "/missing.bin", "/../etc/passwd"])
def test_only_files_inside_the_directory(base, path):
    assert status_of(base + path) == 404


@pytest.mark.parametrize("header, size, expected", [
    ("bytes=0-0", 10, (0, 0)),
    ("bytes=5-", 10, (5, 9)),
    ("bytes=-3", 10, (7, 9)),
    ("bytes=-30", 10, (0, 9)),
    ("bytes=10-", 10, None),
    ("bytes=5-4", 10, None),
    ("bytes=-0", 10, None),
    ("bytes=-", 10, None),
    ("bytes=0-1,4-5", 10, None),
    ("items=0-1", 10, None),
])
def test_parse_range(header, size, expected):
    assert _parse_range(header, size) == expected
