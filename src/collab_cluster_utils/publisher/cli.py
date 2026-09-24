"""Entry point: serve a directory of torrentizer output over HTTP and tell one
target node to fetch each new item from it.

    collab-cluster-publisher

Configuration is via environment variables -- see Config in config.py:
WATCH_DIR, TARGET_NODE, ADVERTISE_HOST, BIND_HOST, HTTP_PORT, STATE_DB_PATH.
"""

from __future__ import annotations

import logging
import time

from .config import Config
from .http_server import serve
from .reconcile import RECONCILE_INTERVAL, tick
from .state import State


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = Config.from_env()

    state = State(config.state_db_path)
    serve(config.watch_dir, config.bind_host, config.http_port)
    logging.info("serving %s at %s, notifying %s",
                 config.watch_dir, config.base_url, config.target_node)

    while True:
        try:
            tick(state, config.watch_dir, config.target_node, config.base_url)
        except Exception:
            logging.exception("tick failed; retrying in %ss", RECONCILE_INTERVAL)
        time.sleep(RECONCILE_INTERVAL)


if __name__ == "__main__":
    main()
