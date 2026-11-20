"""Small quick-capture window (used by the tray icon and when the Shell extension is absent)."""

from gi.repository import Adw, Gdk, Gtk

from ..core import dates, quickparse
from ..i18n import _, ngettext
from .complete import TagCompleter
from .widgets import chip

PLACEHOLDERS = {
    quickparse.TASK: _("Add a task…  tomorrow  #list"),
    quickparse.NOTE: _("Take a note…  #label   Title: - item - item"),
}


class QuickWindow(Adw.Window):
    def __init__(self, app):
        super().__init__(application=app, title=_("Quick Add"), default_width=520,
                         resizable=False, modal=False, hide_on_close=True)
        self.app = app
        self.add_css_class("quick-window")
        self.ignore = set()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=14,
                      margin_bottom=14, margin_start=14, margin_end=14)

        self.mode = Adw.ToggleGroup(halign=Gtk.Align.START)
        self.mode.add_css_class("round")
        self.mode.add(Adw.Toggle(name=quickparse.TASK, label=_("Task"),
                                 icon_name="object-select-symbolic", tooltip=_("Task (Alt+T)")))
        self.mode.add(Adw.Toggle(name=quickparse.NOTE, label=_("Note"),
                                 icon_name="document-edit-symbolic", tooltip=_("Note (Alt+N)")))
        self.mode.set_active_name(app.repo.quick_mode())
        self.mode.connect("notify::active-name", self._mode_changed)
        box.append(self.mode)

        self.entry = Gtk.Entry()
        self.entry.connect("changed", self._changed)
        self.entry.connect("activate", lambda *_: self.save(keep_open=False))
        box.append(self.entry)
        self.preview = Gtk.Box(spacing=6)
        box.append(self.preview)
        self.hint = Gtk.Label(label=_("Enter to save · Shift+Enter to add more · Esc to close"), xalign=0)
        self.hint.add_css_class("dim-label")
        self.hint.add_css_class("caption")
        box.append(self.hint)
        self.set_content(box)

        self.completer = TagCompleter(self.entry, self._tag_names)
        self._apply_mode()

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close)

    def _on_close(self, *_a):
        # Esc / close discards what was typed; the window itself is kept for next time
        self.completer.hide()
        self.entry.set_text("")
        return False

    # ---------- mode ----------
    @property
    def kind(self):
        return self.mode.get_active_name() or quickparse.TASK

    def _mode_changed(self, *_a):
        self.app.repo.set_quick_mode(self.kind)
        self._apply_mode()
        self.entry.grab_focus_without_selecting()

    def _apply_mode(self):
        self.entry.set_placeholder_text(PLACEHOLDERS[self.kind])
        icon = "list-add-symbolic" if self.kind == quickparse.TASK else "document-edit-symbolic"
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, icon)
        self._changed(self.entry)

    def _parse(self):
        return quickparse.parse(self.entry.get_text(), ignore=self.ignore, default_kind=self.kind)

    def _tag_names(self):
        repo = self.app.repo
        is_note = self._parse().kind == quickparse.NOTE
        return [c["title"] for c in (repo.labels() if is_note else repo.tasklists())]

    # ---------- keys ----------
    def _key(self, _c, keyval, _code, state):
        if self.completer.handle_key(keyval):
            return True
        if state & Gdk.ModifierType.ALT_MASK and keyval in (Gdk.KEY_t, Gdk.KEY_T):
            self.mode.set_active_name(quickparse.TASK)
            return True
        if state & Gdk.ModifierType.ALT_MASK and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.mode.set_active_name(quickparse.NOTE)
            return True
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            # GtkEntry only activates on a plain Enter, so both cases are handled here
            self.save(keep_open=bool(state & Gdk.ModifierType.SHIFT_MASK))
            return True
        return False

    # ---------- preview ----------
    def _changed(self, entry):
        if not entry.get_text():
            self.ignore.clear()
        r = self._parse()
        while (c := self.preview.get_first_child()) is not None:
            self.preview.remove(c)
        for sp in r.spans:
            if sp.kind == "date":
                text = dates.chip_text(sp.value)
            elif sp.kind == "list":
                kind = "label" if r.kind == quickparse.NOTE else "tasklist"
                col = self.app.repo.find_collection(kind, sp.value)
                text = col["title"] if col else _("New: {}").format(sp.value.replace("_", " "))
            elif sp.kind == "note":
                text = _("Note")
            else:
                n = len(sp.value)
                text = (ngettext("{} item", "{} items", n) if r.kind == quickparse.NOTE
                        else ngettext("{} subtask", "{} subtasks", n)).format(n)
            btn = Gtk.Button(child=chip(text, "preview-" + sp.kind), tooltip_text=_("Click to keep as text"))
            btn.add_css_class("flat")
            btn.add_css_class("chip")
            btn.connect("clicked", lambda _b, s=sp.start: (self.ignore.add(s), self._changed(self.entry)))
            self.preview.append(btn)

    # ---------- save ----------
    def save(self, keep_open):
        text = self.entry.get_text().strip()
        if text:
            self.app.repo.quick_add(text, ignore=self.ignore, kind=self.kind)
            self.app.sync.request()
        self.entry.set_text("")
        if keep_open:
            self.entry.grab_focus()
        else:
            self.close()

    def popup(self):
        # another entry point (the panel extension) may have switched the mode
        if self.mode.get_active_name() != self.app.repo.quick_mode():
            self.mode.set_active_name(self.app.repo.quick_mode())
        self.present()
        self.entry.grab_focus()
