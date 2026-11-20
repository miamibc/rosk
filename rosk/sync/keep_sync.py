"""Synchronization of notes and labels with Google Keep through gkeepapi (unofficial).

Cycle: keep.sync() to pull -> merge into the local store -> apply queued local
changes to the gkeepapi tree -> keep.sync() to push -> remember the result.
Only items of kind 'note' and collections of kind 'label' are touched here.
"""

import json
import logging

from ..store.db import NOTE_SYNC_FIELDS
from .merge import three_way

log = logging.getLogger(__name__)


class KeepUnavailable(Exception):
    pass


def _load_gkeepapi():
    try:
        import gkeepapi
        return gkeepapi
    except ImportError as e:
        raise KeepUnavailable("gkeepapi is not installed") from e


class KeepSync:
    def __init__(self, store, email, master_token, state_path):
        self.store = store
        self.email = email
        self.master_token = master_token
        self.state_path = state_path
        self.keep = None

    # ---------- session ----------
    def _login(self):
        gkeepapi = _load_gkeepapi()
        keep = gkeepapi.Keep()
        state = None
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text())
            except ValueError:
                state = None
        try:
            keep.authenticate(self.email, self.master_token, state=state, sync=False)
        except Exception as e:
            raise KeepUnavailable(f"Keep login failed: {e}") from e
        self.keep = keep

    def _save_state(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.keep.dump()))
        tmp.replace(self.state_path)

    # ---------- mapping ----------
    def _label_map(self):
        """remote label id -> local collection id"""
        return {c["remote_id"]: c["id"] for c in self.store.collections("label", include_deleted=True)
                if c["remote_id"]}

    def _remote_fields(self, node, labels):
        gkeepapi = _load_gkeepapi()
        is_list = isinstance(node, gkeepapi.node.List)
        checklist = None
        if is_list:
            items = sorted(node.items, key=lambda i: -int(i.sort or 0))
            checklist = json.dumps([{"text": i.text, "checked": bool(i.checked)} for i in items],
                                   ensure_ascii=False)
        label_ids = sorted(labels[l.id] for l in node.labels.all() if l.id in labels)
        return {
            "title": node.title or "",
            "body": "" if is_list else (node.text or ""),
            "checklist_json": checklist,
            "labels_json": json.dumps(label_ids),
            "pinned": 1 if node.pinned else 0,
            "color": node.color.value,
            "archived": 1 if node.archived else 0,
        }

    @staticmethod
    def _updated(node):
        try:
            return node.timestamps.updated.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        except Exception:
            return None

    # ---------- pull ----------
    def _pull(self):
        with self.store.tx():
            self._pull_labels()
            labels = self._label_map()
            seen = set()
            for node in self.keep.all():
                if node.trashed or node.deleted:
                    continue
                seen.add(node.id)
                self._apply(node, labels)
            for row in self.store.conn.execute(
                    "SELECT id, remote_id FROM item WHERE kind='note' AND remote_id IS NOT NULL").fetchall():
                if row["remote_id"] not in seen and not self.store.pending_ops("item", row["id"]):
                    self.store.purge_item(row["id"])

    def _pull_labels(self):
        remote = {l.id: l for l in self.keep.labels()}
        for rid, label in remote.items():
            local = self.store.collection_by_remote("label", rid)
            if local is None:
                local = self.store.collection_by_title("label", label.name)
                if local and local["remote_id"] is None:
                    self.store.conn.execute(
                        "DELETE FROM outbox WHERE target='collection' AND ref_id=? AND op='create'",
                        (local["id"],))
                    self.store.update_collection(local["id"], remote_id=rid)
                    continue
                self.store.insert_collection(kind="label", remote_id=rid, title=label.name)
            elif "update" not in self.store.pending_ops("collection", local["id"]):
                self.store.update_collection(local["id"], title=label.name)
        for local in self.store.collections("label", include_deleted=True):
            if local["remote_id"] and local["remote_id"] not in remote:
                self.store.purge_collection(local["id"])

    def _apply(self, node, labels):
        rf = self._remote_fields(node, labels)
        updated = self._updated(node)
        local = self.store.item_by_remote("note", node.id)
        if local is None:
            stamps = {"created_at": updated, "updated_at": updated} if updated else {}
            self.store.insert_item(kind="note", remote_id=node.id, base_json=json.dumps(rf),
                                   remote_updated=updated, **stamps, **rf)
            return
        ops = self.store.pending_ops("item", local["id"])
        if local["deleted"] and "delete" in ops:
            return
        if not ops:
            self.store.update_item(local["id"], touch=False, base_json=json.dumps(rf),
                                   remote_updated=updated, deleted=0, **rf)
            return
        base = json.loads(local["base_json"] or "{}")
        remote_newer = (updated or "") > (local["updated_at"] or "")
        changes, conflicts = three_way(local, base, rf, NOTE_SYNC_FIELDS, remote_newer)
        if conflicts:
            self.store.remember(local["id"], "conflict", {"local": {f: l for f, l, _ in conflicts},
                                                          "remote": {f: v for f, _, v in conflicts}})
        self.store.update_item(local["id"], touch=False, base_json=json.dumps(rf),
                               remote_updated=updated, **changes)

    # ---------- push ----------
    def _push(self):
        """Applies queued changes to the gkeepapi tree. Returns the outbox entries handled."""
        gkeepapi = _load_gkeepapi()
        handled = []
        label_nodes = {}

        for op in self.store.outbox("collection"):
            col = self.store.collection(op["ref_id"])
            if col is None or col["kind"] != "label":
                continue
            if op["op"] == "create" and not col["remote_id"]:
                existing = self.keep.findLabel(col["title"])
                label = existing or self.keep.createLabel(col["title"])
                label_nodes[col["id"]] = label
                handled.append(("collection", op, col["id"], label.id))
            elif op["op"] == "update" and col["remote_id"]:
                label = self.keep.getLabel(col["remote_id"])
                if label:
                    label.name = col["title"]
                handled.append(("collection", op, col["id"], None))
            elif op["op"] == "delete":
                if col["remote_id"]:
                    try:
                        self.keep.deleteLabel(col["remote_id"])
                    except Exception:
                        pass
                handled.append(("collection-delete", op, col["id"], None))

        def label_node(cid):
            if cid in label_nodes:
                return label_nodes[cid]
            col = self.store.collection(cid)
            if col and col["remote_id"]:
                return self.keep.getLabel(col["remote_id"])
            return None

        for op in self.store.outbox("item"):
            item = self.store.item(op["ref_id"])
            if item is None or item["kind"] != "note":
                continue
            node = self.keep.get(item["remote_id"]) if item["remote_id"] else None
            if op["op"] == "delete":
                if node:
                    node.trash()
                handled.append(("item-delete", op, item["id"], None))
                continue
            want_list = item["checklist_json"] is not None
            if node is not None and isinstance(node, gkeepapi.node.List) != want_list:
                node.trash()  # type changed: Keep cannot convert, so replace the note
                node = None
            if node is None:
                if want_list:
                    entries = json.loads(item["checklist_json"])
                    node = self.keep.createList(item["title"], [(e.get("text", ""), bool(e.get("checked")))
                                                                for e in entries])
                else:
                    node = self.keep.createNote(item["title"], item["body"])
            else:
                node.title = item["title"]
                if want_list:
                    self._sync_list_items(node, json.loads(item["checklist_json"]), gkeepapi)
                elif node.text != item["body"]:
                    node.text = item["body"]
            node.pinned = bool(item["pinned"])
            node.archived = bool(item["archived"])
            try:
                node.color = gkeepapi.node.ColorValue(item["color"])
            except ValueError:
                pass
            wanted = set(json.loads(item["labels_json"] or "[]"))
            current = {l.id: l for l in node.labels.all()}
            wanted_nodes = {}
            for cid in wanted:
                ln = label_node(cid)
                if ln:
                    wanted_nodes[ln.id] = ln
            for lid, ln in current.items():
                if lid not in wanted_nodes:
                    node.labels.remove(ln)
            for lid, ln in wanted_nodes.items():
                if lid not in current:
                    node.labels.add(ln)
            handled.append(("item", op, item["id"], node.id))
        return handled

    @staticmethod
    def _sync_list_items(node, entries, gkeepapi):
        items = sorted(node.items, key=lambda i: -int(i.sort or 0))
        for i, entry in enumerate(entries):
            if i < len(items):
                if items[i].text != entry.get("text", ""):
                    items[i].text = entry.get("text", "")
                if items[i].checked != bool(entry.get("checked")):
                    items[i].checked = bool(entry.get("checked"))
            else:
                node.add(entry.get("text", ""), bool(entry.get("checked")),
                         gkeepapi.node.NewListItemPlacementValue.Bottom)
        for extra in items[len(entries):]:
            extra.delete()

    def _commit(self, handled):
        labels = None
        with self.store.tx():
            for kind, op, ref, remote in handled:
                if kind == "collection":
                    if remote:
                        self.store.update_collection(ref, remote_id=remote)
                    self.store.dequeue(op["seq"])
                elif kind == "collection-delete":
                    self.store.purge_collection(ref)
                elif kind == "item-delete":
                    self.store.purge_item(ref)
                else:
                    if labels is None:
                        labels = self._label_map()
                    node = self.keep.get(remote)
                    fields = {"remote_id": remote}
                    if node is not None:
                        fields["base_json"] = json.dumps(self._remote_fields(node, labels))
                        fields["remote_updated"] = self._updated(node)
                    self.store.update_item(ref, touch=False, **fields)
                    self.store.dequeue(op["seq"])

    # ---------- full cycle ----------
    def sync(self):
        if self.keep is None:
            self._login()
        self.keep.sync()
        self._pull()
        handled = self._push()
        if handled:
            self.keep.sync()
            self._commit(handled)
        self._save_state()
        return len(handled)
