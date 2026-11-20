"""Rosk application: a resident GApplication that owns the data, sync and D-Bus API."""

import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__, config  # noqa: E402
from .i18n import _, ngettext  # noqa: E402

log = logging.getLogger("rosk")


class RoskApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("quick", ord("q"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Open the quick-capture window"), None)
        self.add_main_option("background", ord("b"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Start without a window"), None)
        self.add_main_option("version", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Print the version"), None)
        self.repo = None
        self.sync = None
        self.api = None
        self.tray = None
        self.window = None
        self.quick = None
        self._changed_src = None

    # ---------- core ----------
    def _ensure_core(self):
        if self.repo is not None:
            return
        from .service.dbus import ServiceApi
        from .store.repo import Repo
        from .sync.manager import SyncManager

        self.repo = Repo(config.db_path())
        self.sync = SyncManager(self.repo)
        self.api = ServiceApi(self)
        self.repo.connect_changed(lambda *_: self._on_changed())
        self.sync.connect("data-changed", lambda *_: self._on_changed())

    def do_dbus_register(self, connection, path):
        self._ensure_core()
        self.api.register(connection)
        return True

    def do_dbus_unregister(self, connection, path):
        if self.api:
            self.api.unregister(connection)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._ensure_core()
        from .ui.prefs import apply_theme

        apply_theme(self.repo.store.get_meta("theme", "system"))
        self._actions()
        self.hold()  # stay resident: quick capture, sync and reminders work without a window

        conn = self.get_dbus_connection()
        if conn is not None and not os.environ.get("ROSK_NO_TRAY"):
            from .service.tray import Tray

            icon_dir = str(Path(__file__).resolve().parent / "data" / "icons")
            self.tray = Tray(self, APP_ID + "-symbolic", icon_dir)
            self.tray.start(conn)
        self._on_changed()
        if os.environ.get("ROSK_NO_SYNC"):
            self.sync.enabled = False
        self.sync.request(1)
        GLib.timeout_add_seconds(60, self._minute_tick)
        GLib.idle_add(self._minute_tick)

    def do_shutdown(self):
        if self.window:
            self.window.commit_pending_deletes()
        if self.tray:
            self.tray.stop()
        Adw.Application.do_shutdown(self)

    def do_command_line(self, cmdline):
        opts = cmdline.get_options_dict().end().unpack()
        if opts.get("version"):
            cmdline.print_literal(f"Rosk {__version__}\n")
            return 0
        if opts.get("quick"):
            self.activate_action("quick-add", None)
        elif not opts.get("background"):
            self.activate()
        return 0

    def do_activate(self):
        win = self._window()
        win.present()
        shot = os.environ.get("ROSK_SNAPSHOT")
        if shot:
            from .ui.debug import schedule_snapshot

            schedule_snapshot(self, win, shot)

    # ---------- windows ----------
    def _window(self):
        if self.window is None:
            from .ui.window import MainWindow

            # Windows are hidden on close and reused: PyGObject does not deliver "destroy"
            # reliably, and a destroyed window cannot be shown again.
            self.window = MainWindow(self)
        return self.window

    def open_item(self, iid):
        win = self._window()
        win.present()
        if iid:
            win.open_item(iid, focus=False)

    def open_search(self, text):
        win = self._window()
        win.present()
        win.search_button.set_active(True)
        win.search_entry.set_text(text)

    # ---------- actions ----------
    def _actions(self):
        def add(name, cb, accels=None, param=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.set_accels_for_action("app." + name, accels)

        add("quit", lambda *_: self._quit(), ["<Control>q"])
        add("preferences", lambda *_: self._prefs(), ["<Control>comma"])
        add("about", lambda *_: self._about())
        add("sync", lambda *_: self.sync.now(), ["F5"])
        add("quick-add", lambda *_: self._quick())
        add("open-item", lambda _a, v: self.open_item(v.get_string()), param="s")
        add("show-today", lambda *_: (self._window().present(), self._window().show_view("today")))

    def _quit(self):
        if self.window:
            self.window.editor.flush()
            self.window.commit_pending_deletes()
        self.release()
        self.quit()

    def _prefs(self):
        from .ui.prefs import Preferences

        Preferences(self).present(self._window())

    def _about(self):
        about = Adw.AboutDialog(
            application_name="Rosk", application_icon=APP_ID, version=__version__,
            developer_name="Sergei Miami", website="https://github.com/miamibc/rosk",
            comments=_("A note you write down and cross out."),
            license_type=Gtk.License.GPL_3_0)
        about.present(self._window())

    def _quick(self):
        from .ui.quick import QuickWindow

        if self.quick is None:
            self.quick = QuickWindow(self)
        self.quick.popup()

    # ---------- change propagation ----------
    def _on_changed(self):
        if self._changed_src is None:
            self._changed_src = GLib.timeout_add(150, self._flush_changed)

    def _flush_changed(self):
        self._changed_src = None
        if self.api:
            self.api.emit_changed()
        if self.tray:
            self.tray.set_count(self.repo.counts()["today"])
        return False

    # ---------- morning summary ----------
    def _minute_tick(self):
        store = self.repo.store
        today = date.today().isoformat()
        if store.get_meta("summary_enabled", "1") == "1" and store.get_meta("summary_last") != today:
            hh, mm = (store.get_meta("summary_time", "09:00").split(":") + ["0"])[:2]
            now = datetime.now()
            if (now.hour, now.minute) >= (int(hh), int(mm)):
                with store.tx():
                    store.set_meta("summary_last", today)
                self._summary()
        # the day changed: refresh deadline highlighting
        if getattr(self, "_last_day", today) != today:
            self.repo.notify()
        self._last_day = today
        return True

    def _summary(self):
        c = self.repo.counts()
        if not c["today"]:
            return
        n = Gio.Notification.new(_("Rosk · Today"))
        body = ngettext("{} task for today", "{} tasks for today", c["today"]).format(c["today"])
        if c["overdue"]:
            body += ", " + ngettext("{} overdue", "{} overdue", c["overdue"]).format(c["overdue"])
        titles = [i["title"] for i in self.repo.today_tasks()[:4]]
        if titles:
            body += "\n• " + "\n• ".join(titles)
        n.set_body(body)
        n.set_default_action("app.show-today")
        n.add_button(_("Open"), "app.show-today")
        self.send_notification("summary", n)


def main(argv=None):
    logging.basicConfig(level=logging.INFO if "--debug" not in (argv or sys.argv) else logging.DEBUG,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    argv = [a for a in (argv or sys.argv) if a != "--debug"]
    app = RoskApp()
    return app.run(argv)
