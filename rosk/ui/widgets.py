"""Task rows, note cards and chips."""

import json

from gi.repository import GLib, GObject, Gtk, Pango

from ..core import dates
from ..i18n import _


def chip(text, *classes, icon=None, tooltip=None):
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
    box.add_css_class("chip")
    for c in classes:
        box.add_css_class(c)
    if icon:
        img = Gtk.Image.new_from_icon_name(icon)
        img.set_pixel_size(12)
        box.append(img)
    label = Gtk.Label(label=text, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
    box.append(label)
    if tooltip:
        box.set_tooltip_text(tooltip)
    return box


def due_chip(due):
    state = dates.due_state(due)
    return chip(dates.chip_text(due), state or "", icon="alarm-symbolic",
                tooltip=dates.parse_iso(due).strftime("%x"))


def link_icon(link_type):
    return {"email": "mail-unread-symbolic"}.get(link_type, "emblem-symbolic-link-symbolic")


class TaskRow(Gtk.ListBoxRow):
    __gsignals__ = {
        "toggled": (GObject.SignalFlags.RUN_LAST, None, (bool,)),
    }

    def __init__(self, item, list_title=None, subtasks=(0, 0), is_subtask=False):
        super().__init__()
        self.item = item
        self.item_id = item["id"]
        self.add_css_class("task-row")
        if is_subtask:
            self.add_css_class("subtask")
        if item["status"] == "done":
            self.add_css_class("done")

        box = Gtk.Box(spacing=10, margin_start=36 if is_subtask else 6, margin_end=6)
        self.check = Gtk.CheckButton(active=item["status"] == "done", valign=Gtk.Align.START)
        self.check.add_css_class("selection-mode")
        self.check.set_tooltip_text(_("Mark as done"))
        self._handler = self.check.connect("toggled", self._on_toggled)
        box.append(self.check)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True,
                       valign=Gtk.Align.CENTER)
        title = Gtk.Label(label=item["title"] or _("Untitled"), xalign=0, wrap=True,
                          wrap_mode=Pango.WrapMode.WORD_CHAR, lines=3,
                          ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("task-title")
        if not item["title"]:
            title.add_css_class("dim-label")
        text.append(title)

        body = (item["body"] or "").strip().splitlines()
        if body:
            b = Gtk.Label(label=body[0], xalign=0, ellipsize=Pango.EllipsizeMode.END)
            b.add_css_class("task-body")
            text.append(b)

        chips = Gtk.Box(spacing=6)
        if item["due_date"]:
            chips.append(due_chip(item["due_date"]))
        done, total = subtasks
        if total:
            chips.append(chip(f"{done}/{total}", icon="view-list-bullet-symbolic",
                              tooltip=_("Subtasks")))
        for link in json.loads(item["links_json"] or "[]"):
            btn = Gtk.Button(child=chip(link.get("description") or link.get("type", ""), "link",
                                        icon=link_icon(link.get("type"))))
            btn.add_css_class("flat")
            btn.add_css_class("chip")
            btn.set_tooltip_text(_("Open source"))
            btn.connect("clicked", lambda _b, url=link.get("link"): Gtk.UriLauncher(uri=url).launch(
                self.get_root(), None, None, None))
            chips.append(btn)
        if item["assignment_json"]:
            chips.append(chip(_("Assigned"), icon="avatar-default-symbolic"))
        if list_title:
            chips.append(chip(list_title, "list", icon="view-list-symbolic"))
        if chips.get_first_child():
            text.append(chips)
        box.append(text)
        self.set_child(box)

    def _on_toggled(self, check):
        done = check.get_active()
        if done:
            self.add_css_class("crossing")
            GLib.timeout_add(450, lambda: (self.emit("toggled", True), False)[1])
        else:
            self.remove_css_class("crossing")
            self.remove_css_class("done")
            self.emit("toggled", False)

    def toggle(self):
        self.check.set_active(not self.check.get_active())


class GroupHeader(Gtk.ListBoxRow):
    def __init__(self, text, css=None):
        super().__init__(activatable=False, selectable=False, focusable=False)
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("group-header")
        if css:
            label.add_css_class(css)
        self.set_child(label)


class NoteCard(Gtk.FlowBoxChild):
    __gsignals__ = {
        "checklist-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, item, label_titles=()):
        super().__init__()
        self.item = item
        self.item_id = item["id"]
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("note-card")
        if item["color"] and item["color"] != "DEFAULT":
            card.add_css_class("note-" + item["color"])

        head = Gtk.Box(spacing=6)
        if item["title"]:
            t = Gtk.Label(label=item["title"], xalign=0, hexpand=True, wrap=True,
                          wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2,
                          ellipsize=Pango.EllipsizeMode.END)
            t.add_css_class("note-title")
            head.append(t)
        else:
            head.append(Gtk.Box(hexpand=True))
        if item["pinned"]:
            pin = Gtk.Image.new_from_icon_name("view-pin-symbolic")
            pin.set_opacity(0.6)
            head.append(pin)
        card.append(head)

        if item["checklist_json"]:
            entries = json.loads(item["checklist_json"])
            for idx, entry in enumerate(entries[:8]):
                cb = Gtk.CheckButton(label=entry.get("text", ""), active=bool(entry.get("checked")))
                cb.connect("toggled", self._on_check, idx)
                card.append(cb)
            if len(entries) > 8:
                more = Gtk.Label(label=_("+ {} more").format(len(entries) - 8), xalign=0)
                more.add_css_class("dim-label")
                card.append(more)
        elif item["body"]:
            b = Gtk.Label(label=item["body"], xalign=0, wrap=True, lines=10,
                          wrap_mode=Pango.WrapMode.WORD_CHAR, ellipsize=Pango.EllipsizeMode.END)
            b.add_css_class("note-body")
            card.append(b)

        if label_titles:
            chips = Gtk.Box(spacing=4)
            for t in label_titles:
                chips.append(chip(t, "list", icon="tag-symbolic"))
            card.append(chips)
        self.set_child(card)

    def _on_check(self, cb, idx):
        entries = json.loads(self.item["checklist_json"])
        entries[idx]["checked"] = cb.get_active()
        self.item["checklist_json"] = json.dumps(entries, ensure_ascii=False)
        self.emit("checklist-changed", self.item["checklist_json"])


def empty_state(icon, title, description=""):
    from gi.repository import Adw

    page = Adw.StatusPage(icon_name=icon, title=title, description=description, vexpand=True)
    page.add_css_class("compact")
    return page
