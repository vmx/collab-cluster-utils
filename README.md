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

It serves the watched directory read-only over HTTP and, every 10 seconds,
calls this on the target node for each `.torrent` it hasn't seen yet:

```
POST /add {"info_hash": ..., "torrent_url": ..., "web_seed": ...}
```

The node does the rest: fetches the `.torrent` from `torrent_url`, downloads
the data from `web_seed` (a BEP 19 web seed -- plain HTTP, with every piece
checked against the torrent's hashes), and from then on holds it like any
other dataset. The publisher runs no BitTorrent itself and needs nothing
beyond the Python standard library. The node needs libtorrent 2.1.2 or
later: older versions fail piece hashes at file boundaries when a v2
torrent has more than one file.

"Already notified" is tracked in a small local sqlite ledger, keyed by file
name, so a tick costs one directory listing plus parsing whatever is new --
built for tens of thousands of items a day, indefinitely. The ledger is
durable so a restart doesn't re-offer items someone has since removed from
the node; an entry is forgotten once its file leaves the directory.

Spreading a copy to further nodes is a separate, already-solved concern
(`control.py add`) -- not this tool's job.

### Setup & run

```console
> uv sync
> uv run collab-cluster-publisher
```

Config via environment variables (`Config` in
`src/collab_cluster_utils/publisher/config.py`): `WATCH_DIR`, `TARGET_NODE`,
`ADVERTISE_HOST` (required); `BIND_HOST`, `HTTP_PORT`,
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

`test_web_seed.py` has libtorrent download items from the publisher's HTTP
server as a web seed; its multi-file case is skipped on libtorrent older than
2.1.2. The rest are unit tests without libtorrent.

### Files

| File | Role |
|---|---|
| `config.py` | `Config.from_env()`. |
| `http_server.py` | Read-only file server over the watched directory, with byte ranges. |
| `torrent.py` | A `.torrent`'s v2 info-hash and shape, without libtorrent. |
| `state.py` | sqlite ledger of what's been notified. |
| `reconcile.py` | Find new `.torrent` files, notify the target node. |
| `cli.py` | Wires it together, loops. |

License
-------

Apache-2.0 OR MIT -- see [LICENSE-APACHE](./LICENSE-APACHE) / [LICENSE-MIT](./LICENSE-MIT).
