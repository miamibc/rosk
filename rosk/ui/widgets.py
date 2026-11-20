"""Task rows, note cards and chips."""

import json

from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from ..core import dates
from ..i18n import _, ngettext


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


MAX_CARD_ITEMS = 8
MAX_CARD_LINES = 10


def _preview_text(text, lines=MAX_CARD_LINES, chars=500):
    """GtkLabel's line limit ignores paragraphs, so the card text is cut here."""
    parts = text.strip().splitlines()
    cut = "\n".join(parts[:lines])
    if len(cut) > chars:
        cut = cut[:chars].rstrip()
    if len(parts) > lines or len(cut) < len("\n".join(parts[:lines])):
        cut += "…"
    return cut


class NoteCard(Gtk.Box):
    """Keep-like card: title, a few lines of text or unchecked items, labels."""

    __gsignals__ = {
        "checklist-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "open-note": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, item, label_titles=()):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, focusable=True,
                         overflow=Gtk.Overflow.HIDDEN)
        self.item = item
        self.item_id = item["id"]
        self.add_css_class("note-card")
        if item["color"] and item["color"] != "DEFAULT":
            self.add_css_class("note-" + item["color"])

        head = Gtk.Box(spacing=6)
        if item["title"]:
            head.append(self._label(item["title"], "note-title", lines=3, hexpand=True))
        else:
            head.append(Gtk.Box(hexpand=True))
        if item["pinned"]:
            pin = Gtk.Image.new_from_icon_name("view-pin-symbolic")
            pin.set_opacity(0.55)
            pin.set_valign(Gtk.Align.START)
            head.append(pin)
        if item["title"] or item["pinned"]:
            self.append(head)

        if item["checklist_json"]:
            self._checklist(json.loads(item["checklist_json"]))
        elif item["body"]:
            self.append(self._label(_preview_text(item["body"]), "note-body", lines=MAX_CARD_LINES))
        elif not item["title"]:
            self.append(self._label(_("Empty note"), "dim-label"))

        if label_titles:
            self.append(self._label(" · ".join(label_titles), "note-labels", lines=2))

        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    @staticmethod
    def _label(text, css, lines=1, hexpand=False):
        label = Gtk.Label(label=text, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          lines=lines, ellipsize=Pango.EllipsizeMode.END, hexpand=hexpand,
                          width_chars=1, max_width_chars=1)
        label.add_css_class(css)
        return label

    def _checklist(self, entries):
        unchecked = [(i, e) for i, e in enumerate(entries) if not e.get("checked")]
        checked = len(entries) - len(unchecked)
        for idx, entry in unchecked[:MAX_CARD_ITEMS]:
            row = Gtk.Box(spacing=6)
            cb = Gtk.CheckButton(valign=Gtk.Align.START)
            cb.add_css_class("note-check")
            cb.connect("toggled", self._on_check, idx)
            row.append(cb)
            row.append(self._label(entry.get("text", ""), "note-item", lines=2, hexpand=True))
            self.append(row)
        more = len(unchecked) - MAX_CARD_ITEMS
        if more > 0:
            self.append(self._label(_("+ {} more").format(more), "note-more"))
        if checked:
            self.append(self._label(ngettext("{} checked item", "{} checked items", checked)
                                    .format(checked), "note-more"))

    def _on_check(self, cb, idx):
        entries = json.loads(self.item["checklist_json"])
        entries[idx]["checked"] = cb.get_active()
        self.item["checklist_json"] = json.dumps(entries, ensure_ascii=False)
        self.emit("checklist-changed", self.item["checklist_json"])

    def _on_click(self, gesture, n_press, x, y):
        target = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while target is not None and target is not self:
            if isinstance(target, Gtk.CheckButton):
                return  # the checkbox handles it
            target = target.get_parent()
        self.grab_focus()
        self.emit("open-note")

    def _on_key(self, _c, keyval, _code, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.emit("open-note")
            return True
        return False


def empty_state(icon, title, description=""):
    from gi.repository import Adw

    page = Adw.StatusPage(icon_name=icon, title=title, description=description, vexpand=True)
    page.add_css_class("compact")
    return page
