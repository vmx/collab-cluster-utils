"""The whole algorithm: find .torrent files the ledger hasn't seen, and tell
the target node where to fetch each one and its data from.

Only new files are opened; everything else is a directory listing compared
against the ledger, so a tick costs what changed, not what's on disk.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from .state import State
from .torrent import read_torrent

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL = 10  # seconds; a fixed constant, not deployment config


def notify(target_node: str, info_hash: str, torrent_url: str, web_seed: str,
           timeout: float = 10.0) -> None:
    """Tell the target node: this exists, fetch it from here. The node pulls
    the .torrent from `torrent_url` and the data from `web_seed`."""
    body = json.dumps({"info_hash": info_hash, "torrent_url": torrent_url,
                       "web_seed": web_seed}).encode()
    request = urllib.request.Request(
        f"http://{target_node}/add", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode(errors="replace").strip()
    except OSError:
        return exc.reason


def web_seed_url(base_url: str, item_id: str, single_file: bool) -> str:
    """BEP 19: a URL ending in "/" gets a single file's name appended, or a
    directory torrent's name and file paths. torrentizer keeps a single file
    inside its item directory, so that one needs the directory in the URL."""
    return f"{base_url}{quote(item_id)}/" if single_file else base_url


def tick(state: State, watch_dir: Path, target_node: str, base_url: str) -> None:
    on_disk = {path.name for path in watch_dir.glob("*.torrent")}
    known = state.names()
    state.forget(known - on_disk)

    for name in sorted(on_disk - known):
        item_id = name.removesuffix(".torrent")
        try:
            torrent = read_torrent(watch_dir / name)
        except (OSError, ValueError):
            logger.exception("skipping %s", name)
            continue
        if not torrent.single_file and torrent.name != item_id:
            logger.error("skipping %s: its files are named %r, not %r", name, torrent.name, item_id)
            continue
        try:
            notify(target_node, torrent.info_hash, base_url + quote(name),
                   web_seed_url(base_url, item_id, torrent.single_file))
        except urllib.error.HTTPError as exc:
            # The node's JSON error says why; the traceback wouldn't.
            logger.error("%s rejected %s: HTTP %d %s", target_node, name,
                         exc.code, error_body(exc))
            continue
        except OSError:
            # Unreachable: every remaining item would wait out its own
            # timeout too. Everything unnotified is retried next tick.
            logger.exception("cannot reach %s", target_node)
            break
        state.mark_notified(name, torrent.info_hash, datetime.now(UTC).isoformat())
