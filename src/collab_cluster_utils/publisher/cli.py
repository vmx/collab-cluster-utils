"""Entry point: watch a directory of torrentizer output, seed whatever's in
it, and tell one target node to come get each item.

    collab-cluster-publisher

Configuration is via environment variables -- see Config in config.py:
WATCH_DIR, TARGET_NODE, ADVERTISE_HOST, BIND_HOST, BT_PORT, HTTP_PORT,
STATE_DB_PATH.
"""

from __future__ import annotations

import logging
import time

from .config import Config
from .http_server import serve
from .reconcile import RECONCILE_INTERVAL, tick
from .seed import SeedStore
from .state import State


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.from_env()

    store = SeedStore(config.bind_host, config.bt_port)
    state = State(config.state_db_path)
    serve(store, config.bind_host, config.http_port)
    peer = {"ip": config.advertise_host, "bt": config.bt_port, "http": config.http_port}

    logging.info("watching %s -> %s (advertising %s:%s / %s:%s)",
                 config.watch_dir, config.target_node,
                 config.advertise_host, config.bt_port, config.advertise_host, config.http_port)

    while True:
        try:
            tick(store, state, config.watch_dir, config.target_node, peer)
        except Exception:
            logging.exception("tick failed; retrying in %ss", RECONCILE_INTERVAL)
        time.sleep(RECONCILE_INTERVAL)


if __name__ == "__main__":
    main()
