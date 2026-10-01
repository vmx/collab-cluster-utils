"""What this node should hold: the metadata a dataset's .torrent carries, the
rules from the policy file, and the one decision made from the two.

    [[rule]]                          # first match wins; no match: don't hold
    collection = "sentinel-2-l2a"
    bbox_intersects = [5.9, 45.8, 10.5, 47.8]   # west, south, east, north
    max_cloud_cover = 30
    retain_hours = 48                 # from sensing time; omitted: forever

Every condition is optional; a rule without any matches everything. All of
this is pure, so the policy can be tested without a node.
"""

from __future__ import annotations

import json
import math
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..bencode import entries, string

# collab-cluster-torrentizer's custom top-level key: its metadata as JSON.
METADATA_KEY = b"collab-cluster-torrentizer-metadata"


@dataclass(frozen=True)
class Metadata:
    sensing_time: datetime
    collection: str | None
    bbox: tuple[float, float, float, float] | None
    cloud_cover: float | None


def read_metadata(blob: bytes) -> Metadata | None:
    """The metadata embedded in a .torrent, or None if it carries none this
    can judge by -- not from torrentizer, or without a sensing time."""
    try:
        raw = entries(blob).get(METADATA_KEY)
        if raw is None:
            return None
        stac = json.loads(string(raw)[0]).get("stac") or {}
        properties = stac.get("properties") or {}
        sensing_time = datetime.fromisoformat(properties["datetime"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if sensing_time.tzinfo is None:
        sensing_time = sensing_time.replace(tzinfo=UTC)
    bbox = stac.get("bbox")
    if isinstance(bbox, list) and len(bbox) in (4, 6):
        # A 3D bbox is [w, s, min_z, e, n, max_z].
        half = len(bbox) // 2
        bbox = (bbox[0], bbox[1], bbox[half], bbox[half + 1])
    else:
        bbox = None
    cloud_cover = properties.get("eo:cloud_cover")
    return Metadata(sensing_time, stac.get("collection"), bbox,
                    cloud_cover if isinstance(cloud_cover, (int, float)) else None)


@dataclass(frozen=True)
class Rule:
    collection: str | None = None
    bbox_intersects: tuple[float, float, float, float] | None = None
    max_cloud_cover: float | None = None
    retain_hours: float | None = None

    def matches(self, meta: Metadata) -> bool:
        if self.collection is not None and meta.collection != self.collection:
            return False
        if self.bbox_intersects is not None:
            if meta.bbox is None:
                return False
            w, s, e, n = self.bbox_intersects
            mw, ms, me, mn = meta.bbox
            if mw > e or me < w or ms > n or mn < s:
                return False
        if self.max_cloud_cover is not None:
            if meta.cloud_cover is None or meta.cloud_cover > self.max_cloud_cover:
                return False
        return True


def load_policy(path: Path) -> list[Rule]:
    """The rules, in order. Unknown keys are an error, so a typo doesn't
    silently turn into a rule that matches everything."""
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    unknown = set(doc) - {"rule"}
    if unknown:
        raise ValueError(f"{path}: unknown key(s): {', '.join(sorted(unknown))}")
    rules = []
    for n, table in enumerate(doc.get("rule", []), 1):
        unknown = set(table) - set(Rule.__dataclass_fields__)
        if unknown:
            raise ValueError(f"{path}: rule {n}: unknown key(s): {', '.join(sorted(unknown))}")
        bbox = table.get("bbox_intersects")
        if bbox is not None:
            if len(bbox) != 4:
                raise ValueError(f"{path}: rule {n}: bbox_intersects is [west, south, east, north]")
            table = {**table, "bbox_intersects": tuple(bbox)}
        rules.append(Rule(**table))
    return rules


def hold_until(rules: list[Rule], meta: Metadata) -> float:
    """Until when (POSIX seconds) to hold a dataset: -inf if no rule wants it,
    inf if one wants it forever. A single number because the rules are fixed
    while the manager runs: a dataset's answer only ever changes from "hold"
    to "don't", when that time passes."""
    for rule in rules:
        if rule.matches(meta):
            if rule.retain_hours is None:
                return math.inf
            return (meta.sensing_time + timedelta(hours=rule.retain_hours)).timestamp()
    return -math.inf
