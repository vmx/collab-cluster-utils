"""reconcile.tick()'s decisions, with notify() replaced -- no real HTTP."""

from __future__ import annotations

import io
import urllib.error

import pytest

from collab_cluster_utils.publisher import reconcile
from collab_cluster_utils.publisher.state import State

from .helpers import write_torrent

NODE = "10.0.0.5:8001"
BASE = "http://10.0.0.9:8090/"


@pytest.fixture
def env(tmp_path, monkeypatch):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    calls = []
    monkeypatch.setattr(reconcile, "notify",
                        lambda node, info_hash, torrent_url, web_seed:
                        calls.append((node, info_hash, torrent_url, web_seed)))
    return watch_dir, State(tmp_path / "state.sqlite3"), calls


def tick(watch_dir, state):
    reconcile.tick(state, watch_dir, NODE, BASE)


def test_notifies_where_to_fetch_the_torrent_and_the_data(env):
    watch_dir, state, calls = env
    single = write_torrent(watch_dir / "item-a.torrent", "x", {"image.tif": 5})
    multi = write_torrent(watch_dir / "item-b.torrent", "item-b", {"image.tif": 5, "meta.json": 7})
    tick(watch_dir, state)
    assert sorted(calls) == sorted([
        (NODE, single, BASE + "item-a.torrent", BASE + "item-a/"),
        (NODE, multi, BASE + "item-b.torrent", BASE),
    ])


def test_a_settled_directory_sends_nothing(env):
    watch_dir, state, calls = env
    write_torrent(watch_dir / "item-a.torrent", "x", {"image.tif": 5})
    tick(watch_dir, state)
    tick(watch_dir, state)
    assert len(calls) == 1


def test_a_restart_does_not_resend(env, tmp_path):
    watch_dir, state, calls = env
    write_torrent(watch_dir / "item-a.torrent", "x", {"image.tif": 5})
    tick(watch_dir, state)
    tick(watch_dir, State(tmp_path / "state.sqlite3"))
    assert len(calls) == 1


def test_forgets_what_left_the_directory(env):
    watch_dir, state, calls = env
    path = watch_dir / "item-a.torrent"
    write_torrent(path, "x", {"image.tif": 5})
    tick(watch_dir, state)
    path.unlink()
    tick(watch_dir, state)
    assert state.names() == set()


def test_skips_unusable_torrents_and_carries_on(env):
    watch_dir, state, calls = env
    (watch_dir / "broken.torrent").write_bytes(b"garbage")
    write_torrent(watch_dir / "renamed.torrent", "other-name", {"image.tif": 5, "meta.json": 7})
    good = write_torrent(watch_dir / "item-a.torrent", "x", {"image.tif": 5})
    tick(watch_dir, state)
    assert [c[1] for c in calls] == [good]
    assert state.names() == {"item-a.torrent"}


def test_a_rejected_item_is_retried_and_does_not_block_others(env, monkeypatch):
    watch_dir, state, _ = env
    write_torrent(watch_dir / "item-a.torrent", "x", {"image.tif": 5})
    write_torrent(watch_dir / "item-b.torrent", "x", {"image.tif": 5})
    attempts = []

    def flaky(node, info_hash, torrent_url, web_seed):
        attempts.append(torrent_url)
        if torrent_url.endswith("item-a.torrent") and len(attempts) == 1:
            raise urllib.error.HTTPError(torrent_url, 500, "boom", None, None)

    monkeypatch.setattr(reconcile, "notify", flaky)
    tick(watch_dir, state)
    assert state.names() == {"item-b.torrent"}
    tick(watch_dir, state)
    assert state.names() == {"item-a.torrent", "item-b.torrent"}
    assert len(attempts) == 3


def test_a_rejection_logs_the_nodes_error(env, monkeypatch, caplog):
    watch_dir, state, _ = env
    write_torrent(watch_dir / "item-a.torrent", "x", {"image.tif": 5})

    def reject(node, info_hash, torrent_url, web_seed):
        raise urllib.error.HTTPError(torrent_url, 404, "Not Found", None,
                                     io.BytesIO(b'{"error": "unknown dataset"}'))

    monkeypatch.setattr(reconcile, "notify", reject)
    tick(watch_dir, state)
    assert 'HTTP 404 {"error": "unknown dataset"}' in caplog.text


def test_an_unreachable_node_ends_the_pass(env, monkeypatch):
    watch_dir, state, _ = env
    for i in range(5):
        write_torrent(watch_dir / f"item-{i}.torrent", "x", {"image.tif": 5})
    attempts = []

    def unreachable(*args):
        attempts.append(args)
        raise urllib.error.URLError(ConnectionRefusedError())

    monkeypatch.setattr(reconcile, "notify", unreachable)
    tick(watch_dir, state)
    assert len(attempts) == 1
    assert state.names() == set()
