"""Runs synchronization in a worker thread and reports status to the main loop."""

import hashlib
import logging
import threading
import time

from gi.repository import Gio, GLib, GObject

from .. import config
from ..i18n import _
from ..store.db import Store
from .http import HttpError

log = logging.getLogger(__name__)

IDLE = "idle"
SYNCING = "syncing"
OFFLINE = "offline"
ERROR = "error"
NO_ACCOUNT = "no-account"

# Each part of the sync has its own clock, paced by what the user is doing, so that what
# changes often is seen soon and Google is not asked more than needed. Costs per poll:
# Keep one request, the tasks one request per task list, the task lists themselves one.
# There is no cheap "did anything change" check: Google's tasklist.updated does not move when
# a task is only renamed (checked 2026-11-21).
PUSH_DELAY = 10          # seconds after the last local change before it is sent
AWAY_IDLE = 10 * 60      # idle this long (or a locked screen) counts as away
HEARTBEAT = 60           # local check of what the user is doing and what is due; no network
ACTIVE, PRESENT, AWAY = "active", "present", "away"   # Rosk window active / other apps / away
KEEP, TASKS, LISTS = "keep", "tasks", "lists"
POLL = {  # seconds between polls of each part
    KEEP: {ACTIVE: 2 * 60, PRESENT: 10 * 60, AWAY: 60 * 60},
    TASKS: {ACTIVE: 5 * 60, PRESENT: 15 * 60, AWAY: 60 * 60},
    LISTS: {ACTIVE: 10 * 60, PRESENT: 30 * 60, AWAY: 2 * 60 * 60},
}
DUE_SLACK = 5            # a part this close to its interval is polled now, not a minute later


def fingerprint(store, media_dir=None):
    """A digest of what the UI shows, to tell whether a sync changed anything.
    Pulls write remote_updated whenever Google's copy changed; local edits touch updated_at."""
    h = hashlib.blake2b(digest_size=16)
    for row in store.conn.execute("SELECT id, updated_at, remote_updated, deleted, media_json, "
                                  "web_view_link FROM item ORDER BY id"):
        h.update(repr(tuple(row)).encode())
    for row in store.conn.execute("SELECT id, title, position, deleted, remote_id FROM collection "
                                  "ORDER BY id"):
        h.update(repr(tuple(row)).encode())
    if media_dir is not None and media_dir.is_dir():
        h.update(repr(sorted(p.name for p in media_dir.iterdir())).encode())
    return h.hexdigest()


class SyncManager(GObject.Object):
    __gsignals__ = {
        "status-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        "data-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, repo):
        super().__init__()
        self.repo = repo
        self.state = IDLE
        self.message = ""
        self.keep_message = ""
        self.last_sync = None
        self.last = {KEEP: None, TASKS: None, LISTS: None}  # when each part was last synced
        self._tasks_state = (IDLE, "")  # kept while a sync leaves Tasks out
        self._parts = set()
        self._thread = None
        self._again = False
        self._timer = None
        self._periodic = None
        self._keep = None
        self._keep_key = None
        self._backoff = 0
        self._quiet = False
        self._force = False
        self._again_quiet = False
        self._again_force = False
        self.active = False
        self.locked = False
        self._idle_proxy = None
        self.enabled = True
        self._monitor = Gio.NetworkMonitor.get_default()
        self._monitor.connect("network-changed", self._on_network)
        self._watch_session()
        self._periodic = GLib.timeout_add_seconds(HEARTBEAT, self._heartbeat)
        # every local change queued for Google pushes PUSH_DELAY seconds after the last one
        repo.store.on_enqueue = self.request

    # ---------- public ----------
    def request(self, delay=None, quiet=False, force=False):
        """Asks for a sync soon. Repeated requests are coalesced: each one restarts the timer.
        What is synced is decided when it starts: the parts with changes waiting to be sent and
        the parts whose poll is due, or everything when forced. A quiet sync (polling) does not
        show the spinner; it stays quiet only if every coalesced request was quiet."""
        if not self.enabled:
            return
        if self._timer:
            GLib.source_remove(self._timer)
            self._quiet = self._quiet and quiet
            self._force = self._force or force
        else:
            self._quiet, self._force = quiet, force
        delay = PUSH_DELAY if delay is None else delay
        self._timer = GLib.timeout_add(int(delay * 1000), self._start)

    def now(self):
        """The Refresh button: everything, right away."""
        self.request(0, force=True)

    def set_active(self, active):
        """The main window is active: polls get more frequent, and what is already older than
        the active intervals is synced right away."""
        if active == self.active:
            return
        self.active = active
        self._heartbeat()

    @property
    def pending(self):
        return self.repo.store.pending_count()

    # ---------- internals ----------
    def mode(self):
        if self.locked or self._idle_seconds() >= AWAY_IDLE:
            return AWAY
        return ACTIVE if self.active else PRESENT

    def due_parts(self, mode=None):
        mode = mode or self.mode()
        now = time.time()
        return {p for p, clock in POLL.items()
                if self.last[p] is None or now - self.last[p] >= clock[mode] - DUE_SLACK}

    def pending_parts(self):
        rows = self.repo.store.conn.execute(
            "SELECT DISTINCT coalesce(i.kind, c.kind) FROM outbox o"
            " LEFT JOIN item i ON o.target='item' AND i.id=o.ref_id"
            " LEFT JOIN collection c ON o.target='collection' AND c.id=o.ref_id").fetchall()
        kinds = {r[0] for r in rows}
        parts = set()
        if kinds & {"task", "tasklist"}:
            parts.add(TASKS)
        if "tasklist" in kinds:
            parts.add(LISTS)
        if kinds & {"note", "label"}:
            parts.add(KEEP)
        return parts

    def _heartbeat(self):
        """Once a minute and on focus/unlock. Coming back to the computer needs no rule of its
        own: whatever is older than the new mode's interval is simply due."""
        if self.due_parts():
            self.request(0, quiet=True)
        return True

    # ---------- what the user is doing (GNOME); unknown means "present" ----------
    def _watch_session(self):
        flags = (Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
                 | Gio.DBusProxyFlags.DO_NOT_AUTO_START)
        try:
            self._idle_proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, flags | Gio.DBusProxyFlags.DO_NOT_CONNECT_SIGNALS, None,
                "org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core",
                "org.gnome.Mutter.IdleMonitor", None)
            saver = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, flags, None, "org.gnome.ScreenSaver",
                "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", None)
            saver.connect("g-signal", self._on_saver_signal)
            self._saver = saver
            self.locked = bool(saver.call_sync("GetActive", None, Gio.DBusCallFlags.NONE,
                                               1000, None).unpack()[0])
        except GLib.Error as e:
            log.info("idle/lock state not available: %s", e.message)

    def _on_saver_signal(self, _proxy, _sender, signal, params):
        if signal == "ActiveChanged":
            self.locked = bool(params.unpack()[0])
            if not self.locked:
                self._heartbeat()  # unlocked: catch up on what is due

    def _idle_seconds(self):
        if self._idle_proxy is None:
            return 0
        try:
            ms = self._idle_proxy.call_sync("GetIdletime", None, Gio.DBusCallFlags.NONE, 500,
                                            None).unpack()[0]
            return ms / 1000
        except GLib.Error:
            return 0

    def _on_network(self, monitor, available):
        if available:
            self._backoff = 0
            self.request(3, force=True)

    def _start(self):
        self._timer = None
        if self._thread and self._thread.is_alive():
            if self._again:
                self._again_quiet = self._again_quiet and self._quiet
                self._again_force = self._again_force or self._force
            else:
                self._again_quiet, self._again_force = self._quiet, self._force
            self._again = True
            return False
        parts = self._choose_parts()
        if not parts:
            return False  # nothing waits to be sent and no poll is due
        self._parts = parts
        if not self._quiet:
            self._set_state(SYNCING, "")
        self._thread = threading.Thread(target=self._run, args=(parts,),
                                        name="rosk-sync", daemon=True)
        self._thread.start()
        return False

    def _choose_parts(self):
        if self._force or self.state in (OFFLINE, ERROR):
            return {KEEP, TASKS, LISTS}
        parts = self.pending_parts() | self.due_parts()
        if LISTS in parts:
            parts.add(TASKS)  # the lists are read as part of a Tasks sync
        return parts

    def _set_state(self, state, message):
        self.state = state
        self.message = message
        self.emit("status-changed")

    def _run(self, parts=frozenset({KEEP, TASKS, LISTS})):
        store = Store(config.db_path())
        state, message = IDLE, ""
        changed = True
        try:
            media = config.data_dir() / "media"
            before = fingerprint(store, media)
            if TASKS in parts:
                state, message = self._sync_tasks(store, lists=LISTS in parts)
            else:
                state, message = self._tasks_state  # unchanged since Tasks was last synced
            if KEEP in parts:
                self._sync_keep(store)
            changed = fingerprint(store, media) != before
        except Exception as e:  # never let the worker die silently
            log.exception("sync failed")
            state, message = ERROR, str(e)
        finally:
            store.close()
        GLib.idle_add(self._finished, state, message, changed, parts)

    def _sync_tasks(self, store, lists=True):
        from ..auth import goa
        from .gtasks import TasksClient
        from .tasks_sync import TasksSync

        try:
            accounts = goa.list_google_accounts()
        except Exception as e:
            return ERROR, _("Online Accounts are not available: {}").format(e)
        if not accounts:
            return NO_ACCOUNT, _("Add a Google account in Settings → Online Accounts")
        wanted = store.get_meta("goa_id")
        account = next((a for a in accounts if a.goa_id == wanted), accounts[0])
        if not account.tasks_enabled:
            return ERROR, _("Enable “Calendar” for {} in Online Accounts to sync tasks").format(account.email)
        if store.get_meta("account_email") != account.email:
            with store.tx():
                store.set_meta("account_email", account.email)
        client = TasksClient(goa.token_provider(account.goa_id))
        try:
            TasksSync(store, client).sync(lists=lists)
        except HttpError as e:
            if e.status == 0:
                return OFFLINE, _("No connection. Changes will be sent later.")
            return ERROR, str(e)
        except goa.GoaError as e:
            return ERROR, str(e)
        failed = store.failed_ops()
        if failed:
            return ERROR, _("Some changes were rejected by Google: {}").format(failed[0]["last_error"])
        return IDLE, ""

    def _sync_keep(self, store):
        from ..auth import secrets
        from .keep_sync import KeepSync, KeepUnavailable

        email = store.get_meta("keep_email")
        if not email:
            self.keep_message = ""
            return
        try:
            token = secrets.keep_token(email)
        except Exception as e:
            self.keep_message = str(e)
            return
        if not token:
            self.keep_message = _("Keep is not connected")
            return
        key = (email, token)
        if self._keep is None or self._keep_key != key:
            self._keep = KeepSync(store, email, token, config.data_dir() / "keep-state.json")
            self._keep_key = key
        self._keep.store = store
        try:
            self._keep.sync()
            self.keep_message = ""
        except KeepUnavailable as e:
            self._keep = None
            self.keep_message = str(e)
        except Exception as e:
            log.warning("keep sync failed: %s", e)
            self._keep = None
            self.keep_message = _("Keep sync failed: {}").format(e)

    def _finished(self, state, message, changed=True, parts=frozenset({KEEP, TASKS, LISTS})):
        now = time.time()
        self.last_sync = now
        if KEEP in parts:
            self.last[KEEP] = now  # Keep reports its own trouble in keep_message
        if TASKS in parts:
            self._tasks_state = (state, message)
            if state not in (OFFLINE, ERROR):  # a failed sync is retried, not counted
                self.last[TASKS] = now
                if LISTS in parts:
                    self.last[LISTS] = now
        self._set_state(state, message)
        if changed:  # an idle poll must not rebuild the lists under the user's hands
            self.emit("data-changed")
        if self._again:
            self._again = False
            self.request(1, quiet=self._again_quiet, force=self._again_force)
        elif state == OFFLINE or (state == ERROR and self.pending):
            self._backoff = min(max(self._backoff * 2, 30), 15 * 60)
            self.request(self._backoff, force=True)
        else:
            self._backoff = 0
        return False
