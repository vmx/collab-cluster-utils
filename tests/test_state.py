from collab_cluster_utils.publisher.state import State


def test_unknown_hash_is_not_notified(tmp_path):
    state = State(tmp_path / "state.sqlite3")
    assert state.is_notified("deadbeef") is False


def test_mark_then_is_notified(tmp_path):
    state = State(tmp_path / "state.sqlite3")
    state.mark_notified("deadbeef", "2026-01-01T00:00:00")
    assert state.is_notified("deadbeef") is True


def test_forget_makes_it_unnotified_again(tmp_path):
    state = State(tmp_path / "state.sqlite3")
    state.mark_notified("deadbeef", "2026-01-01T00:00:00")
    state.forget("deadbeef")
    assert state.is_notified("deadbeef") is False


def test_survives_reopening_the_same_file(tmp_path):
    db_path = tmp_path / "state.sqlite3"
    State(db_path).mark_notified("deadbeef", "2026-01-01T00:00:00")
    assert State(db_path).is_notified("deadbeef") is True
