"""Unit tests for the sync/notify loop, decoupled from real libtorrent and
real HTTP -- seed.py's own correctness is covered by test_seed.py; this is
only about tick()'s decisions.
"""

from __future__ import annotations

import urllib.error

from collab_cluster_utils.publisher import reconcile
from collab_cluster_utils.publisher.state import State


class FakeStore:
    """Stands in for SeedStore. sync() reports whichever of the given paths
    it hasn't seen before as added, and any previously-seen path missing
    from the given set as dropped -- the same contract SeedStore.sync()
    has, keyed by filename stem instead of a real info-hash."""

    def __init__(self):
        self.sync_calls = []
        self._tracked: set[str] = set()

    def sync(self, torrent_paths):
        self.sync_calls.append(set(torrent_paths))
        current = {p.stem for p in torrent_paths}
        added = current - self._tracked
        dropped = self._tracked - current
        self._tracked = current
        return added, dropped

    def info_hashes(self):
        return list(self._tracked)


def _touch(*paths):
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def _tick(store, state, watch_dir, calls, peer, monkeypatch):
    monkeypatch.setattr(reconcile, "notify",
                         lambda node, info_hash, p: calls.append((node, info_hash, p)))
    reconcile.tick(store, state, watch_dir, "10.0.0.5:8001", peer)


def test_notifies_new_items_once(tmp_path, monkeypatch):
    watch_dir = tmp_path / "watch"
    _touch(watch_dir / "item-a.torrent", watch_dir / "item-b.torrent")

    store = FakeStore()
    state = State(tmp_path / "state.sqlite3")
    peer = {"ip": "10.0.0.1", "bt": 6890, "http": 8090}
    calls = []
    _tick(store, state, watch_dir, calls, peer, monkeypatch)

    assert sorted(calls) == [
        ("10.0.0.5:8001", "item-a", peer),
        ("10.0.0.5:8001", "item-b", peer),
    ]
    assert state.is_notified("item-a")
    assert state.is_notified("item-b")


def test_does_not_renotify_unchanged_items(tmp_path, monkeypatch):
    """A settled directory should produce no notify traffic at all."""
    watch_dir = tmp_path / "watch"
    _touch(watch_dir / "item-a.torrent")

    store = FakeStore()
    state = State(tmp_path / "state.sqlite3")
    peer = {"ip": "10.0.0.1", "bt": 6890, "http": 8090}
    calls = []
    _tick(store, state, watch_dir, calls, peer, monkeypatch)
    _tick(store, state, watch_dir, calls, peer, monkeypatch)
    _tick(store, state, watch_dir, calls, peer, monkeypatch)

    assert len(calls) == 1


def test_forgets_items_removed_from_disk(tmp_path, monkeypatch):
    watch_dir = tmp_path / "watch"
    a = watch_dir / "item-a.torrent"
    _touch(a)

    store = FakeStore()
    state = State(tmp_path / "state.sqlite3")
    peer = {"ip": "10.0.0.1", "bt": 6890, "http": 8090}
    calls = []
    _tick(store, state, watch_dir, calls, peer, monkeypatch)
    assert state.is_notified("item-a")

    a.unlink()
    _tick(store, state, watch_dir, calls, peer, monkeypatch)
    assert not state.is_notified("item-a")


def test_a_restart_shaped_replay_does_not_renotify(tmp_path, monkeypatch):
    """A fresh SeedStore reports everything currently resident as 'added'
    (it has nothing tracked yet) -- the durable ledger is what stops that
    from turning into a repeat /add for everything on disk."""
    watch_dir = tmp_path / "watch"
    _touch(watch_dir / "item-a.torrent", watch_dir / "item-b.torrent")

    db_path = tmp_path / "state.sqlite3"
    store = FakeStore()
    state = State(db_path)
    peer = {"ip": "10.0.0.1", "bt": 6890, "http": 8090}
    calls = []
    _tick(store, state, watch_dir, calls, peer, monkeypatch)
    assert len(calls) == 2

    # "Restart": a fresh store (nothing tracked) but the same durable ledger.
    fresh_store = FakeStore()
    calls.clear()
    _tick(fresh_store, state, watch_dir, calls, peer, monkeypatch)
    assert calls == []


def test_a_failed_notify_is_retried_next_tick_and_does_not_block_others(tmp_path, monkeypatch):
    watch_dir = tmp_path / "watch"
    _touch(watch_dir / "item-a.torrent", watch_dir / "item-b.torrent")

    store = FakeStore()
    state = State(tmp_path / "state.sqlite3")
    peer = {"ip": "10.0.0.1", "bt": 6890, "http": 8090}

    attempts = []
    calls = []

    def flaky_notify(node, info_hash, p):
        attempts.append(info_hash)
        if info_hash == "item-a" and attempts.count("item-a") == 1:
            raise urllib.error.HTTPError("http://node/add", 500, "boom", None, None)
        calls.append(info_hash)

    monkeypatch.setattr(reconcile, "notify", flaky_notify)
    reconcile.tick(store, state, watch_dir, "10.0.0.5:8001", peer)
    assert calls == ["item-b"]
    assert not state.is_notified("item-a")
    assert state.is_notified("item-b")

    reconcile.tick(store, state, watch_dir, "10.0.0.5:8001", peer)
    assert sorted(calls) == ["item-a", "item-b"]
    assert attempts.count("item-a") == 2
    assert attempts.count("item-b") == 1


def test_an_unreachable_node_ends_the_pass(tmp_path, monkeypatch):
    watch_dir = tmp_path / "watch"
    _touch(*(watch_dir / f"item-{i}.torrent" for i in range(5)))

    store = FakeStore()
    state = State(tmp_path / "state.sqlite3")
    peer = {"ip": "10.0.0.1", "bt": 6890, "http": 8090}
    attempts = []

    def unreachable(node, info_hash, p):
        attempts.append(info_hash)
        raise urllib.error.URLError(ConnectionRefusedError())

    monkeypatch.setattr(reconcile, "notify", unreachable)
    reconcile.tick(store, state, watch_dir, "10.0.0.5:8001", peer)

    assert len(attempts) == 1
    assert not any(state.is_notified(f"item-{i}") for i in range(5))
