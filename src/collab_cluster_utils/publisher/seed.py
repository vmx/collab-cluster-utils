"""A minimal, seed-only libtorrent session: no beacon, no discovery, no HTTP
API of its own beyond what http_server.py answers from SeedStore directly.

It is a bare BitTorrent peer, not a collab-cluster node: it never announces
itself and is never a swarm member; the only way anything ever learns of it
is being told its address directly (see reconcile.py). Its only job is to
hold whatever items currently sit in the watched directory, ready to be
dialled for, and to stop holding one the moment it disappears from disk.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import libtorrent as lt

logger = logging.getLogger(__name__)


def make_session(bind_host: str, bt_port: int) -> "lt.session":
    return lt.session({
        "listen_interfaces": f"{bind_host}:{bt_port}",
        # Every discovery mechanism off, same as a real node: the only peer
        # this session ever talks to is the one address reconcile.py hands
        # a node in its /add call.
        "enable_dht": False,
        "enable_lsd": False,
        "enable_upnp": False,
        "enable_natpmp": False,
        # Without this, libtorrent's default queue (5) pauses anything past
        # the first few items, and a paused torrent refuses peers -- with
        # thousands of items resident at once, most of them would otherwise
        # be unservable. unchoke_slots_limit is left at its default: that
        # only matters with several peers competing for slots on one
        # torrent, and there is only ever one peer here, the target node.
        "active_seeds": -1,
        "alert_mask": lt.alert.category_t.storage_notification,
    })


def _serve_save_path(ti, item_dir: Path) -> str:
    """Where libtorrent must look to find `item_dir`'s data already on disk.

    libtorrent collapses a directory holding exactly one file into a
    single-file torrent named after that file, dropping the directory level
    -- for those, the save path is the directory itself rather than its
    parent. Derived from the torrent actually built rather than assumed.
    """
    item_dir = item_dir.resolve()
    if item_dir.is_dir() and ti.name() != item_dir.name:
        return str(item_dir)
    return str(item_dir.parent)


class SeedStore:
    """Everything currently servable, and the one place that knows it.

    `items` is read by http_server.py (to answer GET /dataset/<hash>.torrent)
    and by reconcile.py (to know which info-hashes to notify a node about),
    and written only here, under `lock`.
    """

    def __init__(self, bind_host: str, bt_port: int):
        self.session = make_session(bind_host, bt_port)
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}   # info_hash -> {blob, handle, name, path}

    def sync(self, torrent_paths: set[Path]) -> tuple[set[str], set[str]]:
        """Reconcile against what's on disk right now. Returns (added,
        dropped) info-hashes.

        Only opens and parses a path the first time it's seen, and detects a
        removal by comparing paths rather than re-reading anything -- with
        thousands of items resident and tens of thousands arriving a day,
        cost here has to stay proportional to what changed since the last
        call, not to everything ever held.
        """
        with self.lock:
            tracked = {entry["path"]: info_hash for info_hash, entry in self.items.items()}

            dropped = set()
            for path, info_hash in tracked.items():
                if path not in torrent_paths:
                    self.session.remove_torrent(self.items.pop(info_hash)["handle"])
                    dropped.add(info_hash)

            added = set()
            for path in torrent_paths - set(tracked):
                try:
                    blob = path.read_bytes()
                    ti = lt.torrent_info(lt.bdecode(blob))
                except Exception:
                    # Left untracked, so it's retried next call rather than
                    # taking every other item down with it.
                    logger.exception("skipping unreadable torrent %s", path)
                    continue
                info_hash = str(ti.info_hashes().v2)
                if info_hash in self.items:
                    logger.warning("skipping %s: same dataset as %s",
                                   path, self.items[info_hash]["path"])
                    continue
                atp = lt.add_torrent_params()
                atp.ti = ti
                atp.save_path = _serve_save_path(ti, path.with_suffix(""))
                # The data was just hashed by whoever built the torrent;
                # without seed_mode libtorrent re-reads and re-hashes all of
                # it before serving anything. In seed_mode it checks a piece
                # the first time a peer asks for it instead.
                atp.flags |= lt.torrent_flags.seed_mode
                handle = self.session.add_torrent(atp)
                self.items[info_hash] = {"blob": blob, "handle": handle,
                                          "name": ti.name(), "path": path}
                added.add(info_hash)

            return added, dropped

    def torrent_bytes(self, info_hash: str) -> bytes | None:
        with self.lock:
            entry = self.items.get(info_hash)
        return entry["blob"] if entry else None

    def info_hashes(self) -> list[str]:
        with self.lock:
            return list(self.items)
