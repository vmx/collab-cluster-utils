from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from collab_cluster_utils.data_manager.policy import (
    Metadata, Rule, hold_until, load_policy, read_metadata)

from .helpers import stac_metadata, torrent_bytes

SENSED = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)


def should_hold(rules, meta, now: datetime) -> bool:
    return now.timestamp() < hold_until(rules, meta)


def meta(**kwargs) -> Metadata:
    fields = {"sensing_time": SENSED, "collection": "sentinel-2-l2a",
              "bbox": (7.0, 46.0, 8.0, 47.0), "cloud_cover": 10.0}
    return Metadata(**{**fields, **kwargs})


def test_reads_torrentizer_metadata():
    _, blob = torrent_bytes("item", stac_metadata("2026-09-30T10:00:00.123Z", cloud_cover=12.5))
    m = read_metadata(blob)
    assert m.sensing_time == SENSED.replace(microsecond=123000)
    assert m.collection == "sentinel-2-l2a"
    assert m.bbox == (7.0, 46.0, 8.0, 47.0)
    assert m.cloud_cover == 12.5


def test_a_3d_bbox_is_flattened():
    _, blob = torrent_bytes("item", stac_metadata("2026-09-30T10:00:00Z",
                                                  bbox=[7.0, 46.0, 0, 8.0, 47.0, 100]))
    assert read_metadata(blob).bbox == (7.0, 46.0, 8.0, 47.0)


@pytest.mark.parametrize("metadata", [None, {"stac": {}}, {"stac": {"properties": {"datetime": "soon"}}}])
def test_no_metadata_to_judge_by(metadata):
    _, blob = torrent_bytes("item", metadata)
    assert read_metadata(blob) is None


def test_no_rules_hold_nothing():
    assert not should_hold([], meta(), SENSED)


def test_an_empty_rule_holds_everything_forever():
    assert should_hold([Rule()], meta(), SENSED + timedelta(days=10_000))


def test_rolling_window_from_sensing_time():
    rules = [Rule(retain_hours=48)]
    assert should_hold(rules, meta(), SENSED + timedelta(hours=47, minutes=59))
    assert not should_hold(rules, meta(), SENSED + timedelta(hours=48))


@pytest.mark.parametrize("rule, held", [
    (Rule(collection="sentinel-2-l2a"), True),
    (Rule(collection="landsat"), False),
    (Rule(bbox_intersects=(7.5, 46.5, 9.0, 48.0)), True),     # overlaps
    (Rule(bbox_intersects=(8.5, 46.0, 9.0, 47.0)), False),    # east of it
    (Rule(bbox_intersects=(7.0, 40.0, 8.0, 45.0)), False),    # south of it
    (Rule(max_cloud_cover=10), True),
    (Rule(max_cloud_cover=9.9), False),
])
def test_conditions(rule, held):
    assert should_hold([rule], meta(), SENSED) is held


def test_missing_fields_dont_match_conditions_on_them():
    assert not should_hold([Rule(max_cloud_cover=50)], meta(cloud_cover=None), SENSED)
    assert not should_hold([Rule(bbox_intersects=(0, 0, 1, 1))], meta(bbox=None), SENSED)


def test_first_match_wins():
    rules = [Rule(collection="sentinel-2-l2a", retain_hours=1), Rule()]
    assert not should_hold(rules, meta(), SENSED + timedelta(hours=2))


def test_load_policy(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text('''
[[rule]]
collection = "sentinel-2-l2a"
bbox_intersects = [5.9, 45.8, 10.5, 47.8]
max_cloud_cover = 30
retain_hours = 48

[[rule]]
''')
    assert load_policy(path) == [
        Rule("sentinel-2-l2a", (5.9, 45.8, 10.5, 47.8), 30, 48), Rule()]


@pytest.mark.parametrize("content", [
    '[[rule]]\nretain_hour = 48\n',
    '[limits]\nmax_bytes = 1\n',
    '[[rule]]\nbbox_intersects = [1, 2, 3]\n',
])
def test_load_policy_rejects_typos(tmp_path, content):
    path = tmp_path / "policy.toml"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_policy(path)
