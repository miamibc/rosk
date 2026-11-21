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
