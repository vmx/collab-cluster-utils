"""The rescuer: plan() on its own, and Rescuer.tick() against a fake local
node and a fake index."""

from __future__ import annotations

import urllib.error

import pytest

from collab_cluster_utils import swarm
from collab_cluster_utils.rescuer import rescue
from collab_cluster_utils.rescuer.rescue import Plan, Rescuer, plan

ME = "me"
LOCAL = "http://127.0.0.1:8001"


def ds(name: str, complete: list[str], partial: list[str] = (), size: int = 10) -> dict:
    return {"info_hash": name, "name": name, "total_size": size,
            "complete": list(complete), "partial": list(partial)}


def test_takes_what_fits():
    candidates = [ds("a", ["x"]), ds("b", ["x"]), ds("c", ["x"])]
    assert plan(ME, candidates, [], used=0, budget=20, downloading=0) == Plan([], ["a", "b"])


def test_never_copies_a_dataset_with_two_copies():
    candidates = [ds("two", ["x", "y"]), ds("coming", ["x"], ["y"])]
    assert plan(ME, candidates, [], used=0, budget=100, downloading=0) == Plan([], [])


def test_skips_what_has_no_complete_copy_or_is_ours():
    candidates = [ds("none", [], ["x"]), ds("mine", ["x", ME]), ds("coming", ["x"], [ME])]
    assert plan(ME, candidates, [], used=0, budget=100, downloading=0).add == []


def test_limits_downloads_in_flight():
    candidates = [ds(str(i), ["x"]) for i in range(10)]
    got = plan(ME, candidates, [], used=0, budget=1000, downloading=rescue.MAX_DOWNLOADS - 1)
    assert len(got.add) == 1


def test_swaps_a_holding_with_copies_to_spare():
    evictable = [ds("plenty", [ME, "x", "y"])]
    got = plan(ME, [ds("rare", ["x"])], evictable, used=10, budget=10, downloading=0)
    assert got == Plan(["plenty"], ["rare"])


def test_never_takes_a_holding_below_the_floor():
    evictable = [ds("two", [ME, "x"])]
    assert plan(ME, [ds("rare", ["x"])], evictable, used=10, budget=10,
                downloading=0) == Plan([], [])


def test_does_not_swap_for_a_dataset_being_rescued_elsewhere():
    evictable = [ds("three", [ME, "x", "y"])]
    candidates = [ds("being-rescued", ["x"], ["y"])]
    assert plan(ME, candidates, evictable, used=10, budget=10, downloading=0) == Plan([], [])


def test_evicts_several_to_fit_a_big_one():
    evictable = [ds("p", [ME, "x", "y"], size=10), ds("q", [ME, "x", "y"], size=10)]
    got = plan(ME, [ds("big", ["x"], size=20)], evictable, used=20, budget=20, downloading=0)
    assert got == Plan(["p", "q"], ["big"])


def test_evicts_nothing_when_it_would_not_fit_anyway():
    evictable = [ds("p", [ME, "x", "y"], size=10), ds("floor", [ME, "x"], size=10)]
    got = plan(ME, [ds("big", ["x"], size=20)], evictable, used=20, budget=20, downloading=0)
    assert got == Plan([], [])


def test_shrunk_budget_lets_go_of_spare_copies_only():
    evictable = [ds("spare", [ME, "x", "y"]), ds("floor", [ME, "x"])]
    assert plan(ME, [], evictable, used=20, budget=5, downloading=0) == Plan(["spare"], [])


def test_partial_copy_of_ours_is_not_evicted():
    evictable = [ds("coming", ["x", "y", "z"], [ME])]
    assert plan(ME, [ds("rare", ["x"])], evictable, used=10, budget=10,
                downloading=0) == Plan([], [])


# --- tick ---------------------------------------------------------------------

class FakeNode:
    def __init__(self, monkeypatch):
        self.rows: list[dict] = []
        self.offer = {"candidates": [], "evictable": []}
        self.collector_up = True
        self.reject_remove = False
        self.calls: list[tuple] = []
        monkeypatch.setattr(swarm, "node_key", lambda base: ME)
        monkeypatch.setattr(swarm, "holdings", self.holdings)
        monkeypatch.setattr(swarm, "add", self.add)
        monkeypatch.setattr(swarm, "remove", self.remove)
        monkeypatch.setattr(rescue, "offer", self.get_offer)

    def holdings(self, base, cursor):
        seq = int(cursor or 0)
        return {"cursor": str(len(self.rows)), "more": False, "holdings": self.rows[seq:]}

    def hold(self, info_hash, size=10, state="complete"):
        self.rows.append({"info_hash": info_hash, "state": state, "total_size": size})

    def add(self, base, info_hash):
        self.calls.append(("add", info_hash))
        self.hold(info_hash, state="downloading")

    def remove(self, base, info_hash):
        self.calls.append(("remove", info_hash))
        if self.reject_remove:
            raise urllib.error.HTTPError(base, 500, "nope", {}, None)
        self.rows.append({"info_hash": info_hash, "state": "gone", "total_size": 10})

    def get_offer(self, collector, key):
        if not self.collector_up:
            raise urllib.error.URLError("down")
        return self.offer


@pytest.fixture
def node(monkeypatch):
    return FakeNode(monkeypatch)


def test_tick_takes_and_counts_local_holdings(node):
    node.hold("old", size=10)
    node.offer["candidates"] = [ds("a", ["x"]), ds("b", ["x"])]
    rescuer = Rescuer(LOCAL, "http://collector:8100", budget=20)
    rescuer.tick()
    assert node.calls == [("add", "a")]
    node.offer["candidates"] = [ds("b", ["x"])]
    rescuer.tick()                          # "a" downloading fills the budget
    assert rescuer.held["a"] == ("downloading", 10)
    assert node.calls == [("add", "a")]


def test_tick_swaps(node):
    node.hold("plenty")
    node.offer = {"candidates": [ds("rare", ["x"])],
                  "evictable": [ds("plenty", [ME, "x", "y"])]}
    Rescuer(LOCAL, "http://collector:8100", budget=10).tick()
    assert node.calls == [("remove", "plenty"), ("add", "rare")]


def test_tick_takes_nothing_when_a_removal_fails(node):
    node.hold("plenty")
    node.reject_remove = True
    node.offer = {"candidates": [ds("rare", ["x"])],
                  "evictable": [ds("plenty", [ME, "x", "y"])]}
    Rescuer(LOCAL, "http://collector:8100", budget=10).tick()
    assert node.calls == [("remove", "plenty")]


def test_tick_skips_the_round_without_a_collector(node):
    node.collector_up = False
    node.offer["candidates"] = [ds("a", ["x"])]
    Rescuer(LOCAL, "http://collector:8100", budget=20).tick()
    assert node.calls == []


def test_tick_follows_local_changes(node):
    node.hold("a")
    rescuer = Rescuer(LOCAL, "http://collector:8100", budget=100)
    rescuer.tick()
    node.rows.append({"info_hash": "a", "state": "gone", "total_size": 10})
    node.hold("b", size=30)
    rescuer.tick()
    assert rescuer.held == {"b": ("complete", 30)}
