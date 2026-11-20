"""Suggestions for #list / #label while typing in a quick-capture entry."""

import re

from gi.repository import Gdk, GLib, Gtk, Pango

from ..core.quickparse import tag_for

_TOKEN = re.compile(r"(?<![\w#])#([\w\-./]*)$", re.U)


class TagCompleter:
    """Shows a popover under `entry` with names matching the #token at the cursor.

    `names()` returns the candidate names. The owner must forward key presses to
    `handle_key()` before its own Enter handling.
    """

    def __init__(self, entry, names, icon="view-list-symbolic"):
        self.entry = entry
        self.names = names
        self.icon = icon
        self.matches = []
        self._token_start = None
        self._suppress = False

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", lambda _l, row: self.accept(row.get_index()))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_height=True, propagate_natural_width=True,
                                      max_content_height=260, min_content_width=220)
        scroller.set_child(self.list)
        self.popover = Gtk.Popover(child=scroller, autohide=False, has_arrow=False,
                                   position=Gtk.PositionType.BOTTOM, can_focus=False)
        self.popover.set_halign(Gtk.Align.START)
        self.popover.set_parent(entry)
        entry.connect("changed", self._on_changed)
        entry.connect("notify::cursor-position", self._on_changed)
        entry.connect("unrealize", lambda *_: self.popover.popdown())
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: GLib.timeout_add(150, self._hide_if_unfocused))
        entry.add_controller(focus)

    # ---------- matching ----------
    def _on_changed(self, *_a):
        if self._suppress:
            return
        text = self.entry.get_text()
        pos = self.entry.get_position()
        m = _TOKEN.search(text[:pos])
        if not m:
            self.hide()
            return
        prefix = m.group(1).replace("_", " ").lower()
        names = self.names()
        starts = [n for n in names if n.lower().startswith(prefix)]
        contains = [n for n in names if prefix and prefix in n.lower() and n not in starts]
        self.matches = (starts + contains)[:8]
        exact = any(tag_for(n).lower() == m.group(1).lower() for n in self.matches)
        if not self.matches or (exact and len(self.matches) == 1):
            self.hide()
            return
        self._token_start = m.start()
        self._fill(prefix)

    def _fill(self, prefix):
        while (child := self.list.get_first_child()) is not None:
            self.list.remove(child)
        for name in self.matches:
            box = Gtk.Box(spacing=8, margin_start=4, margin_end=8, margin_top=2, margin_bottom=2)
            box.append(Gtk.Image.new_from_icon_name(self.icon))
            label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                              width_chars=16, max_width_chars=40)
            i = name.lower().find(prefix) if prefix else -1
            if i >= 0:
                esc = GLib.markup_escape_text
                label.set_markup(esc(name[:i]) + "<b>" + esc(name[i:i + len(prefix)]) + "</b>"
                                 + esc(name[i + len(prefix):]))
            else:
                label.set_text(name)
            box.append(label)
            self.list.append(box)
        self.list.select_row(self.list.get_row_at_index(0))
        if not self.popover.get_visible():
            self.popover.popup()

    # ---------- keys ----------
    @property
    def visible(self):
        return self.popover.get_visible()

    def handle_key(self, keyval):
        if not self.visible:
            return False
        row = self.list.get_selected_row()
        idx = row.get_index() if row else 0
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            self.list.select_row(self.list.get_row_at_index(min(idx + 1, len(self.matches) - 1)))
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            self.list.select_row(self.list.get_row_at_index(max(idx - 1, 0)))
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter, Gdk.KEY_Tab):
            self.accept(idx)
            return True
        if keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def accept(self, idx):
        if not self.matches or self._token_start is None:
            return
        name = self.matches[idx]
        text = self.entry.get_text()
        pos = self.entry.get_position()
        end = pos
        while end < len(text) and not text[end].isspace():
            end += 1
        insert = "#" + tag_for(name) + " "
        new = text[:self._token_start] + insert + text[end:].lstrip(" ")
        self._suppress = True
        self.entry.set_text(new)
        self.entry.set_position(self._token_start + len(insert))
        self._suppress = False
        self.hide()

    def hide(self):
        self.matches = []
        if self.popover.get_visible():
            self.popover.popdown()

    def _hide_if_unfocused(self):
        if not self.entry.has_focus() and not (self.entry.get_delegate() and
                                               self.entry.get_delegate().has_focus()):
            self.hide()
        return False
