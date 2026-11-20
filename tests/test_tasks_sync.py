import pytest

from rosk.store.repo import Repo
from rosk.sync.tasks_sync import TasksSync

from fake_google import FakeTasks


@pytest.fixture
def env(tmp_path):
    g = FakeTasks()
    work = g.add_list("Work")
    g.add_list("My Tasks")
    repo = Repo(tmp_path / "t.db")
    sync = TasksSync(repo.store, g)
    return g, repo, sync, work


def titles(repo, where="kind='task'"):
    return sorted(r["title"] for r in repo.store.items(where))


def test_initial_pull(env):
    g, repo, sync, work = env
    p = g.add_task(work, "parent", due="2026-11-26T00:00:00.000Z",
                   links=[{"type": "email", "description": "mail", "link": "x"}])
    g.add_task(work, "child", parent=p)
    sync.sync()
    assert titles(repo) == ["child", "parent"]
    parent = repo.store.item_by_remote("task", p)
    assert parent["due_date"] == "2026-11-26"
    assert repo.children(parent["id"])[0]["title"] == "child"
    assert repo.store.pending_count() == 0
    assert not [c for c in g.calls if c[0] != "list"]  # nothing pushed on first sync


def test_local_create_and_complete(env):
    g, repo, sync, work = env
    sync.sync()
    col = repo.store.collection_by_remote("tasklist", work)
    iid = repo.quick_add("Call bank tomorrow #Work: - find number - call")
    sync.sync()
    remote = [t for t in g.tasks.values() if t["title"] == "Call bank tomorrow" or t["title"] == "Call bank"]
    assert remote and remote[0]["_list"] == work and remote[0].get("due")
    kids = [t for t in g.tasks.values() if t.get("parent") == remote[0]["id"]]
    assert sorted(k["title"] for k in kids) == ["call", "find number"]
    repo.set_done(iid)
    sync.sync()
    assert g.tasks[remote[0]["id"]]["status"] == "completed"
    assert repo.store.pending_count() == 0
    assert repo.item(iid)["collection_id"] == col["id"]


def test_remote_change_applies(env):
    g, repo, sync, work = env
    t = g.add_task(work, "a")
    sync.sync()
    g.edit(t, title="a2", notes="n")
    sync.sync()
    it = repo.store.item_by_remote("task", t)
    assert it["title"] == "a2" and it["body"] == "n"


def test_field_merge(env):
    g, repo, sync, work = env
    t = g.add_task(work, "a")
    sync.sync()
    it = repo.store.item_by_remote("task", t)
    repo.update(it["id"], body="local note")
    g.edit(t, title="remote title")
    sync.sync()
    it = repo.item(it["id"])
    assert it["title"] == "remote title" and it["body"] == "local note"
    assert g.tasks[t]["notes"] == "local note" and g.tasks[t]["title"] == "remote title"


def test_done_wins_conflict(env):
    g, repo, sync, work = env
    t = g.add_task(work, "a")
    sync.sync()
    it = repo.store.item_by_remote("task", t)
    repo.update(it["id"], title="local")
    repo.set_done(it["id"])
    g.edit(t, title="remote", status="needsAction")
    sync.sync()
    assert repo.item(it["id"])["status"] == "done"
    assert g.tasks[t]["status"] == "completed"


def test_remote_delete(env):
    g, repo, sync, work = env
    t = g.add_task(work, "a")
    sync.sync()
    g.remove(t)
    sync.sync()
    assert titles(repo) == []


def test_remote_delete_vs_local_edit_recreates(env):
    g, repo, sync, work = env
    t = g.add_task(work, "a")
    sync.sync()
    it = repo.store.item_by_remote("task", t)
    repo.update(it["id"], title="keep me")
    g.remove(t)
    sync.sync()
    it2 = repo.item(it["id"])
    assert it2 and it2["title"] == "keep me" and it2["remote_id"] != t
    assert g.tasks[it2["remote_id"]]["title"] == "keep me"


def test_local_delete(env):
    g, repo, sync, work = env
    t = g.add_task(work, "a")
    sync.sync()
    repo.delete(repo.store.item_by_remote("task", t)["id"])
    sync.sync()
    assert g.tasks[t].get("deleted")
    assert repo.store.item_by_remote("task", t) is None


def test_create_then_delete_offline_never_reaches_google(env):
    g, repo, sync, work = env
    sync.sync()
    iid = repo.quick_add("temp #Work")
    repo.delete(iid)
    sync.sync()
    assert not [c for c in g.calls if c[0] == "insert_task"]


def test_move_between_lists(env):
    g, repo, sync, work = env
    t = g.add_task(work, "a")
    sync.sync()
    other = repo.store.collection_by_title("tasklist", "My Tasks")
    it = repo.store.item_by_remote("task", t)
    repo.update(it["id"], collection_id=other["id"])
    sync.sync()
    assert g.tasks[t]["_list"] == other["remote_id"]
    assert repo.item(it["id"])["collection_id"] == other["id"]


def test_new_list_offline_then_adopted(env):
    g, repo, sync, work = env
    # never synced: default list is created locally, then matched by name
    iid = repo.quick_add("offline task")
    assert repo.item(iid)["collection_id"]
    sync.sync()
    assert ("insert_tasklist", "My Tasks") not in g.calls
    assert [t["title"] for t in g.tasks.values()] == ["offline task"]


def test_quick_add_new_list(env):
    g, repo, sync, work = env
    sync.sync()
    repo.quick_add("buy milk #Home")
    sync.sync()
    home = [l for l in g.lists.values() if l["title"] == "Home"]
    assert home
    assert [t["_list"] for t in g.tasks.values()] == [home[0]["id"]]
