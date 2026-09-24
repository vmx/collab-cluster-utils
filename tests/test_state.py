from collab_cluster_utils.publisher.state import State


def test_starts_empty(tmp_path):
    assert State(tmp_path / "state.sqlite3").names() == set()


def test_mark_and_forget(tmp_path):
    state = State(tmp_path / "state.sqlite3")
    state.mark_notified("a.torrent", "aa", "2026-01-01T00:00:00")
    state.mark_notified("b.torrent", "bb", "2026-01-01T00:00:00")
    assert state.names() == {"a.torrent", "b.torrent"}
    state.forget(["a.torrent"])
    assert state.names() == {"b.torrent"}


def test_survives_reopening_the_same_file(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    State(db_path).mark_notified("a.torrent", "aa", "2026-01-01T00:00:00")
    assert State(db_path).names() == {"a.torrent"}
