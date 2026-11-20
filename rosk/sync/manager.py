"""Runs synchronization in a worker thread and reports status to the main loop."""

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
        self._thread = None
        self._again = False
        self._timer = None
        self._periodic = None
        self._keep = None
        self._keep_key = None
        self._backoff = 0
        self.interval = 15 * 60
        self.enabled = True
        self._monitor = Gio.NetworkMonitor.get_default()
        self._monitor.connect("network-changed", self._on_network)
        self._schedule_periodic()

    # ---------- public ----------
    def request(self, delay=2.0):
        """Asks for a sync soon. Repeated requests are coalesced."""
        if not self.enabled:
            return
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(int(delay * 1000), self._start)

    def now(self):
        self.request(0)

    def set_interval(self, seconds):
        if seconds != self.interval:
            self.interval = seconds
            self._schedule_periodic()

    @property
    def pending(self):
        return self.repo.store.pending_count()

    # ---------- internals ----------
    def _schedule_periodic(self):
        if self._periodic:
            GLib.source_remove(self._periodic)
        self._periodic = GLib.timeout_add_seconds(self.interval, self._tick)

    def _tick(self):
        self.request(0)
        return True

    def _on_network(self, monitor, available):
        if available:
            self._backoff = 0
            self.request(3)

    def _start(self):
        self._timer = None
        if self._thread and self._thread.is_alive():
            self._again = True
            return False
        self._set_state(SYNCING, "")
        self._thread = threading.Thread(target=self._run, name="rosk-sync", daemon=True)
        self._thread.start()
        return False

    def _set_state(self, state, message):
        self.state = state
        self.message = message
        self.emit("status-changed")

    def _run(self):
        store = Store(config.db_path())
        state, message = IDLE, ""
        try:
            state, message = self._sync_tasks(store)
            self._sync_keep(store)
        except Exception as e:  # never let the worker die silently
            log.exception("sync failed")
            state, message = ERROR, str(e)
        finally:
            store.close()
        GLib.idle_add(self._finished, state, message)

    def _sync_tasks(self, store):
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
            TasksSync(store, client).sync()
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

    def _finished(self, state, message):
        self.last_sync = time.time()
        self._set_state(state, message)
        self.emit("data-changed")
        if self._again:
            self._again = False
            self.request(1)
        elif state == OFFLINE or (state == ERROR and self.pending):
            self._backoff = min(max(self._backoff * 2, 30), 15 * 60)
            self.request(self._backoff)
        else:
            self._backoff = 0
        return False
