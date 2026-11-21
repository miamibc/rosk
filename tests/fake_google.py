"""In-memory imitation of the Google Tasks API for tests."""

import itertools
from datetime import datetime, timezone

from rosk.sync.http import HttpError

_ids = itertools.count(1)
_clock = itertools.count(1)


def _ts():
    # strictly increasing timestamps
    n = next(_clock)
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{n % 1000:03d}Z"


class FakeTasks:
    def __init__(self):
        self.lists = {}   # id -> {"id","title"}
        self.tasks = {}   # id -> task dict incl. "_list"
        self.calls = []

    def _new_id(self, prefix):
        return f"{prefix}{next(_ids)}"

    def add_list(self, title):
        lid = self._new_id("L")
        self.lists[lid] = {"id": lid, "title": title}
        return lid

    def add_task(self, lid, title, **kw):
        tid = self._new_id("T")
        t = {"id": tid, "title": title, "status": "needsAction", "updated": _ts(),
             "position": f"{len(self.tasks):020d}", "_list": lid, **kw}
        self.tasks[tid] = t
        return tid

    def edit(self, tid, **kw):
        self.tasks[tid].update(kw, updated=_ts())

    def remove(self, tid):
        self.tasks[tid].update(deleted=True, updated=_ts())

    def _pub(self, t):
        return {k: v for k, v in t.items() if not k.startswith("_")}

    # API
    def list_tasklists(self):
        return [dict(v) for v in self.lists.values()]

    def insert_tasklist(self, title):
        self.calls.append(("insert_tasklist", title))
        lid = self.add_list(title)
        return dict(self.lists[lid])

    def patch_tasklist(self, lid, title):
        self.lists[lid]["title"] = title
        return dict(self.lists[lid])

    def delete_tasklist(self, lid):
        self.lists.pop(lid, None)

    def list_tasks(self, lid, updated_min=None):
        if lid not in self.lists:
            raise HttpError(404, "Task list not found")  # like Google for a deleted list
        out = []
        for t in self.tasks.values():
            if t["_list"] != lid:
                continue
            if updated_min is None and t.get("deleted"):
                continue
            out.append(self._pub(t))
        return out

    def insert_task(self, lid, body, parent=None, previous=None):
        self.calls.append(("insert_task", body["title"]))
        tid = self.add_task(lid, body["title"], notes=body.get("notes", ""),
                            status=body.get("status", "needsAction"))
        if body.get("due"):
            self.tasks[tid]["due"] = body["due"]
        if parent:
            self.tasks[tid]["parent"] = parent
        return self._pub(self.tasks[tid])

    def patch_task(self, lid, tid, body):
        self.calls.append(("patch_task", body["title"]))
        t = self.tasks.get(tid)
        if not t or t.get("deleted"):
            raise HttpError(404, "not found")
        for k, v in body.items():
            if v is None:
                t.pop(k, None)
            else:
                t[k] = v
        if t["status"] == "completed":
            t.setdefault("completed", _ts())
        else:
            t.pop("completed", None)
        t["updated"] = _ts()
        return self._pub(t)

    def delete_task(self, lid, tid):
        self.calls.append(("delete_task", tid))
        if tid in self.tasks:
            self.remove(tid)

    def move_task(self, lid, tid, parent=None, previous=None, destination=None):
        self.calls.append(("move_task", tid, destination))
        t = self.tasks[tid]
        if destination:
            t["_list"] = destination
        if parent:
            t["parent"] = parent
        else:
            t.pop("parent", None)
        t["updated"] = _ts()
        return self._pub(t)
