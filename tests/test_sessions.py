import pytest

from toc_rag.sessions import SessionManager

TOKEN = "abcd1234-token"


class Clock:
    def __init__(self, now: float = 1_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_ensure_creates_a_session_with_an_expiry(tmp_path):
    clock = Clock()
    sessions = SessionManager(tmp_path, ttl_seconds=100, clock=clock)

    assert sessions.get(TOKEN) is None
    session = sessions.ensure(TOKEN)

    assert (session.created, session.expires_at, session.was_reset) == (1_000.0, 1_100.0, False)
    assert sessions.get(TOKEN) == session
    assert sessions.ensure(TOKEN) == session  # not restarted while live
    assert sessions.store(TOKEN).list_documents() == []


def test_expired_session_is_wiped_and_restarted(tmp_path):
    clock = Clock()
    sessions = SessionManager(tmp_path, ttl_seconds=100, clock=clock)
    sessions.ensure(TOKEN)
    (sessions.uploads_dir(TOKEN)).mkdir()
    (sessions.uploads_dir(TOKEN) / "a.docx").write_bytes(b"x")

    clock.now += 100
    assert sessions.get(TOKEN) is None  # expired exactly at the ttl

    fresh = sessions.ensure(TOKEN)
    assert fresh.was_reset and fresh.created == 1_100.0
    assert not sessions.uploads_dir(TOKEN).exists()


def test_sweep_removes_only_expired_sessions(tmp_path):
    clock = Clock()
    sessions = SessionManager(tmp_path, ttl_seconds=100, clock=clock)
    sessions.ensure("old-session-1")
    clock.now += 60
    sessions.ensure("new-session-1")
    (tmp_path / "no-marker-dir").mkdir()  # unknown folders count as expired
    (tmp_path / "stray.txt").write_text("not a session")

    clock.now += 50  # old: 110s old, new: 50s old
    assert sessions.sweep() == 2

    assert sessions.get("new-session-1") is not None
    assert not sessions.session_dir("old-session-1").exists()
    assert not (tmp_path / "no-marker-dir").exists()
    assert (tmp_path / "stray.txt").exists()


def test_sweep_with_no_sessions_folder(tmp_path):
    assert SessionManager(tmp_path / "missing", ttl_seconds=10).sweep() == 0


def test_delete_ends_a_session(tmp_path):
    sessions = SessionManager(tmp_path, ttl_seconds=100)
    sessions.ensure(TOKEN)
    sessions.delete(TOKEN)
    assert sessions.get(TOKEN) is None and not sessions.session_dir(TOKEN).exists()
    sessions.delete(TOKEN)  # deleting twice is fine


@pytest.mark.parametrize("token", ["", "short", "../escape-token", "a/b/c/d/e/f/g/h", "x" * 200, "has space 123"])
def test_invalid_tokens_are_rejected(tmp_path, token):
    sessions = SessionManager(tmp_path, ttl_seconds=100)
    with pytest.raises(ValueError):
        sessions.ensure(token)
    with pytest.raises(ValueError):
        sessions.delete(token)
