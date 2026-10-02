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
> ./deploy/service.sh publisher install
> ./deploy/service.sh publisher logs
> ./deploy/service.sh publisher uninstall
```

Persistent `systemd --user` unit, same mechanics as
collab-cluster-torrentizer's `deploy/service.sh`.

### Files

| File | Role |
|---|---|
| `config.py` | `Config.from_env()`. |
| `http_server.py` | Read-only file server over the watched directory, with byte ranges. |
| `torrent.py` | A `.torrent`'s v2 info-hash and shape (bencode in `../bencode.py`). |
| `state.py` | sqlite ledger of what's been notified. |
| `reconcile.py` | Find new `.torrent` files, notify the target node. |
| `cli.py` | Wires it together, loops. |

collab-cluster-data-manager
---------------------------

Decides what one [collab-cluster-node] holds. It runs next to its node (in the
same container) and talks to it at `127.0.0.1:8001`. Every 10 seconds it:

1. follows the local node's `/holdings`, judges each dataset there once from
   its `.torrent` (fetched from the local node), and `POST /remove`s what the
   policy doesn't want (which deletes the node's downloaded copy);
2. follows every peer's `/holdings` from the moment it first sees the peer
   (its cursor from `/stats`): each `downloading` row is a dataset arriving
   there. It fetches that `.torrent` from the peer, judges it, and
   `POST /add`s it to the local node if the policy wants it.

A dataset's metadata is the `collab-cluster-torrentizer-metadata` key in its
`.torrent`.

The policy is a TOML file of rules; the first match decides, and a dataset no
rule matches is not held:

```toml
[[rule]]
collection = "sentinel-2-l2a"
bbox_intersects = [5.9, 45.8, 10.5, 47.8]   # west, south, east, north
max_cloud_cover = 30
retain_hours = 48                           # from sensing time; omit to keep forever
```

Every condition is optional. `retain_hours` makes a rolling archive: a
dataset is removed once its sensing time (STAC `datetime`) is that far in the
past, and isn't taken in the first place if it already is.

The policy applies to **everything the node holds, whoever added it** --
including what a publisher delivered to it, and what you `control.py add`ed
by hand. Only datasets without torrentizer metadata (e.g. ones
`control.py publish`ed) are left alone. A dataset whose last copy is removed
has left the swarm; with the same rolling policy on every node, that is how
the archive rolls.

Nothing is kept about the swarm as a whole: the local node is the authority on
what it holds. In memory are until when to hold each locally held dataset, a
cursor per peer, and for 48 hours the datasets judged unwanted, so a dataset
arriving at further nodes isn't fetched and judged again. A restart costs one
local `.torrent` fetch per dataset the node holds.

The only thing persisted is the peers' cursors, in
`data-manager-cursors.json` (in the working directory). After a restart each
peer's stream hands over exactly what arrived while the manager was down. A
cursor is saved only once everything before it has been judged. How long an
outage this covers is bounded by the node's change log (`CHANGE_LOG_LIMIT`,
100,000 transitions -- more than a day at 20k datasets a day). The node never
answers a cursor it can't fully cover, and the manager logs a warning whenever
a peer can't answer from its cursor -- after a longer outage, a peer restart,
or a peer it couldn't reach for too long -- and follows that peer from now on.

For that, or a first start, `BACKFILL=1` judges everything each peer already
holds when first seen, ignoring saved cursors. Each dataset's `.torrent` is
fetched once, however many peers hold it. A dataset someone removes from the
local node by hand stays removed.

Fetching `.torrent`s gets at most 5 seconds per tick for each of the two
directions, so a large backlog (a backfill, a restart of a node holding a lot)
is worked through over several ticks without holding up the rest.

The node needs to be recent enough that `/remove` deletes the downloaded
copy; older ones keep the files and never free the space.

### Limitations: datasets it can miss

Removal is reliable: what the local node holds is always listed in full and
judged. **Taking is best effort.** The manager sees a dataset only as an
arrival in some peer's holdings stream, and judges each arrival once. A
dataset the policy wants can therefore never reach this node, and nothing
retries it. That happens when:

- **a peer can't answer from its cursor** -- the manager was down longer than
  that peer's change log reaches, the peer restarted, or the manager couldn't
  reach it for too long. Logged as a warning; what arrived there in between
  is never judged.
- **the peer drops the dataset before it's judged**, e.g. during a backlog.
- **the local node rejects the `/add`** (logged as an error). It's retried
  only if the dataset arrives at another node later.
- **the dataset is on a node the local node doesn't see** (`/peers` comes from
  the multicast beacon).
- **the policy is changed.** It applies to what the local node holds and to
  arrivals from then on, but datasets that already arrived and were judged
  under the old policy aren't looked at again.

In each case a dataset is only missed if it doesn't arrive at another node
later -- every new copy elsewhere is a new arrival. `BACKFILL=1` is the
recovery: it judges everything the peers hold. But it's manual, and costs one
`.torrent` fetch per dataset in the swarm.

That's acceptable for a prototype. Something meant for production would
need, for example:

- **a source of new datasets that doesn't forget**: a durable, ordered feed
  whose position survives restarts on both sides, e.g. a change log on the
  node that's persisted and retained by time rather than by count, or the
  manager following the same upstream feed the torrentizer reads
  (Jetstream, with its own cursor) instead of the nodes;
- **periodic reconciliation instead of a manual backfill**, with a persisted
  metadata cache so that comparing against the whole swarm stays cheap;
- **metrics and alerts** for gaps, rejected adds and backlog size, rather
  than log lines.

### Setup & run

```console
> uv sync
> cp deploy/data-manager-policy.example.toml data-manager-policy.toml   # then edit
> uv run collab-cluster-data-manager
```

`deploy/` has more examples to start from: a six-hour rolling archive
(`data-manager-policy.rolling-6h.example.toml`) and keeping everything from
Europe (`data-manager-policy.europe.example.toml`).

`POLICY_PATH` overrides where the policy is read from. It's read once at
startup -- restart to apply a change. `BACKFILL=1` turns on backfill (see
above). As a background service:
`./deploy/service.sh data-manager install` (same mechanics as above).

### In a node's Incus container

The collab-cluster profile ([collab-cluster-node]'s `incus/collab-cluster.yaml`)
clones this repo to `/home/debian/collab-cluster-utils` and installs
`deploy/incus/collab-cluster-data-manager.service`. That unit runs the manager
with the system `python3` straight from the checkout -- it needs nothing
beyond the standard library, so no uv or venv. Give a node a policy when
creating it and the unit is enabled:

```console
> ./incus/new-container.sh node node0 --policy my-policy.toml
```

Re-running it with a changed policy pushes it and restarts the manager. To do
it by hand, push the policy to
`/home/debian/collab-cluster-utils/data-manager-policy.toml` (owned by
`debian`) and `systemctl --user enable --now collab-cluster-data-manager` as
`debian`.

### Files

| File | Role |
|---|---|
| `policy.py` | Read a `.torrent`'s metadata, load the rules, decide. Pure. |
| `reconcile.py` | Follow the local node and what arrives at peers; add and remove. |
| `cli.py` | Wires it together, loops. |
| `../swarm.py` | The node API calls it uses (shared with the rescuer). |

collab-cluster-rescuer
----------------------

Fills a node's spare space with the swarm's rarest datasets, so that old data
that has left the rolling archives still has copies beyond the full archive.
It's for people with space to give: a rescue node runs the rescuer instead of
a data manager, and joins or leaves whenever its owner likes. Each one is best
effort, and different rescue nodes end up holding different parts.

It runs next to its node (`127.0.0.1:8001`) and asks a [collab-cluster-node]
collector, whose index knows how many copies of everything exist. About once a
minute (jittered, so rescuers don't act in step) it:

1. gets `GET /api/rescue?node=<its node key>` from the collector: a random
   sample of the rarest datasets the node doesn't hold, and the node's own
   holdings with the most copies;
2. `POST /add`s those with **fewer than two copies** that fit into its budget,
   at most 4 downloading at once;
3. when the budget is full, swaps: it `POST /remove`s a holding to make room
   for one, but only a holding that keeps **at least two complete copies**
   without it.

So rescue nodes work towards exactly two copies of everything. A dataset with
two is never copied again, nor let go of again; space nobody needs for that
stays empty. A download under way counts as a copy, so a dataset being
rescued elsewhere isn't taken again. Several rescuers need no coordination:
they're handed different random samples, and a dataset two of them happen to
take at the same moment has a copy to spare, the first thing either lets go
of.

Nothing is kept between rounds. The node says what it holds, and the collector
says what everyone else holds.

### Limitations

- **Copies on rolling archives count like any other.** A dataset with three
  copies, one of them on a rolling archive, may be let go of. When that copy
  expires, the dataset dips to one copy until a rescuer takes it again.
- **Two rescuers can let go of the same dataset in the same moment**, and
  take it from three copies to one. It's then among the rarest again.
- **Without the collector it pauses.** It keeps what it has and takes nothing.
- **Space limits it.** If the rescue nodes together have less space than
  the archive, not everything reaches two copies. Full rescuers then only
  make room by letting go of copies to spare, never of a second copy.

### Setup & run

```console
> uv sync
> RESCUE_BYTES=2000000000000 COLLECTOR=10.0.0.2 uv run collab-cluster-rescuer
```

`RESCUE_BYTES` is how much the node may hold, everything on it included.
Lowering it makes the rescuer let go of holdings with copies to spare until
it's under. `COLLECTOR` is `host[:port]`, and the port defaults to 8100. As a
background service: put both in `deploy/.env`, then
`./deploy/service.sh rescuer install`.

In a node's Incus container, put both settings in
`/home/debian/collab-cluster-utils/rescuer.env`, copy
`deploy/incus/collab-cluster-rescuer.service` to `~/.config/systemd/user/`,
and `systemctl --user enable --now collab-cluster-rescuer`, all as `debian`.

### Files

| File | Role |
|---|---|
| `rescue.py` | What to take and let go of (`plan()`, pure), and a round of it. |
| `cli.py` | Settings, loops. |

Tests
-----

```console
> uv run pytest
```

`test_web_seed.py` has libtorrent download items from the publisher's HTTP
server as a web seed; its multi-file case is skipped on libtorrent older than
2.1.2. The rest are unit tests without libtorrent.

License
-------

Apache-2.0 OR MIT -- see [LICENSE-APACHE](./LICENSE-APACHE) / [LICENSE-MIT](./LICENSE-MIT).
