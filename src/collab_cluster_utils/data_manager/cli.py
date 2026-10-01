"""Entry point: keep the local node holding exactly what the policy wants.

    collab-cluster-data-manager

Runs next to its node and talks to it at 127.0.0.1:8001. Settings:
POLICY_PATH, the policy file (default: ./data-manager-policy.toml), read once,
so restart to apply a change; BACKFILL=1 to also judge what other nodes
already hold, not just what they take from where it left off. Where it left
off is kept in ./data-manager-cursors.json.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from .policy import load_policy
from .reconcile import RECONCILE_INTERVAL, Manager

LOCAL_NODE = "http://127.0.0.1:8001"
DEFAULT_POLICY_PATH = "./data-manager-policy.toml"
# Where each peer's stream was read up to, so a restart picks up there.
CURSORS_PATH = Path("./data-manager-cursors.json")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    policy_path = Path(os.environ.get("POLICY_PATH") or DEFAULT_POLICY_PATH)
    rules = load_policy(policy_path)
    backfill = os.environ.get("BACKFILL") == "1"
    logging.info("managing %s with %d rule(s) from %s%s", LOCAL_NODE, len(rules),
                 policy_path, ", backfilling" if backfill else "")

    manager = Manager(rules, LOCAL_NODE, CURSORS_PATH, backfill)
    while True:
        try:
            manager.tick()
        except OSError as exc:
            logging.warning("cannot reach %s (%s); retrying in %ss",
                            LOCAL_NODE, exc, RECONCILE_INTERVAL)
        except Exception:
            logging.exception("tick failed; retrying in %ss", RECONCILE_INTERVAL)
        time.sleep(RECONCILE_INTERVAL)


if __name__ == "__main__":
    main()
