"""Small quick-capture window (used by the tray icon and when the Shell extension is absent)."""

from gi.repository import Adw, Gdk, Gtk

from ..core import dates, quickparse
from ..i18n import _, ngettext
from .widgets import chip


class QuickWindow(Adw.Window):
    def __init__(self, app):
        super().__init__(application=app, title=_("Quick Add"), default_width=520,
                         resizable=False, modal=False)
        self.app = app
        self.add_css_class("quick-window")
        self.ignore = set()

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=14,
                      margin_bottom=14, margin_start=14, margin_end=14)
        self.entry = Gtk.Entry(placeholder_text=_("Add a task…  tomorrow  #list   (n: for a note)"))
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "list-add-symbolic")
        self.entry.connect("changed", self._changed)
        self.entry.connect("activate", self._activate)
        box.append(self.entry)
        self.preview = Gtk.Box(spacing=6)
        box.append(self.preview)
        self.hint = Gtk.Label(label=_("Enter to save · Shift+Enter to add more · Esc to close"), xalign=0)
        self.hint.add_css_class("dim-label")
        self.hint.add_css_class("caption")
        box.append(self.hint)
        self.set_content(box)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._shift = False

    def _key(self, _c, keyval, _code, state):
        self._shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _changed(self, entry):
        if not entry.get_text():
            self.ignore.clear()
        r = quickparse.parse(entry.get_text(), ignore=self.ignore)
        while (c := self.preview.get_first_child()) is not None:
            self.preview.remove(c)
        for sp in r.spans:
            if sp.kind == "date":
                text = dates.chip_text(sp.value)
            elif sp.kind == "list":
                text = "#" + sp.value
            elif sp.kind == "note":
                text = _("Note")
            else:
                text = ngettext("{} subtask", "{} subtasks", len(sp.value)).format(len(sp.value))
            btn = Gtk.Button(child=chip(text, "preview-" + sp.kind), tooltip_text=_("Click to keep as text"))
            btn.add_css_class("flat")
            btn.add_css_class("chip")
            btn.connect("clicked", lambda _b, s=sp.start: (self.ignore.add(s), self._changed(self.entry)))
            self.preview.append(btn)

    def _activate(self, entry):
        text = entry.get_text().strip()
        if text:
            self.app.repo.quick_add(text, ignore=self.ignore)
            self.app.sync.request()
        entry.set_text("")
        if not self._shift:
            self.close()

    def popup(self):
        self.present()
        self.entry.grab_focus()
