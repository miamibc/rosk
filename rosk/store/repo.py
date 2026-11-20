"""Domain operations used by the UI and the D-Bus service.

All writes go through here: they update the local store and queue the change
for synchronization in the same transaction.
"""

import json
from datetime import date

from ..core import quickparse
from ..i18n import _
from .db import Store, now_iso

TASK_MOVE_FIELDS = {"collection_id", "parent_id", "position"}


def _pos_int(p):
    try:
        return int(p)
    except (TypeError, ValueError):
        return None


def position_between(before, after):
    """Local estimate of a Google position string between two neighbours."""
    lo = _pos_int(before)
    hi = _pos_int(after)
    if lo is None and hi is None:
        return "00000000000000000000"
    if lo is None:
        lo = (hi or 0) - 2
    if hi is None:
        hi = lo + 2 ** 20
    mid = max(0, (lo + hi) // 2)
    return f"{mid:020d}"


class Repo:
    def __init__(self, path):
        self.store = Store(path)
        self._listeners = []

    # change notification
    def connect_changed(self, callback):
        self._listeners.append(callback)

    def notify(self, what="items"):
        for cb in list(self._listeners):
            cb(what)

    # ---------- queries ----------
    def tasklists(self):
        return self.store.collections("tasklist")

    def labels(self):
        return self.store.collections("label")

    def default_tasklist(self):
        cid = self.store.get_meta("default_list")
        col = self.store.collection(cid) if cid else None
        if col and not col["deleted"]:
            return col
        lists = self.tasklists()
        if lists:
            return lists[0]
        with self.store.tx():
            cid = self.store.insert_collection(kind="tasklist", title=_("My Tasks"))
            self.store.enqueue("collection", cid, "create")
        return self.store.collection(cid)

    def item(self, iid):
        return self.store.item(iid)

    def children(self, iid):
        return self.store.children(iid)

    def tasks_in(self, collection_id, done=False):
        status = "done" if done else "open"
        return self.store.items("kind='task' AND collection_id=? AND status=?",
                                (collection_id, status))

    def today_tasks(self, today=None):
        today = (today or date.today()).isoformat()
        return self.store.items("kind='task' AND status='open' AND due_date IS NOT NULL AND due_date<=?",
                                (today,), order="due_date, position, created_at DESC")

    def upcoming_tasks(self, today=None):
        today = (today or date.today()).isoformat()
        return self.store.items("kind='task' AND status='open' AND due_date IS NOT NULL AND due_date>?",
                                (today,), order="due_date, position, created_at DESC")

    def completed_tasks(self, limit=300):
        return self.store.items("kind='task' AND status='done' AND hidden=0",
                                order=f"completed_at DESC LIMIT {int(limit)}")

    def notes(self, label_id=None, archived=False):
        rows = self.store.items("kind='note' AND archived=?", (1 if archived else 0,),
                                order="pinned DESC, updated_at DESC")
        if label_id:
            rows = [r for r in rows if label_id in json.loads(r["labels_json"] or "[]")]
        return rows

    def search(self, text):
        return self.store.search(text)

    def counts(self, today=None):
        today = (today or date.today()).isoformat()
        q = self.store.conn.execute
        due = q("SELECT count(*) FROM item WHERE kind='task' AND status='open' AND deleted=0 "
                "AND due_date IS NOT NULL AND due_date<=?", (today,)).fetchone()[0]
        overdue = q("SELECT count(*) FROM item WHERE kind='task' AND status='open' AND deleted=0 "
                    "AND due_date IS NOT NULL AND due_date<?", (today,)).fetchone()[0]
        upcoming = q("SELECT count(*) FROM item WHERE kind='task' AND status='open' AND deleted=0 "
                     "AND due_date>?", (today,)).fetchone()[0]
        per_list = {r[0]: r[1] for r in q(
            "SELECT collection_id, count(*) FROM item WHERE kind='task' AND status='open' "
            "AND deleted=0 AND parent_id IS NULL GROUP BY collection_id")}
        notes = q("SELECT count(*) FROM item WHERE kind='note' AND deleted=0 AND archived=0").fetchone()[0]
        return {"today": due, "overdue": overdue, "upcoming": upcoming,
                "lists": per_list, "notes": notes}

    # ---------- collections ----------
    def create_collection(self, kind, title):
        with self.store.tx():
            cid = self.store.insert_collection(kind=kind, title=title.strip(),
                                               position=len(self.store.collections(kind)))
            self.store.enqueue("collection", cid, "create")
        self.notify("collections")
        return cid

    def rename_collection(self, cid, title):
        with self.store.tx():
            self.store.update_collection(cid, title=title.strip())
            self.store.enqueue("collection", cid, "update")
        self.notify("collections")

    def delete_collection(self, cid):
        with self.store.tx():
            col = self.store.collection(cid)
            if col["kind"] == "label":
                for note in self.store.items("kind='note' AND labels_json LIKE ?", (f'%"{cid}"%',)):
                    labels = [x for x in json.loads(note["labels_json"]) if x != cid]
                    self.store.update_item(note["id"], labels_json=json.dumps(labels))
                    self.store.enqueue("item", note["id"], "update")
            else:
                # tasks disappear together with the list on Google
                for row in self.store.items("collection_id=?", (cid,)):
                    self.store.update_item(row["id"], deleted=1)
                    self.store.conn.execute("DELETE FROM outbox WHERE target='item' AND ref_id=?",
                                            (row["id"],))
            if self.store.enqueue("collection", cid, "delete") == "forget":
                self.store.purge_collection(cid)
            else:
                self.store.update_collection(cid, deleted=1)
        self.notify("collections")

    def set_default_tasklist(self, cid):
        with self.store.tx():
            self.store.set_meta("default_list", cid)

    def ensure_collection(self, kind, title):
        col = self.store.collection_by_title(kind, title)
        if col:
            return col["id"]
        return self.create_collection(kind, title)

    # ---------- items ----------
    def _siblings(self, collection_id, parent_id):
        if parent_id:
            return self.store.children(parent_id)
        return self.store.items("kind='task' AND collection_id=? AND parent_id IS NULL",
                                (collection_id,))

    def create_task(self, title, collection_id=None, due=None, parent_id=None, body="",
                    after_id=None, notify=True):
        """New tasks go on top (like Google), or right after `after_id`."""
        if parent_id:
            parent = self.store.item(parent_id)
            collection_id = parent["collection_id"]
        collection_id = collection_id or self.default_tasklist()["id"]
        sibs = self._siblings(collection_id, parent_id)
        if after_id:
            ids = [x["id"] for x in sibs]
            i = ids.index(after_id) if after_id in ids else len(ids) - 1
            nxt = sibs[i + 1]["position"] if i + 1 < len(sibs) else None
            position = position_between(sibs[i]["position"] if sibs else None, nxt)
            if nxt is None and sibs:
                position = position_between(sibs[i]["position"], None)
        else:
            position = position_between(None, sibs[0]["position"] if sibs else None)
        with self.store.tx():
            iid = self.store.insert_item(kind="task", title=title.strip(), body=body or "",
                                         collection_id=collection_id, parent_id=parent_id,
                                         position=position,
                                         due_date=due.isoformat() if isinstance(due, date) else due)
            self.store.enqueue("item", iid, "create")
        if notify:
            self.notify()
        return iid

    def create_note(self, title="", body="", checklist=None, labels=(), color="DEFAULT",
                    notify=True):
        with self.store.tx():
            iid = self.store.insert_item(
                kind="note", title=title.strip(), body=body or "",
                checklist_json=json.dumps(checklist, ensure_ascii=False) if checklist is not None else None,
                labels_json=json.dumps(list(labels)), color=color)
            self.store.enqueue("item", iid, "create")
        if notify:
            self.notify()
        return iid

    def quick_add(self, text, collection_id=None, ignore=()):
        """Creates a task or a note from a quick-capture line. Returns the new item id."""
        r = quickparse.parse(text, ignore=ignore)
        if not r.title and not r.subtasks:
            return None
        title = r.title or (r.subtasks[0] if r.subtasks else "")
        if r.kind == quickparse.NOTE:
            labels = [self.ensure_collection("label", r.list_name)] if r.list_name else []
            checklist = None
            if r.subtasks:
                checklist = [{"text": s, "checked": False} for s in r.subtasks]
            iid = self.create_note(title=title, checklist=checklist, labels=labels, notify=False)
        else:
            if r.list_name:
                collection_id = self.ensure_collection("tasklist", r.list_name)
            iid = self.create_task(title, collection_id=collection_id, due=r.due, notify=False)
            prev = None
            for sub in r.subtasks:
                prev = self.create_task(sub, parent_id=iid, after_id=prev, notify=False)
        self.notify()
        return iid

    def update(self, iid, **fields):
        item = self.store.item(iid)
        if item is None:
            return
        changed = {k: v for k, v in fields.items() if item.get(k) != v}
        if not changed:
            return
        with self.store.tx():
            self.store.update_item(iid, **changed)
            if item["kind"] == "task" and TASK_MOVE_FIELDS & changed.keys():
                self.store.enqueue("item", iid, "move")
                if "collection_id" in changed:
                    for child in self.store.children(iid):
                        self.store.update_item(child["id"], touch=False,
                                               collection_id=changed["collection_id"])
            if changed.keys() - TASK_MOVE_FIELDS or item["kind"] == "note":
                self.store.enqueue("item", iid, "update")
        self.notify()

    def set_done(self, iid, done=True):
        with self.store.tx():
            ts = now_iso() if done else None
            status = "done" if done else "open"
            self.store.update_item(iid, status=status, completed_at=ts)
            self.store.enqueue("item", iid, "update")
            if done:
                for child in self.store.children(iid):
                    if child["status"] != "done":
                        self.store.update_item(child["id"], status="done", completed_at=ts)
                        self.store.enqueue("item", child["id"], "update")
        self.notify()

    def delete(self, iid):
        with self.store.tx():
            self._delete(iid)
        self.notify()

    def _delete(self, iid):
        for child in self.store.children(iid):
            self._delete(child["id"])
        if self.store.enqueue("item", iid, "delete") == "forget":
            self.store.purge_item(iid)
        else:
            self.store.update_item(iid, deleted=1)

    def clear_completed(self, collection_id=None):
        """Deletes completed tasks (all lists or one)."""
        with self.store.tx():
            where = "kind='task' AND status='done'"
            args = ()
            if collection_id:
                where += " AND collection_id=?"
                args = (collection_id,)
            for row in self.store.items(where, args):
                if self.store.item(row["id"]) and not self.store.item(row["id"])["deleted"]:
                    self._delete(row["id"])
        self.notify()

    def move(self, iid, collection_id=None, parent_id=None, before_id=None, after_id=None):
        """Places a task after `after_id` (or before `before_id`) among the siblings."""
        item = self.store.item(iid)
        collection_id = collection_id or item["collection_id"]
        before = self.store.item(before_id) if before_id else None
        after = self.store.item(after_id) if after_id else None
        pos = position_between(after["position"] if after else None,
                               before["position"] if before else None)
        self.update(iid, collection_id=collection_id, parent_id=parent_id, position=pos)

    def convert(self, iid):
        """Task -> note or note -> task: create in the other service, delete the original."""
        item = self.store.item(iid)
        if item["kind"] == "task":
            subs = self.store.children(iid)
            checklist = [{"text": s["title"], "checked": s["status"] == "done"} for s in subs] or None
            new = self.create_note(title=item["title"], body=item["body"], checklist=checklist,
                                   notify=False)
        else:
            new = self.create_task(item["title"] or _("Untitled"), body=item["body"], notify=False)
            prev = None
            for entry in json.loads(item["checklist_json"] or "[]"):
                prev = self.create_task(entry.get("text", ""), parent_id=new, after_id=prev,
                                        notify=False)
                if entry.get("checked"):
                    self.set_done(prev)
        self.delete(iid)
        return new
