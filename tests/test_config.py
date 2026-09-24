from pathlib import Path

import pytest

from collab_cluster_utils.publisher.config import Config


def test_from_env_applies_defaults():
    config = Config.from_env({
        "WATCH_DIR": "/data/output",
        "TARGET_NODE": "10.0.0.5:8001",
        "ADVERTISE_HOST": "10.0.0.9",
    })
    assert config.watch_dir == Path("/data/output")
    assert config.target_node == "10.0.0.5:8001"
    assert config.advertise_host == "10.0.0.9"
    assert config.bind_host == "0.0.0.0"
    assert config.http_port == 8090
    assert config.state_db_path == Path("./collab-cluster-publisher-state.sqlite3")
    assert config.base_url == "http://10.0.0.9:8090/"


def test_from_env_honors_overrides():
    config = Config.from_env({
        "WATCH_DIR": "/data/output",
        "TARGET_NODE": "10.0.0.5",
        "ADVERTISE_HOST": "10.0.0.9",
        "BIND_HOST": "10.0.0.9",
        "HTTP_PORT": "7002",
        "STATE_DB_PATH": "/var/lib/collab-cluster-publisher/state.sqlite3",
    })
    assert config.target_node == "10.0.0.5:8001"
    assert config.bind_host == "10.0.0.9"
    assert config.http_port == 7002
    assert config.state_db_path == Path("/var/lib/collab-cluster-publisher/state.sqlite3")


@pytest.mark.parametrize("missing", ["WATCH_DIR", "TARGET_NODE", "ADVERTISE_HOST"])
def test_from_env_requires_the_essentials(missing):
    env = {
        "WATCH_DIR": "/data/output",
        "TARGET_NODE": "10.0.0.5:8001",
        "ADVERTISE_HOST": "10.0.0.9",
    }
    del env[missing]
    with pytest.raises(ValueError, match=missing):
        Config.from_env(env)


def test_from_env_keeps_an_explicit_target_port():
    config = Config.from_env({
        "WATCH_DIR": "/data/output",
        "TARGET_NODE": "10.0.0.5:8004",
        "ADVERTISE_HOST": "10.0.0.9",
    })
    assert config.target_node == "10.0.0.5:8004"
