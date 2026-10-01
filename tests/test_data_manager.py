"""Manager.tick() against a fake swarm: nodes with the same holdings-stream
semantics as collab-cluster-experiment's (a holding starts "downloading",
cursors, paging, resync, "gone" tombstones), in memory -- no real HTTP."""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from collab_cluster_utils.data_manager import reconcile, swarm
from collab_cluster_utils.data_manager.policy import Rule

from .helpers import stac_metadata, torrent_bytes

LOCAL = "http://127.0.0.1:8001"
PEER_A = "http://10.0.0.6:8001"
PEER_B = "http://10.0.0.7:8001"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PAGE = 2


class FakeNode:
    def __init__(self, key: str):
        self.key = key
        self.epoch = 0
        self.held: dict[str, str] = {}         # info_hash -> name
        self.changes: list[dict] = []
        self.trimmed_before = 0                # like node.py: older cursors get a 409
        self.blobs: dict[str, bytes] = {}
        self.up = True

    def hold(self, info_hash: str, blob: bytes, name: str = "item") -> None:
        self.held[info_hash] = name
        self.blobs[info_hash] = blob
        for state in ("downloading", "complete"):
            self.changes.append({"info_hash": info_hash, "state": state, "name": name})

    def drop(self, info_hash: str) -> None:
        name = self.held.pop(info_hash)
        self.changes.append({"info_hash": info_hash, "state": "gone", "name": name})

    def restart(self) -> None:
        self.epoch += 1
        self.changes = []
        self.trimmed_before = 0

    def trim(self) -> None:
        """Drop everything from the change log, as CHANGE_LOG_LIMIT would."""
        self.trimmed_before = len(self.changes)

    def cursor(self) -> str:
        return f"{self.epoch}:{len(self.changes)}"

    def holdings(self, cursor: str | None) -> dict:
        if cursor is None or cursor.count(":") == 2:
            after = cursor.split(":")[2] if cursor else ""
            keys = sorted(h for h in self.held if h > after)[:PAGE]
            more = len(keys) == PAGE
            return {"cursor": self.cursor() + (f":{keys[-1]}" if more else ""),
                    "more": more,
                    "holdings": [{"info_hash": h, "state": "complete", "name": self.held[h]}
                                 for h in keys]}
        epoch, seq = map(int, cursor.split(":"))
        if epoch != self.epoch or seq < self.trimmed_before:
            raise swarm.Resync(cursor)
        return {"cursor": self.cursor(), "more": False, "holdings": self.changes[seq:]}


class FakeSwarm:
    def __init__(self, monkeypatch):
        self.nodes = {LOCAL: FakeNode("local"), PEER_A: FakeNode("a"), PEER_B: FakeNode("b")}
        self.local, self.a, self.b = self.nodes[LOCAL], self.nodes[PEER_A], self.nodes[PEER_B]
        self.calls: list[tuple] = []
        self.reject_adds = False
        self.now = NOW
        for name in ("peers", "cursor", "holdings", "torrent", "add", "remove"):
            monkeypatch.setattr(swarm, name, getattr(self, name))

    def _node(self, base: str) -> FakeNode:
        node = self.nodes[base]
        if not node.up:
            raise urllib.error.URLError("down")
        return node

    def peers(self, base):
        node = self._node(base)
        return {"self": {"node": node.key},
                "peers": [{"node": n.key, "ip": b.split("//")[1].split(":")[0], "http": 8001}
                          for b, n in self.nodes.items() if n is not node]}

    def cursor(self, base):
        return self._node(base).cursor()

    def holdings(self, base, cursor):
        return self._node(base).holdings(cursor)

    def torrent(self, base, info_hash):
        self.calls.append(("torrent", base, info_hash))
        node = self._node(base)
        if info_hash not in node.held:
            raise urllib.error.HTTPError(base, 404, "not held here", {}, None)
        return node.blobs[info_hash]

    def add(self, base, info_hash):
        assert base == LOCAL
        self.calls.append(("add", info_hash))
        if self.reject_adds:
            raise urllib.error.HTTPError(base, 404, "nope", {}, None)
        source = next(n for n in self.nodes.values() if info_hash in n.held)
        self.local.hold(info_hash, source.blobs[info_hash], source.held[info_hash])

    def remove(self, base, info_hash):
        assert base == LOCAL
        self.calls.append(("remove", info_hash))
        self.local.drop(info_hash)

    def actions(self) -> list[tuple]:
        return [c for c in self.calls if c[0] != "torrent"]

    def fetches(self) -> list[tuple]:
        return [c[1:] for c in self.calls if c[0] == "torrent"]

    def manager(self, *rules: Rule, backfill: bool = False) -> reconcile.Manager:
        return reconcile.Manager(list(rules), LOCAL, self.cursors_path, backfill,
                                 clock=lambda: self.now.timestamp())


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fake = FakeSwarm(monkeypatch)
    fake.cursors_path = tmp_path / "cursors.json"
    return fake


def dataset(name: str, hours_ago: float, **kwargs) -> tuple[str, bytes]:
    sensed = (NOW - timedelta(hours=hours_ago)).isoformat()
    return torrent_bytes(name, stac_metadata(sensed, **kwargs))


# --- datasets arriving elsewhere -----------------------------------------------

def test_takes_what_the_policy_wants_of_what_arrives(fake):
    m = fake.manager(Rule(max_cloud_cover=30))
    m.tick()
    wanted, blob_w = dataset("wanted", 1)
    cloudy, blob_c = dataset("cloudy", 1, cloud_cover=90)
    fake.a.hold(wanted, blob_w)
    fake.a.hold(cloudy, blob_c)
    m.tick()
    assert fake.actions() == [("add", wanted)]
    assert set(fake.local.held) == {wanted}


def test_ignores_what_was_there_before_it_started(fake):
    info_hash, blob = dataset("old", 1)
    fake.a.hold(info_hash, blob)
    m = fake.manager(Rule())
    m.tick()
    m.tick()
    assert fake.calls == []


def test_backfill_judges_what_was_there_before_once(fake):
    info_hash, blob = dataset("old", 1)
    fake.a.hold(info_hash, blob)
    fake.b.hold(info_hash, blob)
    m = fake.manager(Rule(), backfill=True)
    m.tick()
    m.tick()
    assert fake.actions() == [("add", info_hash)]
    assert len(fake.fetches()) == 1


def test_a_rejection_is_remembered_for_a_while(fake):
    m = fake.manager(Rule(collection="sentinel-2-l2a"))
    m.tick()
    info_hash, blob = dataset("landsat", 1, collection="landsat")
    fake.a.hold(info_hash, blob)
    m.tick()
    fake.b.hold(info_hash, blob)
    m.tick()
    assert fake.fetches() == [(PEER_A, info_hash)]
    # Long after, a new copy elsewhere is judged again.
    fake.now += timedelta(seconds=reconcile.REJECTED_TTL + 1)
    m.tick()
    fake.b.drop(info_hash)
    fake.b.hold(info_hash, blob)
    m.tick()
    assert fake.fetches() == [(PEER_A, info_hash), (PEER_B, info_hash)]
    assert fake.actions() == []


def test_does_not_take_what_already_expired(fake):
    m = fake.manager(Rule(retain_hours=48))
    m.tick()
    info_hash, blob = dataset("item", 49)
    fake.a.hold(info_hash, blob)
    m.tick()
    assert fake.actions() == []


def test_a_rejected_add_waits_for_the_next_arrival(fake):
    m = fake.manager(Rule())
    m.tick()
    info_hash, blob = dataset("item", 1)
    fake.reject_adds = True
    fake.a.hold(info_hash, blob)
    m.tick()
    fake.reject_adds = False
    m.tick()
    assert fake.actions() == [("add", info_hash)]
    fake.b.hold(info_hash, blob)
    m.tick()
    assert fake.actions() == [("add", info_hash), ("add", info_hash)]
    assert info_hash in fake.local.held


def test_a_dataset_gone_before_it_is_judged_is_dropped(fake):
    m = fake.manager(Rule())
    m.tick()
    info_hash, blob = dataset("item", 1)
    fake.a.hold(info_hash, blob)
    fake.a.drop(info_hash)
    m.tick()
    m.tick()
    assert fake.fetches() == [(PEER_A, info_hash)]
    assert fake.actions() == []


def test_a_peer_restart_is_followed_from_then_on(fake, caplog):
    m = fake.manager(Rule())
    m.tick()
    fake.a.restart()                                   # its holdings re-appear...
    old, blob_o = dataset("old", 1)
    fake.a.hold(old, blob_o)                           # ...as new rows
    m.tick()                                           # 409: start from now
    assert "can't answer from our cursor" in caplog.text
    new, blob_n = dataset("new", 1)
    fake.a.hold(new, blob_n)
    m.tick()
    assert fake.actions() == [("add", new)]


def test_falling_behind_a_peers_change_log_is_logged(fake, caplog):
    m = fake.manager(Rule())
    m.tick()
    fake.a.up = False
    m.tick()
    missed, blob_m = dataset("missed", 1)
    fake.a.hold(missed, blob_m)
    fake.a.trim()                                      # more than its log holds
    fake.a.up = True
    m.tick()
    assert "can't answer from our cursor" in caplog.text
    new, blob_n = dataset("new", 1)
    fake.a.hold(new, blob_n)
    m.tick()
    assert fake.actions() == [("add", new)]


def test_an_unreachable_peer_is_skipped(fake):
    m = fake.manager(Rule())
    m.tick()
    fake.a.up = False
    info_hash, blob = dataset("item", 1)
    fake.b.hold(info_hash, blob)
    m.tick()
    assert fake.actions() == [("add", info_hash)]


def test_a_backlog_is_worked_through_over_several_ticks(fake, monkeypatch):
    m = fake.manager(Rule())
    m.tick()
    info_hash, blob = dataset("item", 1)
    fake.a.hold(info_hash, blob)
    monkeypatch.setattr(reconcile, "WORK_BUDGET", -1)  # spent before it starts
    m.tick()
    assert fake.calls == [] and info_hash in m.pending
    monkeypatch.setattr(reconcile, "WORK_BUDGET", 5)
    m.tick()
    assert fake.actions() == [("add", info_hash)]


# --- the local node --------------------------------------------------------------

def test_judges_what_the_node_holds_from_the_node_itself(fake):
    info_hash, blob = dataset("item", 1)
    fake.local.hold(info_hash, blob)
    fake.a.hold(info_hash, blob)
    m = fake.manager(Rule())
    m.tick()
    m.tick()
    assert fake.fetches() == [(LOCAL, info_hash)]


def test_rolling_archive_removes_what_expired(fake):
    info_hash, blob = dataset("item", 47)
    fake.local.hold(info_hash, blob)
    m = fake.manager(Rule(retain_hours=48))
    m.tick()
    assert fake.actions() == []
    fake.now += timedelta(hours=1)
    m.tick()
    assert fake.actions() == [("remove", info_hash)]
    assert not fake.local.held
    # ...and a copy that arrives elsewhere afterwards isn't even fetched.
    fake.a.hold(info_hash, blob)
    m.tick()
    assert fake.fetches() == [(LOCAL, info_hash)]


def test_expires_what_it_added(fake):
    m = fake.manager(Rule(retain_hours=48))
    m.tick()
    info_hash, blob = dataset("item", 47)
    fake.a.hold(info_hash, blob)
    m.tick()
    fake.now += timedelta(hours=1)
    m.tick()
    assert fake.actions() == [("add", info_hash), ("remove", info_hash)]
    assert fake.fetches() == [(PEER_A, info_hash)]     # judged once, when it arrived


def test_applies_to_everything_held_but_leaves_what_it_cant_judge(fake):
    unwanted, blob_u = dataset("unwanted", 1, collection="landsat")
    manual, blob_m = torrent_bytes("manual")          # no metadata
    fake.local.hold(unwanted, blob_u)
    fake.local.hold(manual, blob_m)
    m = fake.manager(Rule(collection="sentinel-2-l2a"))
    m.tick()
    m.tick()
    assert fake.actions() == [("remove", unwanted)]
    assert set(fake.local.held) == {manual}


def test_judges_what_arrives_locally_by_other_means(fake):
    m = fake.manager(Rule(retain_hours=48))
    m.tick()
    info_hash, blob = dataset("item", 49)
    fake.local.hold(info_hash, blob)                  # e.g. from the publisher
    m.tick()
    assert fake.actions() == [("remove", info_hash)]


def test_a_manual_removal_stands(fake):
    info_hash, blob = dataset("item", 1)
    fake.local.hold(info_hash, blob)
    fake.a.hold(info_hash, blob)
    m = fake.manager(Rule())
    m.tick()
    fake.local.drop(info_hash)
    m.tick()
    m.tick()
    assert fake.actions() == []


def test_a_local_restart_is_followed_by_a_full_listing(fake):
    kept, blob_k = dataset("kept", 1)
    dropped, blob_d = dataset("dropped", 1)
    fake.local.hold(kept, blob_k)
    fake.local.hold(dropped, blob_d)
    m = fake.manager(Rule())
    m.tick()
    fake.local.restart()
    del fake.local.held[dropped]                      # gone without a tombstone
    m.tick()
    assert set(m.held) == set(m.until) == {kept}
    assert len(fake.fetches()) == 2                   # nothing judged twice


def test_the_local_listing_is_paged(fake):
    hashes = set()
    for i in range(2 * PAGE + 1):
        info_hash, blob = dataset(f"d{i}", 1)
        fake.local.held[info_hash] = f"d{i}"
        fake.local.blobs[info_hash] = blob
        hashes.add(info_hash)
    m = fake.manager(Rule())
    m.tick()
    assert set(m.until) == hashes


def test_an_unreachable_local_node_ends_the_tick(fake):
    fake.local.up = False
    with pytest.raises(OSError):
        fake.manager(Rule()).tick()


# --- picking up where it left off ------------------------------------------------

def test_a_restart_judges_what_arrived_while_it_was_down(fake):
    old, blob_o = dataset("old", 1)
    fake.a.hold(old, blob_o)
    fake.manager(Rule()).tick()
    missed, blob_m = dataset("missed", 1)
    fake.a.hold(missed, blob_m)                        # while it's down
    fake.manager(Rule()).tick()
    assert fake.actions() == [("add", missed)]


def test_a_cursor_that_cant_be_answered_is_followed_from_now(fake, caplog):
    fake.manager(Rule()).tick()
    fake.a.restart()
    missed, blob_m = dataset("missed", 1)
    fake.a.hold(missed, blob_m)
    m = fake.manager(Rule())
    m.tick()
    assert "can't answer from our cursor" in caplog.text
    new, blob_n = dataset("new", 1)
    fake.a.hold(new, blob_n)
    m.tick()
    assert fake.actions() == [("add", new)]


def test_cursors_are_not_saved_past_what_is_still_pending(fake, monkeypatch):
    m = fake.manager(Rule())
    m.tick()
    saved = fake.cursors_path.read_text()
    info_hash, blob = dataset("item", 1)
    fake.a.hold(info_hash, blob)
    monkeypatch.setattr(reconcile, "WORK_BUDGET", -1)
    m.tick()
    assert info_hash in m.pending
    assert fake.cursors_path.read_text() == saved
    monkeypatch.setattr(reconcile, "WORK_BUDGET", 5)
    fake.manager(Rule()).tick()                        # crashed and restarted
    assert fake.actions() == [("add", info_hash)]


def test_backfill_ignores_where_it_left_off(fake):
    fake.manager(Rule()).tick()
    old, blob_o = dataset("old", 1)
    fake.a.hold(old, blob_o)
    fake.a.changes.clear()                             # gone from its change log
    fake.manager(Rule(), backfill=True).tick()
    assert fake.actions() == [("add", old)]


def test_a_peer_out_of_sight_for_a_while_is_resumed(fake):
    m = fake.manager(Rule())
    m.tick()
    del fake.nodes[PEER_B]
    m.tick()
    info_hash, blob = dataset("item", 1)
    fake.b.hold(info_hash, blob)
    fake.nodes[PEER_B] = fake.b
    m.tick()
    assert fake.actions() == [("add", info_hash)]


def test_unreadable_cursors_are_ignored(fake):
    fake.cursors_path.write_text("not json")
    info_hash, blob = dataset("old", 1)
    fake.a.hold(info_hash, blob)
    m = fake.manager(Rule())
    m.tick()
    assert fake.actions() == []
    assert set(json.loads(fake.cursors_path.read_text())) == {"a", "b"}
