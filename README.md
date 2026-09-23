collab-cluster-utils
=====================

Independent utilities supporting the collab-cluster project. Each one is
meant to stand alone; this repo is just where they live.

collab-cluster-publisher
------------------------

Bridges [collab-cluster-torrentizer]'s output to one [collab-cluster-node]:
watches for `<item_id>/` + `<item_id>.torrent` pairs and tells one
configured node about each.

[collab-cluster-torrentizer]: ../collab-cluster-torrentizer
[collab-cluster-node]: ../collab-cluster-experiment

It's a bare BitTorrent peer, not a collab-cluster node -- it never joins the
swarm, no beacon, no discovery. It holds a seed-only libtorrent session for
whatever's currently in the watched directory and answers one HTTP route,
`GET /dataset/<info_hash>.torrent` (the same route a node answers for other
nodes), so the target node's existing `/add` can fetch from it like any
peer.

Every 10 seconds it rescans the directory and, for anything new, calls:

```
POST /add {"info_hash": ..., "peer": {"ip": ..., "bt": ..., "http": ...}}
```

The node does the rest: fetches the `.torrent` over HTTP, the data over
BitTorrent, and verifies it. "Already notified" is tracked in a small local
sqlite ledger rather than by asking the node, and rescans never re-parse a
`.torrent` already seen, so cost stays proportional to what's new, not to
total history -- built for tens of thousands of items a day, indefinitely.
Both the seed session and the ledger forget an item once it's gone from
disk; nothing actively evicts an item early just because the target node
finished taking it.

Spreading a copy to further nodes is a separate, already-solved concern
(`control.py add`) -- not this tool's job.

### Setup & run

```console
> uv sync
> uv run collab-cluster-publisher
```

Config via environment variables (`Config` in
`src/collab_cluster_utils/publisher/config.py`): `WATCH_DIR`, `TARGET_NODE`,
`ADVERTISE_HOST` (required); `BIND_HOST`, `BT_PORT`, `HTTP_PORT`,
`STATE_DB_PATH` (optional, defaulted).

### Background service

```console
> cp deploy/.env.example deploy/.env   # then edit
> ./deploy/service.sh install
> ./deploy/service.sh logs
> ./deploy/service.sh uninstall
```

Persistent `systemd --user` unit, same mechanics as
collab-cluster-torrentizer's `deploy/service.sh`.

### Tests

```console
> uv run pytest
```

`test_seed.py` runs a real BitTorrent transfer between two local libtorrent
sessions; the rest are unit tests against fakes.

### Files

| File | Role |
|---|---|
| `config.py` | `Config.from_env()`. |
| `seed.py` | Seed-only libtorrent session + registry of what's servable. |
| `http_server.py` | `GET /dataset/<hash>.torrent`, reading `seed.py`'s registry. |
| `state.py` | sqlite ledger of what's been notified. |
| `reconcile.py` | Sync the seed store, notify the target node. |
| `cli.py` | Wires it together, loops. |

License
-------

Apache-2.0 OR MIT -- see [LICENSE-APACHE](./LICENSE-APACHE) / [LICENSE-MIT](./LICENSE-MIT).
