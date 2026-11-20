"""Synchronization of task lists and tasks with Google Tasks.

Cycle: pull lists -> pull tasks (incremental) -> merge -> push the outbox.
Only items of kind 'task' and collections of kind 'tasklist' are touched here.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from ..core.dates import from_google, to_google
from ..store.db import TASK_SYNC_FIELDS, now_iso
from .http import HttpError
from .merge import done_wins, three_way

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _status(remote):
    return "done" if remote.get("status") == "completed" else "open"


class TasksSync:
    def __init__(self, store, client):
        self.store = store
        self.client = client

    # ---------- helpers ----------
    def _remote_fields(self, r, collection_id):
        parent = None
        if r.get("parent"):
            p = self.store.item_by_remote("task", r["parent"])
            parent = p["id"] if p else None
        return {
            "title": r.get("title", ""),
            "body": r.get("notes", "") or "",
            "status": _status(r),
            "due_date": from_google(r.get("due")),
            "completed_at": r.get("completed") if _status(r) == "done" else None,
            "collection_id": collection_id,
            "parent_id": parent,
        }

    @staticmethod
    def _extra(r):
        return {
            "remote_updated": r.get("updated"),
            "position": r.get("position", ""),
            "links_json": json.dumps(r.get("links", []), ensure_ascii=False),
            "web_view_link": r.get("webViewLink"),
            "assignment_json": json.dumps(r["assignmentInfo"], ensure_ascii=False)
            if r.get("assignmentInfo") else None,
            "hidden": 1 if r.get("hidden") else 0,
        }

    @staticmethod
    def _body(item):
        return {
            "title": item["title"],
            "notes": item["body"] or "",
            "status": "completed" if item["status"] == "done" else "needsAction",
            "due": to_google(item["due_date"]),
        }

    def _list_remote(self, collection_id):
        col = self.store.collection(collection_id) if collection_id else None
        return col["remote_id"] if col else None

    # ---------- pull ----------
    def pull_lists(self):
        remote = self.client.list_tasklists()
        seen = set()
        with self.store.tx():
            for i, rl in enumerate(remote):
                seen.add(rl["id"])
                local = self.store.collection_by_remote("tasklist", rl["id"])
                if local is None:
                    # adopt a local list with the same name that was never pushed
                    for cand in self.store.collections("tasklist"):
                        if cand["remote_id"] is None and cand["title"].lower() == rl["title"].lower():
                            local = cand
                            self.store.conn.execute(
                                "DELETE FROM outbox WHERE target='collection' AND ref_id=? AND op='create'",
                                (cand["id"],))
                            self.store.update_collection(cand["id"], remote_id=rl["id"])
                            break
                if local is None:
                    self.store.insert_collection(kind="tasklist", remote_id=rl["id"],
                                                 title=rl["title"], position=i,
                                                 base_json=json.dumps({"title": rl["title"]}))
                    continue
                ops = self.store.pending_ops("collection", local["id"])
                fields = {"position": i, "base_json": json.dumps({"title": rl["title"]})}
                if "update" not in ops:
                    fields["title"] = rl["title"]
                self.store.update_collection(local["id"], **fields)
            for local in self.store.collections("tasklist", include_deleted=True):
                if local["remote_id"] and local["remote_id"] not in seen:
                    self.store.purge_collection(local["id"])

    def pull_tasks(self, collection):
        key = f"tasks_cursor:{collection['id']}"
        cursor = self.store.get_meta(key)
        started = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        remote = self.client.list_tasks(collection["remote_id"], updated_min=cursor)
        # parents before children
        remote.sort(key=lambda r: (r.get("parent") is not None, r.get("position", "")))
        with self.store.tx():
            for r in remote:
                self._apply(r, collection["id"])
            if cursor is None:
                # full listing: anything we know in this list that Google did not return is gone
                ids = {r["id"] for r in remote}
                for row in self.store.conn.execute(
                        "SELECT id, remote_id FROM item WHERE kind='task' AND collection_id=? "
                        "AND remote_id IS NOT NULL", (collection["id"],)).fetchall():
                    if row["remote_id"] not in ids and not self.store.pending_ops("item", row["id"]):
                        self.store.purge_item(row["id"])
            self.store.set_meta(key, started)
        return len(remote)

    def _apply(self, r, collection_id):
        local = self.store.item_by_remote("task", r["id"])
        if r.get("deleted"):
            if local is None:
                return
            ops = self.store.pending_ops("item", local["id"])
            if not ops or ops == ["delete"] or local["deleted"]:
                self.store.purge_item(local["id"])
            else:
                # edited here, deleted there: keep the edit, recreate on Google
                self.store.conn.execute("DELETE FROM outbox WHERE target='item' AND ref_id=?",
                                        (local["id"],))
                self.store.update_item(local["id"], touch=False, remote_id=None, base_json=None)
                self.store.enqueue("item", local["id"], "create")
            return

        rf = self._remote_fields(r, collection_id)
        extra = self._extra(r)
        if local is None:
            self.store.insert_item(kind="task", remote_id=r["id"], base_json=json.dumps(rf),
                                   created_at=r.get("updated") or now_iso(),
                                   updated_at=r.get("updated") or now_iso(), **rf, **extra)
            return
        ops = self.store.pending_ops("item", local["id"])
        if local["deleted"] and "delete" in ops:
            return
        if not ops:
            self.store.update_item(local["id"], touch=False, base_json=json.dumps(rf), deleted=0,
                                   **rf, **extra)
            return
        base = json.loads(local["base_json"] or "{}")
        remote_newer = (r.get("updated") or "") > (local["updated_at"] or "")
        changes, conflicts = three_way(local, base, rf, TASK_SYNC_FIELDS, remote_newer,
                                       prefer={"status": done_wins})
        if conflicts:
            self.store.remember(local["id"], "conflict", {"local": {f: l for f, l, _ in conflicts},
                                                          "remote": {f: v for f, _, v in conflicts}})
        if "move" in ops:
            extra.pop("position", None)
        if changes.get("status") == "done" and not local["completed_at"]:
            changes.setdefault("completed_at", rf["completed_at"])
        self.store.update_item(local["id"], touch=False, base_json=json.dumps(rf), **changes, **extra)
        merged = {**local, **changes}
        if all(merged.get(f) == rf.get(f) for f in TASK_SYNC_FIELDS):
            self.store.conn.execute(
                "DELETE FROM outbox WHERE target='item' AND ref_id=? AND op IN ('update','move')",
                (local["id"],))

    # ---------- push ----------
    def push(self):
        """Sends queued changes. Raises HttpError if the network/server is unavailable."""
        sent = 0
        for op in self.store.outbox("collection"):
            col = self.store.collection(op["ref_id"])
            if col is None or col["kind"] != "tasklist":
                continue
            if op["attempts"] >= MAX_ATTEMPTS:
                continue
            sent += self._run(op, lambda: self._push_collection(op, col))
        for op in self.store.outbox("item"):
            item = self.store.item(op["ref_id"])
            if item is None:
                with self.store.tx():
                    self.store.dequeue(op["seq"])
                continue
            if item["kind"] != "task" or op["attempts"] >= MAX_ATTEMPTS:
                continue
            sent += self._run(op, lambda: self._push_item(op, item))
        return sent

    def _run(self, op, fn):
        try:
            return 1 if fn() else 0
        except HttpError as e:
            if e.retryable:
                raise
            log.warning("sync op %s %s failed: %s", op["op"], op["ref_id"], e)
            with self.store.tx():
                self.store.fail(op["seq"], e)
            return 0

    def _push_collection(self, op, col):
        kind = op["op"]
        if kind == "create":
            if col["remote_id"]:
                with self.store.tx():
                    self.store.dequeue(op["seq"])
                return False
            r = self.client.insert_tasklist(col["title"])
            with self.store.tx():
                self.store.update_collection(col["id"], remote_id=r["id"],
                                             base_json=json.dumps({"title": r["title"]}))
                self.store.dequeue(op["seq"])
                self.store.set_meta(f"tasks_cursor:{col['id']}", None)
        elif kind == "update":
            if not col["remote_id"]:
                return False
            self.client.patch_tasklist(col["remote_id"], col["title"])
            with self.store.tx():
                self.store.update_collection(col["id"], base_json=json.dumps({"title": col["title"]}))
                self.store.dequeue(op["seq"])
        elif kind == "delete":
            if col["remote_id"]:
                try:
                    self.client.delete_tasklist(col["remote_id"])
                except HttpError as e:
                    if e.status != 404:
                        raise
            with self.store.tx():
                self.store.purge_collection(col["id"])
        return True

    def _push_item(self, op, item):
        kind = op["op"]
        base = json.loads(item["base_json"] or "{}")
        if kind == "create":
            if item["remote_id"]:
                with self.store.tx():
                    self.store.dequeue(op["seq"])
                return False
            list_remote = self._list_remote(item["collection_id"])
            if not list_remote:
                return False
            parent_remote = None
            if item["parent_id"]:
                parent = self.store.item(item["parent_id"])
                parent_remote = parent["remote_id"] if parent else None
                if not parent_remote:
                    return False
            r = self.client.insert_task(list_remote, self._body(item), parent=parent_remote,
                                        previous=self._previous_sibling_remote(item))
            self._after_write(op, item, r, item["collection_id"])
            return True

        if not item["remote_id"]:
            return False  # waits for its create
        list_remote = self._list_remote(base.get("collection_id") or item["collection_id"])
        if not list_remote:
            return False
        try:
            if kind == "update":
                r = self.client.patch_task(list_remote, item["remote_id"], self._body(item))
                self._after_write(op, item, r, base.get("collection_id") or item["collection_id"])
            elif kind == "move":
                dest = self._list_remote(item["collection_id"])
                parent_remote = None
                if item["parent_id"]:
                    parent = self.store.item(item["parent_id"])
                    parent_remote = parent["remote_id"] if parent else None
                    if not parent_remote:
                        return False
                previous = self._previous_sibling_remote(item)
                r = self.client.move_task(list_remote, item["remote_id"], parent=parent_remote,
                                          previous=previous,
                                          destination=dest if dest != list_remote else None)
                self._after_write(op, item, r, item["collection_id"], moved=True)
            elif kind == "delete":
                try:
                    self.client.delete_task(list_remote, item["remote_id"])
                except HttpError as e:
                    if e.status != 404:
                        raise
                with self.store.tx():
                    self.store.purge_item(item["id"])
        except HttpError as e:
            if e.status == 404 and kind in ("update", "move"):
                # gone on Google while we edited it: recreate
                with self.store.tx():
                    self.store.conn.execute("DELETE FROM outbox WHERE target='item' AND ref_id=?",
                                            (item["id"],))
                    self.store.update_item(item["id"], touch=False, remote_id=None, base_json=None)
                    self.store.enqueue("item", item["id"], "create")
                return False
            raise
        return True

    def _previous_sibling_remote(self, item):
        if item["parent_id"]:
            sibs = self.store.children(item["parent_id"])
        else:
            sibs = self.store.items("kind='task' AND collection_id=? AND parent_id IS NULL",
                                    (item["collection_id"],))
        prev = None
        for s in sibs:
            if s["id"] == item["id"]:
                break
            prev = s
        return prev["remote_id"] if prev else None

    def _after_write(self, op, item, r, collection_id, moved=False):
        with self.store.tx():
            current = self.store.item(item["id"])
            rf = self._remote_fields(r, collection_id)
            fields = {"remote_id": r["id"], "base_json": json.dumps(rf), **self._extra(r)}
            self.store.update_item(item["id"], touch=False, **fields)
            self.store.dequeue(op["seq"])
            if current and current["updated_at"] != item["updated_at"]:
                # edited while the request was in flight: send again
                self.store.enqueue("item", item["id"], "move" if moved else "update")

    # ---------- full cycle ----------
    def sync(self):
        self.pull_lists()
        for col in self.store.collections("tasklist"):
            if col["remote_id"]:
                self.pull_tasks(col)
        pushed = self.push()
        if pushed:
            # pick up server-side effects (positions, completed children)
            for col in self.store.collections("tasklist"):
                if col["remote_id"]:
                    self.pull_tasks(col)
        return pushed
