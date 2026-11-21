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


def test_focus_syncs_what_is_older_than_the_active_interval(mgr):
    _repo, m, starts = mgr
    m._idle_seconds = lambda: 0
    now = time.time()
    m.last = {manager.KEEP: now - 3 * 60, manager.TASKS: now - 3 * 60, manager.LISTS: now - 3 * 60}
    assert m.due_parts() == set()  # in other apps: nothing due after 3 minutes
    m.set_active(True)
    run_loop(50)
    assert [q for _t, q in starts] == [True]  # Keep (2 min when active) is due: quietly
    assert m.due_parts() == {manager.KEEP}


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


def test_each_part_has_its_own_clock(mgr):
    _repo, m, _starts = mgr
    idle = [0]
    m._idle_seconds = lambda: idle[0]
    m.active = False
    now = time.time()
    m.last = {manager.KEEP: now - 11 * 60, manager.TASKS: now - 11 * 60, manager.LISTS: now - 11 * 60}
    assert m.due_parts() == {manager.KEEP}  # notes after 10 min, tasks only after 15
    m.last[manager.TASKS] = now - 16 * 60
    assert m.due_parts() == {manager.KEEP, manager.TASKS}  # the lists themselves wait for 30
    m.last[manager.LISTS] = now - 31 * 60
    assert manager.LISTS in m.due_parts()

    idle[0] = manager.AWAY_IDLE  # away: an hour for both, two for the lists
    assert m.due_parts() == set()
    m.locked, idle[0] = False, 0  # back: what is older than the "other apps" clocks is due
    assert m.due_parts() == {manager.KEEP, manager.TASKS, manager.LISTS}


def test_changes_sync_only_their_part(mgr):
    repo, m, _starts = mgr
    m._idle_seconds = lambda: 0
    now = time.time()
    m.last = {p: now for p in m.last}
    m._force = False
    assert m._choose_parts() == set()  # nothing due, nothing waiting
    repo.quick_add("n: a note")
    assert m._choose_parts() == {manager.KEEP}
    repo.store.conn.execute("DELETE FROM outbox")
    repo.quick_add("a task")  # its list is new too (Inbox), so the lists are re-read
    assert manager.TASKS in m._choose_parts()
    m._force = True  # Refresh button
    assert m._choose_parts() == {manager.KEEP, manager.TASKS, manager.LISTS}


def test_skipped_tasks_keep_their_status(mgr, monkeypatch, tmp_path):
    _repo, m, _starts = mgr
    monkeypatch.setenv("ROSK_DATA_DIR", str(tmp_path / "data"))  # _run opens config.db_path()
    m._sync_tasks = lambda store, lists=True: (_ for _ in ()).throw(AssertionError("Tasks called"))
    m._sync_keep = lambda store: None
    m._tasks_state = (manager.NO_ACCOUNT, "no account")
    got = []
    monkeypatch.setattr(GLib, "idle_add", lambda fn, *a: got.append(a))
    m._run({manager.KEEP})
    assert got[0][:2] == (manager.NO_ACCOUNT, "no account")
