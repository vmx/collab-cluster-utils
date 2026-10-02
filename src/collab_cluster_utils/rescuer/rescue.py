"""Fill a node's spare space with the swarm's rarest datasets, best effort.

Every round the collector's index (its /api/rescue) hands over a random sample
of the rarest datasets this node doesn't hold, and this node's holdings with
the most copies. A dataset with FLOOR copies has enough: it is never copied
again, and never let go of again. One with fewer is taken if it fits in the
budget; when the budget is full, in exchange for a holding that keeps FLOOR
complete copies without this one. So rescue nodes work towards exactly two
copies of everything, and leave space empty rather than make a third.

Nothing is kept between rounds: the local node says what it holds, the index
what everybody else does. Several rescue nodes need no coordination: they're
handed different random samples, and a dataset two of them happen to take
at the same moment ends up with a copy to spare -- the first thing either
lets go of.

Copies on rolling archives count like any other. A dataset whose spare copies
were there dips below FLOOR when they expire; it is then among the rarest, and
gets rescued again.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .. import swarm

logger = logging.getLogger(__name__)

FLOOR = 2           # copied up to, and never let go of below
MAX_DOWNLOADS = 4   # rescues in flight at once
SAMPLE = 50         # candidates (and evictable holdings) asked for per round


@dataclass(frozen=True)
class Plan:
    remove: list[str]
    add: list[str]


def plan(key: str, candidates: list[dict], evictable: list[dict],
         used: int, budget: int, downloading: int) -> Plan:
    """What to let go of and what to take this round. Datasets are the
    index's rows: {"info_hash", "total_size", "complete": [node keys],
    "partial": [node keys], ...}."""
    # Only complete copies of ours whose dataset keeps FLOOR without them.
    pool = sorted((e for e in evictable
                   if key in e["complete"] and len(e["complete"]) > FLOOR),
                  key=lambda e: -len(e["complete"]))
    remove, add = [], []
    while used > budget and pool:          # the budget shrank
        e = pool.pop(0)
        remove.append(e["info_hash"])
        used -= e["total_size"]
    slots = MAX_DOWNLOADS - downloading
    for cand in candidates:
        if slots <= 0:
            break
        if (not cand["complete"] or _copies(cand) >= FLOOR
                or key in cand["complete"] or key in cand["partial"]):
            continue
        size, freed, evict = cand["total_size"], 0, []
        for e in pool:                      # most copies first
            if used + size - freed <= budget:
                break
            evict.append(e)
            freed += e["total_size"]
        if used + size - freed > budget:
            continue                        # a smaller one may still fit
        for e in evict:
            pool.remove(e)
            remove.append(e["info_hash"])
        add.append(cand["info_hash"])
        used += size - freed
        slots -= 1
    return Plan(remove, add)


def _copies(ds: dict) -> int:
    """Copies there are or soon will be: a download under way counts."""
    return len(ds["complete"]) + len(ds["partial"])


def offer(collector: str, key: str) -> dict:
    """The index's {"candidates", "evictable"} for this node."""
    query = urllib.parse.urlencode({"node": key, "limit": SAMPLE})
    with urllib.request.urlopen(f"{collector}/api/rescue?{query}",
                                timeout=swarm.TIMEOUT) as response:
        return json.loads(response.read())


class Rescuer:
    def __init__(self, local_base: str, collector: str, budget: int):
        self.local_base = local_base
        self.collector = collector
        self.budget = budget
        self.key: str | None = None
        self.cursor: str | None = None
        self.held: dict[str, tuple[str, int]] = {}   # info_hash -> (state, total_size)

    def tick(self) -> None:
        """One round. The local node unreachable: raises. The collector
        unreachable: logged, and the round skipped."""
        if self.key is None:
            self.key = swarm.node_key(self.local_base)
        self._follow_local()
        try:
            got = offer(self.collector, self.key)
        except OSError as exc:
            logger.warning("cannot ask %s (%s); skipping this round", self.collector, exc)
            return
        used = sum(size for _, size in self.held.values())
        downloading = sum(1 for state, _ in self.held.values() if state == "downloading")
        todo = plan(self.key, got["candidates"], got["evictable"],
                    used, self.budget, downloading)
        names = {d["info_hash"]: d["name"] for d in got["candidates"] + got["evictable"]}
        for info_hash in todo.remove:
            try:
                swarm.remove(self.local_base, info_hash)
            except urllib.error.HTTPError as exc:
                logger.error("node rejected removing %s: HTTP %d %s; taking nothing "
                             "this round", info_hash[:16], exc.code, swarm.error_body(exc))
                return                      # its space isn't free
            self.held.pop(info_hash, None)
            logger.info("let go of %s [%s]", names[info_hash], info_hash[:16])
        for info_hash in todo.add:
            try:
                swarm.add(self.local_base, info_hash)
            except urllib.error.HTTPError as exc:
                logger.error("node rejected adding %s: HTTP %d %s",
                             info_hash[:16], exc.code, swarm.error_body(exc))
                continue
            logger.info("rescuing %s [%s]", names[info_hash], info_hash[:16])
        if used > self.budget and not todo.remove:
            logger.warning("over budget (%d of %d bytes), but nothing held has "
                           "copies to spare", used, self.budget)

    def _follow_local(self) -> None:
        """Keep `held` in step with the local node: deltas by cursor, a full
        listing at first and whenever the node can't answer from it."""
        if self.cursor is not None:
            try:
                page = swarm.holdings(self.local_base, self.cursor)
            except swarm.Resync:
                self.cursor = None
            else:
                for row in page["holdings"]:
                    self._apply(row)
                self.cursor = page["cursor"]
                return
        self.held, cursor = {}, None
        while True:
            page = swarm.holdings(self.local_base, cursor)
            for row in page["holdings"]:
                self._apply(row)
            cursor = page["cursor"]
            if not page["more"]:
                break
        self.cursor = cursor

    def _apply(self, row: dict) -> None:
        if row["state"] == "gone":
            self.held.pop(row["info_hash"], None)
        else:
            self.held[row["info_hash"]] = (row["state"], int(row.get("total_size") or 0))
