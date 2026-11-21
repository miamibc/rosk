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

# One sync costs about 8 requests (Tasks: the lists + one per list; Keep: one), so polling is
# paced by what the user is doing. Google's tasklist.updated does not change when a task is
# only renamed, so a cheaper "has anything changed" check is not reliable.
PUSH_DELAY = 15          # seconds after the last local change before it is sent
ACTIVE_INTERVAL = 5 * 60     # the Rosk window is active
PRESENT_INTERVAL = 15 * 60   # the user works in other apps
AWAY_INTERVAL = 60 * 60      # idle for AWAY_IDLE or the screen is locked
AWAY_IDLE = 10 * 60
FOCUS_MIN_AGE = 2 * 60   # focusing the window syncs if the last sync is older than this
RETURN_MIN_AGE = 5 * 60  # coming back to the computer syncs if the last sync is older than this
HEARTBEAT = 60           # local check of what the user is doing; no network
LISTS_EVERY = 2          # polls re-read the Google Tasks lists themselves every other time;
                         # the tasks in them are pulled on every sync
ACTIVE, PRESENT, AWAY = "active", "present", "away"


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
        self.last_lists_sync = None
        self._with_lists = True
        self._thread = None
        self._again = False
        self._timer = None
        self._periodic = None
        self._keep = None
        self._keep_key = None
        self._backoff = 0
        self._quiet = False
        self._again_quiet = False
        self.interval = PRESENT_INTERVAL
        self.active = False
        self.locked = False
        self._mode = PRESENT
        self._idle_proxy = None
        self.enabled = True
        self._monitor = Gio.NetworkMonitor.get_default()
        self._monitor.connect("network-changed", self._on_network)
        self._watch_session()
        self._periodic = GLib.timeout_add_seconds(HEARTBEAT, self._heartbeat)
        # every local change queued for Google pushes PUSH_DELAY seconds after the last one
        repo.store.on_enqueue = self.request

    # ---------- public ----------
    def request(self, delay=None, quiet=False):
        """Asks for a sync soon. Repeated requests are coalesced: each one restarts the timer.
        A quiet sync (polling) does not show the spinner; it stays quiet only if every
        coalesced request was quiet."""
        if not self.enabled:
            return
        if self._timer:
            GLib.source_remove(self._timer)
            self._quiet = self._quiet and quiet
        else:
            self._quiet = quiet
        delay = PUSH_DELAY if delay is None else delay
        self._timer = GLib.timeout_add(int(delay * 1000), self._start)

    def now(self):
        self.request(0)

    def set_interval(self, seconds):
        """Polling interval while the user works in other apps."""
        self.interval = seconds

    def set_active(self, active):
        """The main window is active: poll more often and catch up right away."""
        if active == self.active:
            return
        self.active = active
        if active and self._age() > FOCUS_MIN_AGE:
            self.request(0, quiet=True)

    @property
    def pending(self):
        return self.repo.store.pending_count()

    # ---------- internals ----------
    def _age(self):
        return float("inf") if self.last_sync is None else time.time() - self.last_sync

    def mode(self):
        if self.locked or self._idle_seconds() >= AWAY_IDLE:
            return AWAY
        return ACTIVE if self.active else PRESENT

    def poll_interval(self, mode):
        return {ACTIVE: ACTIVE_INTERVAL, PRESENT: self.interval, AWAY: AWAY_INTERVAL}[mode]

    def _heartbeat(self):
        mode = self.mode()
        came_back = self._mode == AWAY and mode != AWAY
        self._mode = mode
        age = self._age()
        if age >= self.poll_interval(mode) or (came_back and age > RETURN_MIN_AGE):
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
                self._heartbeat()  # unlocked: catch up if it has been a while

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
            self.request(3)

    def _start(self):
        self._timer = None
        if self._thread and self._thread.is_alive():
            self._again_quiet = self._quiet if not self._again else self._again_quiet and self._quiet
            self._again = True
            return False
        if not self._quiet:
            self._set_state(SYNCING, "")
        self._with_lists = self._wants_lists()
        self._thread = threading.Thread(target=self._run, args=(self._with_lists,),
                                        name="rosk-sync", daemon=True)
        self._thread.start()
        return False

    def _set_state(self, state, message):
        self.state = state
        self.message = message
        self.emit("status-changed")

    def _wants_lists(self):
        """Pushes, the Refresh button and retries re-read the task lists; quiet polls only when
        that was LISTS_EVERY poll intervals ago or a list change waits to be sent."""
        if not self._quiet or self.state in (OFFLINE, ERROR) or self.last_lists_sync is None:
            return True
        if self.repo.store.conn.execute(
                "SELECT 1 FROM outbox o JOIN collection c ON o.target='collection'"
                " AND c.id=o.ref_id WHERE c.kind='tasklist' LIMIT 1").fetchone():
            return True
        due = LISTS_EVERY * self.poll_interval(self.mode()) - HEARTBEAT  # heartbeat jitter
        return time.time() - self.last_lists_sync >= due

    def _run(self, with_lists=True):
        store = Store(config.db_path())
        state, message = IDLE, ""
        changed = True
        try:
            media = config.data_dir() / "media"
            before = fingerprint(store, media)
            state, message = self._sync_tasks(store, with_lists)
            self._sync_keep(store)
            changed = fingerprint(store, media) != before
        except Exception as e:  # never let the worker die silently
            log.exception("sync failed")
            state, message = ERROR, str(e)
        finally:
            store.close()
        GLib.idle_add(self._finished, state, message, changed)

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

    def _finished(self, state, message, changed=True):
        self.last_sync = time.time()
        if self._with_lists and state == IDLE:
            self.last_lists_sync = self.last_sync
        self._set_state(state, message)
        if changed:  # an idle poll must not rebuild the lists under the user's hands
            self.emit("data-changed")
        if self._again:
            self._again = False
            self.request(1, quiet=self._again_quiet)
        elif state == OFFLINE or (state == ERROR and self.pending):
            self._backoff = min(max(self._backoff * 2, 30), 15 * 60)
            self.request(self._backoff)
        else:
            self._backoff = 0
        return False
