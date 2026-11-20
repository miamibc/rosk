"""Fallback panel icon through StatusNotifierItem (AppIndicator).

Shown only while the Rosk GNOME Shell extension is not active — for example right
after installation, before the next login, or when the extension is turned off.
"""

import logging

from gi.repository import Gio, GLib

from ..i18n import _

log = logging.getLogger(__name__)

EXTENSION_UUID = "rosk@miamibc.github.io"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/StatusNotifierItem/Menu"

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="XAyatanaLabel" type="s" access="read"/>
    <property name="XAyatanaLabelGuide" type="s" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
    <signal name="XAyatanaNewLabel"><arg name="label" type="s"/><arg name="guide" type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/><arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/><arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/><arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/><arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/><arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
    <signal name="ItemActivationRequested"><arg type="i" name="id"/><arg type="u" name="timestamp"/></signal>
  </interface>
</node>
"""


class Tray:
    def __init__(self, app, icon_name, icon_dir):
        self.app = app
        self.icon_name = icon_name
        self.icon_dir = icon_dir
        self.conn = None
        self.status = "Passive"
        self.label = ""
        self._ids = []
        self._registered = False
        self._watch = None
        self._ext_sub = None
        self._revision = 1

    # ---------- menu ----------
    def _items(self):
        return [
            (1, {"label": _("Quick Add…")}, "quick"),
            (2, {"label": _("Open Rosk")}, "open"),
            (3, {"type": "separator"}, None),
            (4, {"label": _("Sync Now")}, "sync"),
            (5, {"type": "separator"}, None),
            (6, {"label": _("Quit")}, "quit"),
        ]

    @staticmethod
    def _props(props):
        out = {"visible": GLib.Variant("b", True), "enabled": GLib.Variant("b", True)}
        for k, v in props.items():
            out[k] = GLib.Variant("s", v)
        return out

    def _layout(self):
        children = [GLib.Variant("(ia{sv}av)", (iid, self._props(p), []))
                    for iid, p, _a in self._items()]
        root = (0, {"children-display": GLib.Variant("s", "submenu")}, children)
        return root

    def _menu_call(self, conn, sender, path, iface, method, params, inv):
        if method == "GetLayout":
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (self._revision, self._layout())))
        elif method == "GetGroupProperties":
            ids = params.unpack()[0]
            res = [(iid, self._props(p)) for iid, p, _a in self._items() if not ids or iid in ids]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (res,)))
        elif method == "GetProperty":
            iid, name = params.unpack()
            for i, p, _a in self._items():
                if i == iid and name in p:
                    inv.return_value(GLib.Variant("(v)", (GLib.Variant("s", p[name]),)))
                    return
            inv.return_dbus_error("com.canonical.dbusmenu.Error", "no such property")
        elif method == "Event":
            iid, event, _data, _ts = params.unpack()
            if event == "clicked":
                self._activate_menu(iid)
            inv.return_value(None)
        elif method == "EventGroup":
            for iid, event, _data, _ts in params.unpack()[0]:
                if event == "clicked":
                    self._activate_menu(iid)
            inv.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _menu_prop(self, conn, sender, path, iface, name):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"),
                "IconThemePath": GLib.Variant("as", [])}.get(name)

    def _activate_menu(self, iid):
        action = {i: a for i, _p, a in self._items()}.get(iid)
        GLib.idle_add(self._run_action, action)

    def _run_action(self, action):
        if action == "quick":
            self.app.activate_action("quick-add", None)
        elif action == "open":
            self.app.activate()
        elif action == "sync":
            self.app.sync.now()
        elif action == "quit":
            self.app.activate_action("quit", None)
        return False

    # ---------- item ----------
    def _item_call(self, conn, sender, path, iface, method, params, inv):
        if method == "Activate":
            GLib.idle_add(self._run_action, "quick")
        elif method == "SecondaryActivate":
            GLib.idle_add(self._run_action, "open")
        inv.return_value(None)

    def _item_prop(self, conn, sender, path, iface, name):
        tooltip = _("Rosk — {} due").format(self.label) if self.label else "Rosk"
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "rosk"),
            "Title": GLib.Variant("s", "Rosk"),
            "Status": GLib.Variant("s", self.status),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", self.icon_name),
            "IconThemePath": GLib.Variant("s", self.icon_dir),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], tooltip, "")),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
            "XAyatanaLabel": GLib.Variant("s", self.label),
            "XAyatanaLabelGuide": GLib.Variant("s", "99"),
        }
        return values.get(name)

    # ---------- lifecycle ----------
    def start(self, conn):
        self.conn = conn
        item_info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._ids.append(conn.register_object(ITEM_PATH, item_info, self._item_call,
                                              self._item_prop, None))
        self._ids.append(conn.register_object(MENU_PATH, menu_info, self._menu_call,
                                              self._menu_prop, None))
        self._watch = Gio.bus_watch_name_on_connection(
            conn, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
            self._watcher_appeared, self._watcher_vanished)
        self._ext_sub = conn.signal_subscribe(
            "org.gnome.Shell.Extensions", "org.gnome.Shell.Extensions", "ExtensionStateChanged",
            "/org/gnome/Shell/Extensions", None, Gio.DBusSignalFlags.NONE, self._ext_changed)
        self._check_extension()

    def stop(self):
        if not self.conn:
            return
        for rid in self._ids:
            self.conn.unregister_object(rid)
        self._ids = []
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
        if self._ext_sub:
            self.conn.signal_unsubscribe(self._ext_sub)

    def _watcher_appeared(self, conn, name, owner):
        conn.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                  "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (ITEM_PATH,)), None, Gio.DBusCallFlags.NONE, -1, None,
                  self._registered_cb)

    def _registered_cb(self, conn, res):
        try:
            conn.call_finish(res)
            self._registered = True
        except GLib.Error as e:
            log.info("tray icon not registered: %s", e.message)

    def _watcher_vanished(self, *_a):
        self._registered = False

    def _check_extension(self):
        self.conn.call("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions",
                       "org.gnome.Shell.Extensions", "GetExtensionInfo",
                       GLib.Variant("(s)", (EXTENSION_UUID,)), None, Gio.DBusCallFlags.NONE, -1,
                       None, self._ext_info_cb)

    def _ext_info_cb(self, conn, res):
        active = False
        try:
            info = conn.call_finish(res).unpack()[0]
            active = int(info.get("state", 0)) == 1
        except GLib.Error:
            pass
        self._set_status("Passive" if active else "Active")

    def _ext_changed(self, conn, sender, path, iface, signal, params):
        uuid, info = params.unpack()
        if uuid == EXTENSION_UUID:
            self._set_status("Passive" if int(info.get("state", 0)) == 1 else "Active")

    def _set_status(self, status):
        if status == self.status:
            return
        self.status = status
        self._emit("NewStatus", GLib.Variant("(s)", (status,)))

    def set_count(self, n):
        label = str(n) if n else ""
        if label == self.label:
            return
        self.label = label
        self._emit("XAyatanaNewLabel", GLib.Variant("(ss)", (label, "99")))
        self._emit("NewToolTip", None)

    def _emit(self, signal, params):
        if self.conn:
            try:
                self.conn.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", signal, params)
            except GLib.Error as e:
                log.debug("tray signal failed: %s", e.message)
