"""Runtime configuration loaded from environment variables.

Only what genuinely varies per deployment is here -- a path, two addresses,
where the ledger lives. Timing (RECONCILE_INTERVAL in reconcile.py) is fixed
in code, the same way collab-cluster-experiment treats BEACON_INTERVAL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# Distinct from collab-cluster-experiment's own HTTP ports (8001+id) so a
# publisher and a node can share a host without colliding.
DEFAULT_HTTP_PORT = 8090
DEFAULT_STATE_DB_PATH = "./collab-cluster-publisher-state.sqlite3"
# collab-cluster-experiment's STATS_PORT_BASE: node 0's HTTP API.
DEFAULT_TARGET_PORT = 8001


def with_default_port(target_node: str) -> str:
    """host[:port] -> host:port, so an unset port means the node's, not 80."""
    if urlsplit(f"//{target_node}").port is None:
        return f"{target_node}:{DEFAULT_TARGET_PORT}"
    return target_node


@dataclass(frozen=True)
class Config:
    watch_dir: Path
    target_node: str      # the node to notify: host[:port], port defaults to 8001
    advertise_host: str   # this process's address as the node reaches it -- can't be self-detected
    bind_host: str = "0.0.0.0"
    http_port: int = DEFAULT_HTTP_PORT
    state_db_path: Path = Path(DEFAULT_STATE_DB_PATH)

    @property
    def base_url(self) -> str:
        return f"http://{self.advertise_host}:{self.http_port}/"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = env if env is not None else os.environ
        missing = [name for name in ("WATCH_DIR", "TARGET_NODE", "ADVERTISE_HOST")
                   if not env.get(name)]
        if missing:
            raise ValueError(f"missing required environment variable(s): {', '.join(missing)}")
        return cls(
            watch_dir=Path(env["WATCH_DIR"]),
            target_node=with_default_port(env["TARGET_NODE"]),
            advertise_host=env["ADVERTISE_HOST"],
            bind_host=env.get("BIND_HOST", cls.bind_host),
            http_port=int(env.get("HTTP_PORT", DEFAULT_HTTP_PORT)),
            state_db_path=Path(env.get("STATE_DB_PATH", DEFAULT_STATE_DB_PATH)),
        )
