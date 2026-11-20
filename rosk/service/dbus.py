"""D-Bus API for the GNOME Shell extension and the Shell search provider."""

import json
import logging

from gi.repository import Gio, GLib

from .. import SEARCH_PATH, SERVICE_IFACE, SERVICE_PATH
from ..core import dates, quickparse
from ..i18n import _, ngettext

log = logging.getLogger(__name__)

SERVICE_XML = f"""
<node>
  <interface name="{SERVICE_IFACE}">
    <method name="QuickAdd"><arg type="s" name="text" direction="in"/><arg type="s" name="id" direction="out"/></method>
    <method name="Preview"><arg type="s" name="text" direction="in"/><arg type="s" name="json" direction="out"/></method>
    <method name="Due"><arg type="s" name="json" direction="out"/></method>
    <method name="Counts"><arg type="s" name="json" direction="out"/></method>
    <method name="Complete"><arg type="s" name="id" direction="in"/></method>
    <method name="Open"><arg type="s" name="id" direction="in"/></method>
    <method name="Sync"/>
    <signal name="Changed"><arg type="s" name="counts"/></signal>
  </interface>
</node>
"""

SEARCH_XML = """
<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet"><arg type="as" name="terms" direction="in"/><arg type="as" name="results" direction="out"/></method>
    <method name="GetSubsearchResultSet"><arg type="as" name="previous_results" direction="in"/><arg type="as" name="terms" direction="in"/><arg type="as" name="results" direction="out"/></method>
    <method name="GetResultMetas"><arg type="as" name="identifiers" direction="in"/><arg type="aa{sv}" name="metas" direction="out"/></method>
    <method name="ActivateResult"><arg type="s" name="identifier" direction="in"/><arg type="as" name="terms" direction="in"/><arg type="u" name="timestamp" direction="in"/></method>
    <method name="LaunchSearch"><arg type="as" name="terms" direction="in"/><arg type="u" name="timestamp" direction="in"/></method>
  </interface>
</node>
"""


class ServiceApi:
    def __init__(self, app):
        self.app = app
        self.repo = app.repo
        self.connection = None
        self._ids = []

    def register(self, connection):
        self.connection = connection
        for xml, path, handler in ((SERVICE_XML, SERVICE_PATH, self._service_call),
                                   (SEARCH_XML, SEARCH_PATH, self._search_call)):
            info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
            self._ids.append(connection.register_object(path, info, handler, None, None))

    def unregister(self, connection):
        for rid in self._ids:
            connection.unregister_object(rid)
        self._ids = []

    def emit_changed(self):
        if self.connection is None:
            return
        try:
            self.connection.emit_signal(None, SERVICE_PATH, SERVICE_IFACE, "Changed",
                                        GLib.Variant("(s)", (json.dumps(self.counts()),)))
        except GLib.Error as e:
            log.debug("emit Changed failed: %s", e)

    # ---------- data ----------
    def counts(self):
        c = self.repo.counts()
        return {"today": c["today"], "overdue": c["overdue"], "upcoming": c["upcoming"]}

    def due(self, limit=12):
        titles = {c["id"]: c["title"] for c in self.repo.tasklists()}
        out = []
        for item in self.repo.today_tasks()[:limit]:
            out.append({"id": item["id"], "title": item["title"] or _("Untitled"),
                        "chip": dates.chip_text(item["due_date"]),
                        "state": dates.due_state(item["due_date"]),
                        "list": titles.get(item["collection_id"], "")})
        return out

    def preview(self, text):
        r = quickparse.parse(text)
        chips = []
        for sp in r.spans:
            if sp.kind == "date":
                chips.append({"kind": "date", "text": dates.chip_text(sp.value)})
            elif sp.kind == "list":
                chips.append({"kind": "list", "text": "#" + sp.value})
            elif sp.kind == "note":
                chips.append({"kind": "note", "text": _("Note")})
            else:
                chips.append({"kind": "subtasks", "text": ngettext(
                    "{} subtask", "{} subtasks", len(sp.value)).format(len(sp.value))})
        return {"title": r.title, "chips": chips}

    # ---------- handlers ----------
    def _service_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            args = params.unpack()
            if method == "QuickAdd":
                iid = self.repo.quick_add(args[0]) or ""
                self.app.sync.request()
                invocation.return_value(GLib.Variant("(s)", (iid,)))
            elif method == "Preview":
                invocation.return_value(GLib.Variant("(s)", (json.dumps(self.preview(args[0])),)))
            elif method == "Due":
                invocation.return_value(GLib.Variant("(s)", (json.dumps(self.due()),)))
            elif method == "Counts":
                invocation.return_value(GLib.Variant("(s)", (json.dumps(self.counts()),)))
            elif method == "Complete":
                if self.repo.item(args[0]):
                    self.repo.set_done(args[0], True)
                    self.app.sync.request()
                invocation.return_value(None)
            elif method == "Open":
                self.app.open_item(args[0] or None)
                invocation.return_value(None)
            elif method == "Sync":
                self.app.sync.now()
                invocation.return_value(None)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as e:
            log.exception("D-Bus %s failed", method)
            invocation.return_dbus_error("io.github.miamibc.rosk.Error", str(e))

    def _search(self, terms):
        text = " ".join(terms).strip()
        if len(text) < 2:
            return []
        return [r["id"] for r in self.repo.search(text)[:20]]

    def _search_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            args = params.unpack()
            if method == "GetInitialResultSet":
                invocation.return_value(GLib.Variant("(as)", (self._search(args[0]),)))
            elif method == "GetSubsearchResultSet":
                invocation.return_value(GLib.Variant("(as)", (self._search(args[1]),)))
            elif method == "GetResultMetas":
                metas = []
                for iid in args[0]:
                    item = self.repo.item(iid)
                    if not item:
                        continue
                    desc = []
                    if item["kind"] == "task":
                        if item["due_date"]:
                            desc.append(dates.chip_text(item["due_date"]))
                        if item["status"] == "done":
                            desc.append(_("Completed"))
                    else:
                        desc.append(_("Note"))
                    if item["body"]:
                        desc.append(item["body"].splitlines()[0][:80])
                    icon = Gio.ThemedIcon.new("object-select-symbolic" if item["kind"] == "task"
                                              else "document-edit-symbolic")
                    metas.append({
                        "id": GLib.Variant("s", iid),
                        "name": GLib.Variant("s", item["title"] or _("Untitled")),
                        "description": GLib.Variant("s", " · ".join(desc)),
                        "gicon": GLib.Variant("s", icon.to_string()),
                    })
                invocation.return_value(GLib.Variant("(aa{sv})", (metas,)))
            elif method == "ActivateResult":
                self.app.open_item(args[0])
                invocation.return_value(None)
            elif method == "LaunchSearch":
                self.app.open_search(" ".join(args[0]))
                invocation.return_value(None)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as e:
            log.exception("search %s failed", method)
            invocation.return_dbus_error("io.github.miamibc.rosk.Error", str(e))
