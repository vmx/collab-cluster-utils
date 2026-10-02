"""Entry point: fill the local node's spare space with the rarest datasets.

    collab-cluster-rescuer

Runs next to its node and talks to it at 127.0.0.1:8001. Settings:
RESCUE_BYTES, how much the node may hold (required); COLLECTOR, the
collector to ask, host[:port] (required, port defaults to 8100).
"""

from __future__ import annotations

import logging
import os
import random
import sys
import time

from .rescue import Rescuer

LOCAL_NODE = "http://127.0.0.1:8001"
COLLECTOR_PORT = 8100
ROUND = 60          # seconds between rounds, jittered so rescuers don't act in step


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        budget = int(os.environ["RESCUE_BYTES"])
        collector = os.environ["COLLECTOR"]
    except (KeyError, ValueError):
        sys.exit("set RESCUE_BYTES (bytes the node may hold) and COLLECTOR (host[:port])")
    if "://" not in collector:
        collector = f"http://{collector}"
    if collector.count(":") == 1:
        collector += f":{COLLECTOR_PORT}"
    logging.info("rescuing into %s, up to %d bytes, asking %s",
                 LOCAL_NODE, budget, collector)

    rescuer = Rescuer(LOCAL_NODE, collector, budget)
    while True:
        try:
            rescuer.tick()
        except OSError as exc:
            logging.warning("cannot reach %s (%s); retrying next round", LOCAL_NODE, exc)
        except Exception:
            logging.exception("round failed; retrying next round")
        time.sleep(ROUND * random.uniform(0.5, 1.5))


if __name__ == "__main__":
    main()
