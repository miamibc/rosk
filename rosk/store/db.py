"""Local SQLite store: the source of truth for the UI.

Every user change is written together with an outbox entry in one transaction,
so nothing is lost if the app crashes before it reaches Google.
"""

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS collection (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('tasklist', 'label')),
    remote_id TEXT,
    title TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    base_json TEXT,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS collection_remote ON collection(kind, remote_id);

CREATE TABLE IF NOT EXISTS item (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('task', 'note')),
    remote_id TEXT,
    collection_id TEXT,
    parent_id TEXT,
    position TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    completed_at TEXT,
    due_date TEXT,
    -- reserved for the internal Google Tasks API (ROADMAP M7)
    due_time TEXT, recurrence TEXT, starred INTEGER NOT NULL DEFAULT 0,
    -- notes
    checklist_json TEXT,
    labels_json TEXT NOT NULL DEFAULT '[]',
    pinned INTEGER NOT NULL DEFAULT 0,
    color TEXT NOT NULL DEFAULT 'DEFAULT',
    archived INTEGER NOT NULL DEFAULT 0,
    -- read-only data from Google
    links_json TEXT NOT NULL DEFAULT '[]',
    web_view_link TEXT,
    -- Keep images and drawings: [{"id", "type", "mimetype", "width", "height"}]
    media_json TEXT NOT NULL DEFAULT '[]',
    assignment_json TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    remote_updated TEXT,
    base_json TEXT,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS item_remote ON item(kind, remote_id);
CREATE INDEX IF NOT EXISTS item_collection ON item(collection_id, deleted);
CREATE INDEX IF NOT EXISTS item_parent ON item(parent_id);
CREATE INDEX IF NOT EXISTS item_due ON item(due_date) WHERE status = 'open' AND deleted = 0;

CREATE VIRTUAL TABLE IF NOT EXISTS item_fts USING fts5(id UNINDEXED, title, body, tokenize = 'unicode61');

CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL CHECK (target IN ('item', 'collection')),
    ref_id TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('create', 'update', 'move', 'delete')),
    created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS outbox_ref ON outbox(target, ref_id);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    at TEXT NOT NULL,
    source TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);
"""

TASK_SYNC_FIELDS = ("title", "body", "status", "due_date", "completed_at",
                    "collection_id", "parent_id")
NOTE_SYNC_FIELDS = ("title", "body", "checklist_json", "labels_json", "pinned", "color",
                    "archived")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id():
    return uuid.uuid4().hex


def connect(path):
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
    elif int(row["value"]) < SCHEMA_VERSION:
        migrate(conn, int(row["value"]))
    return conn


def migrate(conn, version):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(item)")}
    if version < 2 and "media_json" not in columns:
        conn.execute("ALTER TABLE item ADD COLUMN media_json TEXT NOT NULL DEFAULT '[]'")
    conn.execute("UPDATE meta SET value=? WHERE key='schema'", (str(SCHEMA_VERSION),))


class Tx:
    """BEGIN IMMEDIATE ... COMMIT; re-entrant within a thread."""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        local = self.store._local
        local.depth = getattr(local, "depth", 0) + 1
        if local.depth == 1:
            self.store.conn.execute("BEGIN IMMEDIATE")
        return self.store.conn

    def __exit__(self, exc_type, exc, tb):
        local = self.store._local
        local.depth -= 1
        if local.depth == 0:
            if exc_type:
                self.store.conn.execute("ROLLBACK")
            else:
                self.store.conn.execute("COMMIT")
        return False


def row_to_dict(row):
    return None if row is None else dict(row)


class Store:
    """Low-level data access. One Store per thread (each has its own connection)."""

    def __init__(self, path):
        self.path = path
        self.conn = connect(path)
        self._local = threading.local()

    def tx(self):
        return Tx(self)

    def close(self):
        self.conn.close()

    # meta
    def get_meta(self, key, default=None):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else row[0]

    def set_meta(self, key, value):
        if value is None:
            self.conn.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            self.conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))

    # collections
    def collections(self, kind=None, include_deleted=False):
        sql = "SELECT * FROM collection WHERE 1=1"
        args = []
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if not include_deleted:
            sql += " AND deleted=0"
        sql += " ORDER BY position, title COLLATE NOCASE"
        return [dict(r) for r in self.conn.execute(sql, args)]

    def collection(self, cid):
        return row_to_dict(self.conn.execute("SELECT * FROM collection WHERE id=?", (cid,)).fetchone())

    def collection_by_remote(self, kind, remote_id):
        return row_to_dict(self.conn.execute(
            "SELECT * FROM collection WHERE kind=? AND remote_id=?", (kind, remote_id)).fetchone())

    def collection_by_title(self, kind, title):
        return row_to_dict(self.conn.execute(
            "SELECT * FROM collection WHERE kind=? AND deleted=0 AND lower(title)=lower(?)",
            (kind, title)).fetchone())

    def insert_collection(self, **fields):
        fields.setdefault("id", new_id())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        self.conn.execute(f"INSERT INTO collection ({cols}) VALUES ({marks})", list(fields.values()))
        return fields["id"]

    def update_collection(self, cid, **fields):
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE collection SET {sets} WHERE id=?", [*fields.values(), cid])

    def purge_collection(self, cid):
        ids = [r[0] for r in self.conn.execute("SELECT id FROM item WHERE collection_id=?", (cid,))]
        for iid in ids:
            self.purge_item(iid)
        self.conn.execute("DELETE FROM outbox WHERE target='collection' AND ref_id=?", (cid,))
        self.conn.execute("DELETE FROM collection WHERE id=?", (cid,))

    # items
    def item(self, iid):
        return row_to_dict(self.conn.execute("SELECT * FROM item WHERE id=?", (iid,)).fetchone())

    def item_by_remote(self, kind, remote_id):
        return row_to_dict(self.conn.execute(
            "SELECT * FROM item WHERE kind=? AND remote_id=?", (kind, remote_id)).fetchone())

    def items(self, where="1=1", args=(), order="position, created_at DESC"):
        sql = f"SELECT * FROM item WHERE deleted=0 AND ({where}) ORDER BY {order}"
        return [dict(r) for r in self.conn.execute(sql, args)]

    def children(self, parent_id, include_deleted=False):
        sql = "SELECT * FROM item WHERE parent_id=?"
        if not include_deleted:
            sql += " AND deleted=0"
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY position, created_at DESC",
                                                    (parent_id,))]

    def insert_item(self, **fields):
        ts = now_iso()
        fields.setdefault("id", new_id())
        fields.setdefault("created_at", ts)
        fields.setdefault("updated_at", ts)
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        self.conn.execute(f"INSERT INTO item ({cols}) VALUES ({marks})", list(fields.values()))
        self._index(fields["id"])
        return fields["id"]

    def update_item(self, iid, touch=True, **fields):
        if touch:
            fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE item SET {sets} WHERE id=?", [*fields.values(), iid])
        if "title" in fields or "body" in fields or "checklist_json" in fields or "deleted" in fields:
            self._index(iid)

    def purge_item(self, iid):
        for child in self.children(iid, include_deleted=True):
            self.purge_item(child["id"])
        self.conn.execute("DELETE FROM outbox WHERE target='item' AND ref_id=?", (iid,))
        self.conn.execute("DELETE FROM item_fts WHERE id=?", (iid,))
        self.conn.execute("DELETE FROM item WHERE id=?", (iid,))

    def _index(self, iid):
        self.conn.execute("DELETE FROM item_fts WHERE id=?", (iid,))
        row = self.item(iid)
        if row is None or row["deleted"]:
            return
        body = row["body"] or ""
        if row["checklist_json"]:
            body += "\n" + "\n".join(e.get("text", "") for e in json.loads(row["checklist_json"]))
        self.conn.execute("INSERT INTO item_fts (id, title, body) VALUES (?, ?, ?)",
                          (iid, row["title"], body))

    def search(self, query, limit=50):
        terms = [t for t in query.replace('"', " ").split() if t]
        if not terms:
            return []
        match = " ".join(f'"{t}"*' for t in terms)
        sql = ("SELECT item.* FROM item_fts JOIN item ON item.id = item_fts.id "
               "WHERE item_fts MATCH ? AND item.deleted=0 ORDER BY rank LIMIT ?")
        return [dict(r) for r in self.conn.execute(sql, (match, limit))]

    # outbox
    def outbox(self, target=None):
        sql = "SELECT * FROM outbox"
        args = []
        if target:
            sql += " WHERE target=?"
            args.append(target)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY seq", args)]

    def pending_ops(self, target, ref_id):
        return [r["op"] for r in self.conn.execute(
            "SELECT op FROM outbox WHERE target=? AND ref_id=? ORDER BY seq", (target, ref_id))]

    def enqueue(self, target, ref_id, op):
        """Adds an operation, coalescing with what is already queued for the object."""
        ops = self.pending_ops(target, ref_id)
        if op == "update" and ("create" in ops or "update" in ops):
            return
        if op == "move" and ("create" in ops or "move" in ops):
            return
        if op == "delete":
            if "create" in ops:
                # never reached the server: forget it entirely
                self.conn.execute("DELETE FROM outbox WHERE target=? AND ref_id=?", (target, ref_id))
                return "forget"
            self.conn.execute("DELETE FROM outbox WHERE target=? AND ref_id=?", (target, ref_id))
        self.conn.execute("INSERT INTO outbox (target, ref_id, op, created_at) VALUES (?, ?, ?, ?)",
                          (target, ref_id, op, now_iso()))

    def dequeue(self, seq):
        self.conn.execute("DELETE FROM outbox WHERE seq=?", (seq,))

    def fail(self, seq, error):
        self.conn.execute("UPDATE outbox SET attempts=attempts+1, last_error=? WHERE seq=?",
                          (str(error)[:500], seq))

    def pending_count(self):
        return self.conn.execute("SELECT count(*) FROM outbox").fetchone()[0]

    def failed_ops(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM outbox WHERE last_error IS NOT NULL ORDER BY seq")]

    # history
    def remember(self, item_id, source, snapshot):
        self.conn.execute("INSERT INTO history (item_id, at, source, snapshot_json) VALUES (?,?,?,?)",
                          (item_id, now_iso(), source, json.dumps(snapshot, ensure_ascii=False)))
