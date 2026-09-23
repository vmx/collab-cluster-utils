"""Runtime configuration loaded from environment variables.

Only what genuinely varies per deployment is here -- a path, two addresses,
where the ledger lives. Timing constants (RECONCILE_INTERVAL in
reconcile.py) and session settings (seed.py's make_session) are fixed in
code, the same way collab-cluster-experiment treats BEACON_INTERVAL or
NODE_LOOP_INTERVAL as plain constants rather than environment knobs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Distinct from collab-cluster-experiment's own port ranges (6881+id,
# 8001+id) so a publisher and a node can share a host without colliding.
DEFAULT_BT_PORT = 6890
DEFAULT_HTTP_PORT = 8090
DEFAULT_STATE_DB_PATH = "./collab-cluster-publisher-state.sqlite3"


@dataclass(frozen=True)
class Config:
    watch_dir: Path
    target_node: str      # the node to notify: host[:port], port defaults to 8001
    advertise_host: str   # this process's reachable address -- can't be self-detected
    bind_host: str = "0.0.0.0"
    bt_port: int = DEFAULT_BT_PORT
    http_port: int = DEFAULT_HTTP_PORT
    state_db_path: Path = Path(DEFAULT_STATE_DB_PATH)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = env if env is not None else os.environ
        missing = [name for name in ("WATCH_DIR", "TARGET_NODE", "ADVERTISE_HOST")
                   if not env.get(name)]
        if missing:
            raise ValueError(f"missing required environment variable(s): {', '.join(missing)}")
        return cls(
            watch_dir=Path(env["WATCH_DIR"]),
            target_node=env["TARGET_NODE"],
            advertise_host=env["ADVERTISE_HOST"],
            bind_host=env.get("BIND_HOST", cls.bind_host),
            bt_port=int(env.get("BT_PORT", DEFAULT_BT_PORT)),
            http_port=int(env.get("HTTP_PORT", DEFAULT_HTTP_PORT)),
            state_db_path=Path(env.get("STATE_DB_PATH", DEFAULT_STATE_DB_PATH)),
        )
