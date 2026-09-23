"""The whole algorithm: keep SeedStore in sync with the watched directory,
and tell the target node about whatever it hasn't already been told about.

Checks the ledger against the full currently-resident set on every tick,
not just what sync() reports as newly added -- a failed notify() has to stay
retriable on later ticks even though the item won't appear as "added" again,
and a point lookup per resident item stays cheap regardless of how many
there are. The expensive operation (opening and parsing a .torrent) was
already ruled out in seed.py's sync(); this is not that.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .seed import SeedStore
from .state import State

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL = 10  # seconds; a fixed constant, not deployment config


def scan(watch_dir: Path) -> set[Path]:
    """torrentizer writes .torrent files flat in its output directory, one
    per item, alongside each item's own data directory of the same name."""
    return set(watch_dir.glob("*.torrent"))


def notify(target_node: str, info_hash: str, peer: dict, timeout: float = 10.0) -> None:
    """Tell the target node: this exists, come get it from me. The actual
    transfer (the .torrent over HTTP, the data over BitTorrent) is the node
    pulling from `peer` -- this call carries none of it, just the address."""
    body = json.dumps({"info_hash": info_hash, "peer": peer}).encode()
    request = urllib.request.Request(
        f"http://{target_node}/add", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def tick(store: SeedStore, state: State, watch_dir: Path, target_node: str, peer: dict) -> None:
    _added, dropped = store.sync(scan(watch_dir))

    for info_hash in dropped:
        state.forget(info_hash)

    for info_hash in store.info_hashes():
        if state.is_notified(info_hash):
            continue
        try:
            notify(target_node, info_hash, peer)
        except urllib.error.HTTPError:
            logger.exception("%s rejected %s", target_node, info_hash[:8])
            continue
        except OSError:
            # Unreachable: every remaining item would wait out its own
            # timeout too. Everything unnotified is retried next tick.
            logger.exception("cannot reach %s", target_node)
            break
        state.mark_notified(info_hash, datetime.now(UTC).isoformat())
