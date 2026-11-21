import time

import pytest
from gi.repository import GLib

from rosk.store.repo import Repo
from rosk.sync import manager
from rosk.sync.manager import SyncManager, fingerprint


def run_loop(ms):
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "PUSH_DELAY", 0.1)
    repo = Repo(tmp_path / "m.db")
    m = SyncManager(repo)
    starts = []

    def fake_start():
        m._timer = None
        starts.append((time.monotonic(), m._quiet))
        return False
    m._start = fake_start
    return repo, m, starts


def test_push_waits_for_the_last_change(mgr):
    repo, m, starts = mgr
    iid = repo.create_note(title="a")
    t0 = time.monotonic()
    for text in ("ab", "abc", "abcd"):
        run_loop(50)
        repo.update(iid, title=text)
    last = time.monotonic()
    run_loop(300)
    assert len(starts) == 1  # one sync for the whole burst of edits
    assert starts[0][0] >= last + 0.09 and starts[0][0] > t0 + 0.2
    assert starts[0][1] is False  # a push shows the spinner


def test_polls_are_quiet_unless_a_change_joins(mgr):
    _repo, m, starts = mgr
    m.request(0, quiet=True)
    run_loop(50)
    m.request(0, quiet=True)
    m.request(0.05)  # a local change coalesced with the poll: not quiet any more
    run_loop(150)
    assert [q for _t, q in starts] == [True, False]


def test_focus_syncs_only_when_stale(mgr):
    _repo, m, starts = mgr
    m.last_sync = time.time()
    m.set_active(True)
    run_loop(50)
    assert starts == []  # synced a moment ago
    m.set_active(False)
    m.last_sync = time.time() - manager.FOCUS_MIN_AGE - 1
    m.set_active(True)
    run_loop(50)
    assert [q for _t, q in starts] == [True]


def test_fingerprint_sees_only_real_changes(tmp_path):
    repo = Repo(tmp_path / "f.db")
    iid = repo.create_note(title="a")
    store = repo.store
    before = fingerprint(store)
    with store.tx():
        store.set_meta("tasks_cursor:x", "2026-11-21T00:00:00Z")  # sync bookkeeping only
        store.update_item(iid, touch=False, title="a")  # a pull that found nothing new
    assert fingerprint(store) == before
    with store.tx():
        store.update_item(iid, touch=False, title="b", remote_updated="2026-11-21T10:00:00.000Z")
    assert fingerprint(store) != before


def test_mode_follows_the_user(mgr):
    _repo, m, _starts = mgr
    idle = [0]
    m._idle_seconds = lambda: idle[0]
    m.locked = False
    assert m.mode() == manager.PRESENT
    m.active = True
    assert m.mode() == manager.ACTIVE
    idle[0] = manager.AWAY_IDLE
    assert m.mode() == manager.AWAY  # the window stays focused, but nobody is there
    idle[0] = 0
    m.locked = True
    assert m.mode() == manager.AWAY


def test_heartbeat_polls_by_mode(mgr):
    _repo, m, starts = mgr
    idle = [0]
    m._idle_seconds = lambda: idle[0]
    m.active = False

    m.last_sync = time.time() - manager.PRESENT_INTERVAL + 30
    m._heartbeat()
    run_loop(20)
    assert starts == []  # not due yet
    m.last_sync = time.time() - manager.PRESENT_INTERVAL
    m._heartbeat()
    run_loop(20)
    assert len(starts) == 1

    idle[0] = manager.AWAY_IDLE  # away: an hour between polls
    m.last_sync = time.time() - manager.PRESENT_INTERVAL * 2
    m._heartbeat()
    run_loop(20)
    assert len(starts) == 1

    idle[0] = 0  # back at the computer after a while: catch up at once
    m.last_sync = time.time() - manager.RETURN_MIN_AGE - 1
    m._heartbeat()
    run_loop(20)
    assert len(starts) == 2 and starts[-1][1] is True  # quietly


def test_polls_reread_task_lists_every_other_time(mgr):
    repo, m, _starts = mgr
    m._idle_seconds = lambda: 0
    m.active = False
    interval = manager.PRESENT_INTERVAL
    m._quiet = True
    m.last_lists_sync = time.time() - interval  # one poll ago
    assert m._wants_lists() is False
    m.last_lists_sync = time.time() - 2 * interval
    assert m._wants_lists() is True

    m.last_lists_sync = time.time()
    m._quiet = False  # Refresh button or a push
    assert m._wants_lists() is True
    m._quiet = True
    repo.create_collection("tasklist", "New")  # a new list waits to be sent
    assert m._wants_lists() is True
