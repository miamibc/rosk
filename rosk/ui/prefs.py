"""Preferences: account, default list, Google Keep, morning summary, appearance."""

import threading

from gi.repository import Adw, Gio, GLib, Gtk

from ..i18n import _

THEMES = [("system", _("Follow System")), ("light", _("Light")), ("dark", _("Dark"))]


def apply_theme(value):
    scheme = {"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}.get(
        value, Adw.ColorScheme.DEFAULT)
    Adw.StyleManager.get_default().set_color_scheme(scheme)


class Preferences(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__(title=_("Preferences"))
        self.app = app
        self.repo = app.repo
        self.meta = self.repo.store
        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")
        self.add(page)

        # account
        from ..auth import goa

        acc_group = Adw.PreferencesGroup(title=_("Google Account"),
                                         description=_("Tasks sync through GNOME Online Accounts."))
        try:
            self.accounts = goa.list_google_accounts()
        except Exception:
            self.accounts = []
        if self.accounts:
            model = Gtk.StringList.new([a.email for a in self.accounts])
            row = Adw.ComboRow(title=_("Account"), model=model, icon_name="avatar-default-symbolic")
            current = self.meta.get_meta("goa_id")
            ids = [a.goa_id for a in self.accounts]
            if current in ids:
                row.set_selected(ids.index(current))
            row.connect("notify::selected", self._on_account)
            acc_group.add(row)
        else:
            acc_group.add(Adw.ActionRow(title=_("No Google account"),
                                        subtitle=_("Add one in Settings → Online Accounts")))
        open_goa = Adw.ButtonRow(title=_("Open Online Accounts"),
                                 end_icon_name="adw-external-link-symbolic")
        open_goa.connect("activated", lambda *_: Gio.Subprocess.new(
            ["gnome-control-center", "online-accounts"], Gio.SubprocessFlags.NONE))
        acc_group.add(open_goa)
        page.add(acc_group)

        # tasks
        tasks_group = Adw.PreferencesGroup(title=_("Tasks"))
        lists = self.repo.tasklists()
        if lists:
            model = Gtk.StringList.new([c["title"] for c in lists])
            row = Adw.ComboRow(title=_("Default list"), model=model,
                               subtitle=_("Quick capture adds tasks here"))
            ids = [c["id"] for c in lists]
            default = self.repo.default_tasklist()["id"]
            if default in ids:
                row.set_selected(ids.index(default))
            row.connect("notify::selected", lambda r, _p: self.repo.set_default_tasklist(ids[r.get_selected()]))
            tasks_group.add(row)
        page.add(tasks_group)

        # keep
        self.keep_group = Adw.PreferencesGroup(title=_("Google Keep"))
        page.add(self.keep_group)
        self._fill_keep()

        # summary
        sum_group = Adw.PreferencesGroup(title=_("Notifications"))
        sw = Adw.SwitchRow(title=_("Morning summary"),
                           subtitle=_("A notification with today’s and overdue tasks"),
                           active=self.meta.get_meta("summary_enabled", "1") == "1")
        sw.connect("notify::active", lambda r, _p: self._set("summary_enabled", "1" if r.get_active() else "0"))
        sum_group.add(sw)
        hh, mm = (self.meta.get_meta("summary_time", "09:00").split(":") + ["0"])[:2]
        hour = Adw.SpinRow.new_with_range(0, 23, 1)
        hour.set_title(_("Hour"))
        hour.set_value(int(hh))
        minute = Adw.SpinRow.new_with_range(0, 55, 5)
        minute.set_title(_("Minute"))
        minute.set_value(int(mm))

        def time_changed(*_a):
            self._set("summary_time", f"{int(hour.get_value()):02d}:{int(minute.get_value()):02d}")
        hour.connect("notify::value", time_changed)
        minute.connect("notify::value", time_changed)
        sw.bind_property("active", hour, "sensitive", 2)
        sw.bind_property("active", minute, "sensitive", 2)
        sum_group.add(hour)
        sum_group.add(minute)
        page.add(sum_group)

        # appearance
        look = Adw.PreferencesGroup(title=_("Appearance"))
        model = Gtk.StringList.new([t[1] for t in THEMES])
        row = Adw.ComboRow(title=_("Style"), model=model)
        keys = [t[0] for t in THEMES]
        current = self.meta.get_meta("theme", "system")
        row.set_selected(keys.index(current) if current in keys else 0)

        def theme_changed(r, _p):
            value = keys[r.get_selected()]
            self._set("theme", value)
            apply_theme(value)
        row.connect("notify::selected", theme_changed)
        look.add(row)
        page.add(look)

    def _set(self, key, value):
        with self.meta.tx():
            self.meta.set_meta(key, value)

    def _on_account(self, row, _p):
        acc = self.accounts[row.get_selected()]
        if acc.goa_id != self.meta.get_meta("goa_id"):
            self._set("goa_id", acc.goa_id)
            self.app.sync.now()

    # ---------- keep ----------
    def _fill_keep(self):
        group = self.keep_group
        for row in list(getattr(self, "_keep_rows", [])):
            group.remove(row)
        self._keep_rows = []

        def add(row):
            group.add(row)
            self._keep_rows.append(row)

        email = self.meta.get_meta("keep_email")
        if email:
            status = Adw.ActionRow(title=_("Connected"), subtitle=email, icon_name="emblem-ok-symbolic")
            if self.app.sync.keep_message:
                status.set_title(_("Problem"))
                status.set_subtitle(self.app.sync.keep_message)
                status.set_icon_name("dialog-warning-symbolic")
            add(status)
            off = Adw.ButtonRow(title=_("Disconnect"))
            off.add_css_class("destructive-action")
            off.connect("activated", self._keep_disconnect)
            add(off)
            return

        group.set_description(_(
            "Google has no public Keep API for personal accounts, so Rosk uses the unofficial "
            "gkeepapi library. It may stop working if Google changes Keep. Tasks are not affected."))
        default_email = self.meta.get_meta("account_email") or ""
        self.keep_email = Adw.EntryRow(title=_("Email"), text=default_email)
        add(self.keep_email)

        from .keep_login import INSTALL_HINT, webkit_available

        sign_in = Adw.ButtonRow(title=_("Sign in with Google…"), start_icon_name="avatar-default-symbolic")
        sign_in.add_css_class("suggested-action")
        if webkit_available():
            sign_in.connect("activated", self._keep_sign_in)
        else:
            sign_in.set_sensitive(False)
            sign_in.set_title(_("Sign-in window needs: {}").format(INSTALL_HINT))
        add(sign_in)

        manual = Adw.ExpanderRow(title=_("Enter the token manually"),
                                 subtitle=_("If the sign-in window does not work"))
        steps = Adw.ActionRow(
            title=_("1. Sign in at accounts.google.com/EmbeddedSetup"),
            subtitle=_("2. Accept, then copy the “oauth_token” cookie (Developer Tools → Storage → Cookies)"),
            activatable=True, subtitle_lines=3)
        steps.add_suffix(Gtk.Image.new_from_icon_name("adw-external-link-symbolic"))
        steps.connect("activated", lambda *_: Gtk.UriLauncher(
            uri="https://accounts.google.com/EmbeddedSetup").launch(self.get_root(), None, None, None))
        manual.add_row(steps)
        self.keep_token = Adw.PasswordEntryRow(title=_("oauth_token cookie"))
        manual.add_row(self.keep_token)
        connect = Adw.ButtonRow(title=_("Connect Keep"))
        connect.connect("activated", self._keep_connect)
        manual.add_row(connect)
        add(manual)
        self._keep_connect_row = connect

    def _keep_sign_in(self, row):
        from .keep_login import KeepLoginWindow

        email = self.keep_email.get_text().strip()
        if not email:
            self.add_toast(Adw.Toast(title=_("Enter the email of the Keep account")))
            return
        win = KeepLoginWindow(self.get_root(), email)
        win.connect("finished", self._keep_signed_in)
        win.present()

    def _keep_signed_in(self, _win, ok, email, value):
        if not ok:
            if value:
                self.add_toast(Adw.Toast(title=_("Keep login failed: {}").format(value)))
            return
        from ..auth import secrets

        try:
            secrets.store_keep_token(email, value)
        except Exception as e:
            self.add_toast(Adw.Toast(title=_("Keep login failed: {}").format(e)))
            return
        self._set("keep_email", email)
        self.app.sync.now()
        self._fill_keep()
        self.add_toast(Adw.Toast(title=_("Keep connected")))

    def _keep_connect(self, row):
        email = self.keep_email.get_text().strip()
        cookie = self.keep_token.get_text().strip()
        if not email or not cookie:
            self.add_toast(Adw.Toast(title=_("Enter the email and the oauth_token cookie")))
            return
        row.set_sensitive(False)
        row.set_title(_("Connecting…"))

        def work():
            from ..auth import secrets
            try:
                token = secrets.exchange_oauth_token(email, cookie)
                secrets.store_keep_token(email, token)
                error = None
            except Exception as e:
                error = str(e)
            GLib.idle_add(done, error)

        def done(error):
            if error:
                row.set_sensitive(True)
                row.set_title(_("Connect Keep"))
                self.add_toast(Adw.Toast(title=_("Keep login failed: {}").format(error)))
            else:
                self._set("keep_email", email)
                self.app.sync.now()
                self._fill_keep()
                self.add_toast(Adw.Toast(title=_("Keep connected")))
            return False

        threading.Thread(target=work, daemon=True).start()

    def _keep_disconnect(self, *_a):
        from ..auth import secrets

        email = self.meta.get_meta("keep_email")
        try:
            secrets.forget_keep_token(email)
        except Exception:
            pass
        self._set("keep_email", None)
        self._fill_keep()
