"""The whole algorithm: judge what the local node holds and remove what the
policy no longer wants, and judge every dataset another node takes from now
on and add what the policy wants.

Nothing is kept about the swarm as a whole. The node is the authority on
what it holds, and a dataset's .torrent on what it is, so all that's kept is:

- for each dataset the local node holds, until when to hold it -- read
  once from its .torrent, fetched from the local node itself;
- one cursor per peer, to see what it takes from now on: every holding
  starts as a "downloading" row in its node's stream, so each such row is
  a dataset arriving there;
- the datasets judged not wanted, for a while, so the same dataset arriving
  at further nodes isn't fetched and judged again.

Only the cursors are persisted, so that after a restart each peer's stream
hands over exactly what arrived while the manager was down. They're saved
only when nothing is left pending, so a saved cursor always means
"everything before here has been judged". A peer that can't answer from its
cursor -- it restarted, or the gap is longer than its change log; there's
no telling which -- is followed from now on, and that gap is lost but logged.
Backfill, which judges everything each peer already holds when first seen,
is what covers it.

A restart costs one local fetch per dataset the local node holds.

The policy applies to everything the local node holds, whoever added it --
except datasets without metadata to judge by, which are left alone.

Removing is reliable; taking is best effort -- a wanted dataset can be
missed for good. The README's "Limitations" lists when, and what a
production version would need instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..bencode import entries
from .. import swarm
from .policy import Rule, hold_until, read_metadata

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL = 10       # seconds; fixed constants, not deployment config
WORK_BUDGET = 5               # seconds per tick for each kind of .torrent fetching
REJECTED_TTL = 48 * 3600      # how long a "not wanted" is remembered


@dataclass
class Peer:
    base: str
    cursor: str | None = None


class Manager:
    def __init__(self, rules: list[Rule], local_base: str, cursors_path: Path,
                 backfill: bool = False, clock: Callable[[], float] = time.time):
        self.rules = rules
        self.local_base = local_base
        self.backfill = backfill
        self.clock = clock
        self.local_cursor: str | None = None
        self.held: dict[str, str] = {}             # held locally: info_hash -> name
        # info_hash -> until when to hold it, for what's held locally and has
        # been judged; None for no metadata to judge by.
        self.until: dict[str, float | None] = {}
        self.peers: dict[str, Peer] = {}           # node key -> peer
        # Cursors of peers not (or no longer) being followed: from before a
        # restart, or of a peer that dropped out of sight, to resume from.
        self.cursors_path = cursors_path
        self.written = load_cursors(cursors_path)
        # A backfill judges everything anyway, so where we left off is moot.
        self.saved: dict[str, str] = {} if backfill else dict(self.written)
        self.pending: dict[str, tuple[str, str]] = {}  # info_hash -> (base, name) to judge
        self.rejected: dict[str, float] = {}       # info_hash -> when; oldest first

    def tick(self) -> None:
        self._follow_local()
        self._follow_peers()
        self._judge_held()
        self._remove_expired()
        self._judge_arrivals()
        self._forget_old_rejections()
        if not self.pending:
            self._save_cursors()

    # --- the local node ------------------------------------------------------

    def _follow_local(self) -> None:
        """Keep `held` in step with the local node. Unreachable: raises, and
        the tick ends there -- there's nothing to manage without it."""
        if self.local_cursor is not None:
            try:
                page = swarm.holdings(self.local_base, self.local_cursor)
            except swarm.Resync:
                self.local_cursor = None
            else:
                for row in page["holdings"]:
                    if row["state"] == "gone":
                        self.held.pop(row["info_hash"], None)
                        self.until.pop(row["info_hash"], None)
                    else:
                        self.held[row["info_hash"]] = row["name"]
                self.local_cursor = page["cursor"]
                return
        listed, self.local_cursor = self._list(self.local_base)
        self.held = {row["info_hash"]: row["name"] for row in listed}
        self.until = {h: u for h, u in self.until.items() if h in self.held}

    def _judge_held(self) -> None:
        unjudged = [h for h in self.held if h not in self.until]
        for info_hash in self._within_budget(unjudged):
            try:
                blob = swarm.torrent(self.local_base, info_hash)
            except OSError:
                continue          # removed meanwhile, or a hiccup: next tick
            if self._matches(blob, info_hash, self.local_base):
                self.until[info_hash] = self._decide(blob)

    def _remove_expired(self) -> None:
        now = self.clock()
        for info_hash, until in list(self.until.items()):
            if until is None or now < until:
                continue          # no metadata to judge by, or still wanted
            try:
                swarm.remove(self.local_base, info_hash)
            except urllib.error.HTTPError as exc:
                logger.error("node rejected removing %s: HTTP %d %s",
                             info_hash[:16], exc.code, swarm.error_body(exc))
                continue
            name = self.held.pop(info_hash, "?")
            del self.until[info_hash]
            self.rejected[info_hash] = now
            logger.info("removed %s [%s]", name, info_hash[:16])

    # --- datasets arriving elsewhere ------------------------------------------

    def _follow_peers(self) -> None:
        view = swarm.peers(self.local_base)
        bases = {peer["node"]: f"http://{peer['ip']}:{peer['http']}"
                 for peer in view["peers"] if peer.get("ip") and peer.get("http")}
        for key in set(self.peers) - set(bases):
            gone = self.peers.pop(key)
            if gone.cursor is not None:
                self.saved[key] = gone.cursor
        for key, base in bases.items():
            peer = self.peers.get(key)
            first_contact = peer is None
            if first_contact:
                peer = self.peers[key] = Peer(base, self.saved.pop(key, None))
            peer.base = base
            try:
                self._follow_peer(peer, first_contact)
            except swarm.Resync:
                # A backfill listing that moved on under us: start it over.
                del self.peers[key]
            except OSError:
                logger.warning("cannot read holdings of %s; trying again next tick", base)

    def _follow_peer(self, peer: Peer, first_contact: bool) -> None:
        if first_contact and self.backfill and peer.cursor is None:
            listed, peer.cursor = self._list(peer.base)
            for row in listed:
                self._arrived(peer, row)
            return
        if peer.cursor is None:
            peer.cursor = swarm.cursor(peer.base)
            return
        try:
            page = swarm.holdings(peer.base, peer.cursor)
        except swarm.Resync:
            # It restarted, or we fell further behind than its change log
            # reaches. Either way there may be arrivals we'll never see --
            # the cursor is opaque, so we can't tell how many -- and all we
            # can do is say so and follow it from now on.
            logger.warning("%s can't answer from our cursor (it restarted, or we "
                           "fell behind its change log): datasets that arrived "
                           "there in between are not judged", peer.base)
            peer.cursor = swarm.cursor(peer.base)
            return
        for row in page["holdings"]:
            if row["state"] == "downloading":      # a holding's first row
                self._arrived(peer, row)
        peer.cursor = page["cursor"]

    def _arrived(self, peer: Peer, row: dict) -> None:
        info_hash = row["info_hash"]
        if (info_hash not in self.held and info_hash not in self.rejected
                and info_hash not in self.pending):
            self.pending[info_hash] = (peer.base, row["name"])

    def _judge_arrivals(self) -> None:
        for info_hash in self._within_budget(list(self.pending)):
            base, name = self.pending[info_hash]
            if info_hash in self.held:
                del self.pending[info_hash]          # got here meanwhile
                continue
            try:
                blob = swarm.torrent(base, info_hash)
            except urllib.error.HTTPError:
                # Not held there any more. If it shows up elsewhere, that's
                # a new arrival.
                del self.pending[info_hash]
                continue
            except OSError:
                continue          # unreachable: try again next tick
            del self.pending[info_hash]
            if not self._matches(blob, info_hash, base):
                continue
            until = self._decide(blob)
            now = self.clock()
            if until is None or now >= until:
                self.rejected[info_hash] = now
                continue
            try:
                swarm.add(self.local_base, info_hash)
            except urllib.error.HTTPError as exc:
                logger.error("node rejected adding %s: HTTP %d %s",
                             info_hash[:16], exc.code, swarm.error_body(exc))
                continue
            self.held[info_hash] = name
            self.until[info_hash] = until
            logger.info("added %s [%s]", name, info_hash[:16])

    def _forget_old_rejections(self) -> None:
        cutoff = self.clock() - REJECTED_TTL
        while self.rejected:
            info_hash, when = next(iter(self.rejected.items()))
            if when > cutoff:
                break
            del self.rejected[info_hash]

    def _save_cursors(self) -> None:
        cursors = {**self.saved, **{key: peer.cursor for key, peer in self.peers.items()
                                    if peer.cursor is not None}}
        if cursors == self.written:
            return
        try:
            save_cursors(self.cursors_path, cursors)
        except OSError:
            logger.exception("cannot save cursors to %s", self.cursors_path)
            return
        self.written = cursors

    # --- helpers ---------------------------------------------------------------

    @staticmethod
    def _list(base: str) -> tuple[list[dict], str]:
        """Everything a node holds, paged to the end, and the cursor to follow
        it from afterwards."""
        rows, cursor = [], None
        while True:
            page = swarm.holdings(base, cursor)
            rows += page["holdings"]
            cursor = page["cursor"]
            if not page["more"]:
                return rows, cursor

    @staticmethod
    def _within_budget(items: Iterable[str]) -> Iterator[str]:
        """Stop handing out work once this tick's budget for it is spent; the
        rest waits for the next tick, so a backlog never stalls the others."""
        deadline = time.monotonic() + WORK_BUDGET
        for item in items:
            if time.monotonic() > deadline:
                return
            yield item

    @staticmethod
    def _matches(blob: bytes, info_hash: str, base: str) -> bool:
        try:
            if hashlib.sha256(entries(blob)[b"info"]).hexdigest() == info_hash:
                return True
        except (KeyError, ValueError):
            pass
        logger.warning("%s sent something else for %s", base, info_hash[:16])
        return False

    def _decide(self, blob: bytes) -> float | None:
        """Until when to hold it; None if it has no metadata to judge by."""
        meta = read_metadata(blob)
        return None if meta is None else hold_until(self.rules, meta)


def load_cursors(path: Path) -> dict[str, str]:
    try:
        cursors = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.exception("ignoring unreadable cursors in %s", path)
        return {}
    return {k: v for k, v in cursors.items() if isinstance(v, str)}


def save_cursors(path: Path, cursors: dict[str, str]) -> None:
    """Temp file + rename, so a crash mid-write leaves the previous version."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(cursors, indent=1, sort_keys=True))
    tmp.replace(path)
