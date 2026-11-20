"""Sign-in window for Google Keep.

Opens Google's sign-in page (accounts.google.com/EmbeddedSetup) in Rosk's own private,
temporary browser view. After the user signs in, Google sets a one-time `oauth_token`
cookie in that view; Rosk exchanges it for a Keep token, stores it in the keyring and
discards the temporary browser session. Rosk never reads cookies of the user's browsers.
"""

import threading

import gi
from gi.repository import Adw, GLib, GObject, Gtk

from ..i18n import _

SIGN_IN_URL = "https://accounts.google.com/EmbeddedSetup"


def webkit_available():
    try:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit  # noqa: F401
        return True
    except (ValueError, ImportError):
        return False


INSTALL_HINT = "sudo apt install gir1.2-webkit-6.0"


class KeepLoginWindow(Adw.Window):
    __gsignals__ = {
        # (email, master_token) on success; ("", error) on failure
        "finished": (GObject.SignalFlags.RUN_LAST, None, (bool, str, str)),
    }

    def __init__(self, parent, email):
        super().__init__(title=_("Sign in to Google Keep"), modal=True, transient_for=parent,
                         default_width=480, default_height=680)
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit

        self.email = email
        self._done = False

        self.session = WebKit.NetworkSession.new_ephemeral()
        self.cookies = self.session.get_cookie_manager()
        self.cookies.connect("changed", lambda *_: self._check_cookies())
        self.view = WebKit.WebView(network_session=self.session, vexpand=True)
        self.view.connect("load-changed", lambda *_: self._check_cookies())

        tb = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.status = Adw.WindowTitle(title=_("Sign in to Google Keep"), subtitle=email)
        header.set_title_widget(self.status)
        tb.add_top_bar(header)
        self.stack = Gtk.Stack()
        self.stack.add_named(self.view, "web")
        wait = Adw.StatusPage(title=_("Connecting Keep…"), child=Adw.Spinner(height_request=32))
        self.stack.add_named(wait, "wait")
        tb.set_content(self.stack)
        self.set_content(tb)
        self.connect("close-request", self._on_close)
        self.view.load_uri(SIGN_IN_URL)

    def _check_cookies(self):
        if self._done:
            return
        self.cookies.get_cookies("https://accounts.google.com/", None, self._got_cookies)

    def _got_cookies(self, manager, result):
        if self._done:
            return
        try:
            cookies = manager.get_cookies_finish(result)
        except GLib.Error:
            return
        for cookie in cookies:
            if cookie.get_name() == "oauth_token" and cookie.get_value():
                self._done = True
                self._exchange(cookie.get_value())
                return

    def _exchange(self, oauth_token):
        self.stack.set_visible_child_name("wait")
        self.view.stop_loading()

        def work():
            from ..auth import secrets
            try:
                token = secrets.exchange_oauth_token(self.email, oauth_token)
                GLib.idle_add(self._finish, True, self.email, token)
            except Exception as e:
                GLib.idle_add(self._finish, False, "", str(e))

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, ok, email, value):
        self.emit("finished", ok, email, value)
        self.destroy()
        return False

    def _on_close(self, *_a):
        if not self._done:
            self._done = True
            self.emit("finished", False, "", "")
        return False
