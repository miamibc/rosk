import json

import gkeepapi
import pytest

from rosk.store.repo import Repo
from rosk.sync.keep_sync import KeepSync


@pytest.fixture
def env(tmp_path):
    keep = gkeepapi.Keep()
    keep.sync = lambda resync=False: None
    keep.dump = lambda: {}
    repo = Repo(tmp_path / "k.db")
    ks = KeepSync(repo.store, "me@example.com", "token", tmp_path / "state.json")
    ks.keep = keep
    return keep, repo, ks


def notes(repo):
    return repo.store.items("kind='note'")


def test_pull_notes_and_labels(env):
    keep, repo, ks = env
    label = keep.createLabel("ideas")
    n = keep.createNote("Title", "Body")
    n.labels.add(label)
    n.pinned = True
    keep.createList("Shop", [("milk", False), ("bread", True)])
    ks.sync()
    rows = {r["title"]: r for r in notes(repo)}
    assert rows["Title"]["body"] == "Body" and rows["Title"]["pinned"] == 1
    col = repo.store.collection_by_title("label", "ideas")
    assert json.loads(rows["Title"]["labels_json"]) == [col["id"]]
    assert json.loads(rows["Shop"]["checklist_json"]) == [
        {"text": "milk", "checked": False}, {"text": "bread", "checked": True}]
    assert repo.store.pending_count() == 0


def test_local_note_pushed(env):
    keep, repo, ks = env
    ks.sync()
    iid = repo.quick_add("n: Поездка: - билеты - отель #отпуск")
    ks.sync()
    node = keep.get(repo.item(iid)["remote_id"])
    assert isinstance(node, gkeepapi.node.List)
    assert [i.text for i in sorted(node.items, key=lambda i: -int(i.sort))] == ["билеты", "отель"]
    assert [l.name for l in node.labels.all()] == ["отпуск"]
    assert repo.store.pending_count() == 0


def test_new_notes_go_on_top(env):
    keep, repo, ks = env
    for i in range(5):
        keep.createNote(f"old {i}", "").sort = 9_000_000_000 - i
    ks.sync()
    first = repo.quick_add("n: first")
    second = repo.quick_add("n: second")
    ks.sync()
    order = [n.title for n in sorted(keep.all(), key=lambda n: -n.sort)]
    assert order[:3] == ["second", "first", "old 0"]
    assert repo.item(first)["remote_id"] and repo.item(second)["remote_id"]


def test_local_edit_and_remote_edit_merge(env):
    keep, repo, ks = env
    n = keep.createNote("T", "B")
    ks.sync()
    local = repo.store.item_by_remote("note", n.id)
    repo.update(local["id"], body="local body")
    n.title = "remote title"
    ks.sync()
    assert n.title == "remote title" and n.text == "local body"
    row = repo.item(local["id"])
    assert row["title"] == "remote title" and row["body"] == "local body"


def test_checklist_edit(env):
    keep, repo, ks = env
    l = keep.createList("L", [("a", False), ("b", False)])
    ks.sync()
    row = repo.store.item_by_remote("note", l.id)
    repo.update(row["id"], checklist_json=json.dumps([{"text": "a", "checked": True},
                                                      {"text": "c", "checked": False}]))
    ks.sync()
    items = sorted(l.items, key=lambda i: -int(i.sort))
    assert [(i.text, i.checked) for i in items] == [("a", True), ("c", False)]


def test_delete_trashes(env):
    keep, repo, ks = env
    n = keep.createNote("T", "B")
    ks.sync()
    repo.delete(repo.store.item_by_remote("note", n.id)["id"])
    ks.sync()
    assert n.trashed
    assert notes(repo) == []


def test_remote_trash_removes_local(env):
    keep, repo, ks = env
    n = keep.createNote("T", "B")
    ks.sync()
    n.trash()
    ks.sync()
    assert notes(repo) == []


def test_tasks_untouched_by_keep(env):
    keep, repo, ks = env
    repo.quick_add("a task")
    ks.sync()
    assert len(keep.all()) == 0
    assert repo.store.pending_count() == 2  # list create + task create stay for Tasks sync
