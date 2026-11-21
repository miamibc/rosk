"""Side pane for editing a task or a note. Changes are saved as you type."""

import json
from datetime import date, timedelta

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ..core import dates
from ..i18n import _, ngettext
from .widgets import insertion_indicator, link_icon, media_picture, note_media

NOTE_COLORS = ["DEFAULT", "RED", "ORANGE", "YELLOW", "GREEN", "TEAL", "BLUE", "CERULEAN",
               "PURPLE", "PINK", "BROWN", "GRAY"]
COLOR_HEX = {"DEFAULT": "#ffffff", "RED": "#f28b82", "ORANGE": "#fbbc04", "YELLOW": "#fff475",
             "GREEN": "#ccff90", "TEAL": "#a7ffeb", "BLUE": "#cbf0f8", "CERULEAN": "#aecbfa",
             "PURPLE": "#d7aefb", "PINK": "#fdcfe8", "BROWN": "#e6c9a8", "GRAY": "#e8eaed"}


class Debounce:
    def __init__(self, ms, fn):
        self.ms, self.fn, self.src = ms, fn, None

    def __call__(self, *args):
        self.cancel()
        self.src = GLib.timeout_add(self.ms, self._fire, args)

    def _fire(self, args):
        self.src = None
        self.fn(*args)
        return False

    def flush(self):
        if self.src:
            GLib.source_remove(self.src)
            self.src = None
            self.fn()

    def cancel(self):
        if self.src:
            GLib.source_remove(self.src)
            self.src = None


def _text_of(view):
    buf = view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


class Editor(Gtk.Box):
    __gsignals__ = {
        "close-request": (GObject.SignalFlags.RUN_LAST, None, ()),
        "delete-request": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "open-item": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, repo):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.repo = repo
        self.item_id = None
        self._loading = False

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False, show_end_title_buttons=False,
                               show_start_title_buttons=False)
        close = Gtk.Button(icon_name="go-next-symbolic", tooltip_text=_("Close"))
        close.connect("clicked", lambda *_: self.emit("close-request"))
        header.pack_start(close)
        self.kind_label = Gtk.Label()
        self.kind_label.add_css_class("dim-label")
        header.set_title_widget(self.kind_label)
        self.menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=_("More"))
        header.pack_end(self.menu_button)
        toolbar.add_top_bar(header)

        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.clamp = Adw.Clamp(maximum_size=520, margin_start=12, margin_end=12,
                               margin_top=6, margin_bottom=24)
        self.scroller.set_child(self.clamp)
        toolbar.set_content(self.scroller)
        self.append(toolbar)
        toolbar.set_vexpand(True)

        self._save_title = Debounce(500, self._commit_title)
        self._save_body = Debounce(700, self._commit_body)
        self._save_checklist = Debounce(600, self._commit_checklist)

    # ---------- loading ----------
    def load(self, item_id):
        self.flush()
        self.item_id = item_id
        item = self.repo.item(item_id) if item_id else None
        if item is None or item["deleted"]:
            self.item_id = None
            self.clamp.set_child(None)
            return
        self._loading = True
        if item["kind"] == "task":
            self.kind_label.set_label(_("Task"))
            self.clamp.set_child(self._build_task(item))
        else:
            self.kind_label.set_label(_("Note"))
            self.clamp.set_child(self._build_note(item))
        self._build_menu(item)
        self._loading = False

    def refresh(self):
        """Reloads after an external change unless the user is typing here."""
        if not self.item_id:
            return
        item = self.repo.item(self.item_id)
        if item is None or item["deleted"]:
            self.load(None)
            self.emit("close-request")
            return
        focus = self.get_root().get_focus() if self.get_root() else None
        if focus is not None and focus.is_ancestor(self.clamp):
            if item["kind"] == "task" and hasattr(self, "subtasks_box"):
                self._fill_subtasks(item)
            return
        self.load(self.item_id)

    def focus_title(self):
        if hasattr(self, "title_entry"):
            self.title_entry.grab_focus()

    def flush(self):
        for d in (self._save_title, self._save_body, self._save_checklist):
            d.flush()

    # ---------- common ----------
    def _title_entry(self, item, placeholder):
        entry = Gtk.Entry(text=item["title"], placeholder_text=placeholder, hexpand=True)
        entry.add_css_class("flat")
        entry.add_css_class("editor-title")
        entry.connect("changed", lambda *_: self._loading or self._save_title())
        entry.connect("activate", lambda *_: self._save_title.flush())
        self.title_entry = entry
        return entry

    def _body_view(self, item, placeholder):
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                            top_margin=10, bottom_margin=10, left_margin=12, right_margin=12)
        view.add_css_class("editor-text")
        view.get_buffer().set_text(item["body"] or "")
        view.get_buffer().connect("changed", lambda *_: self._loading or self._save_body())
        view.update_property([Gtk.AccessibleProperty.LABEL], [placeholder])
        overlay = Gtk.Overlay(child=view)
        hint = Gtk.Label(label=placeholder, xalign=0, yalign=0, can_target=False,
                         halign=Gtk.Align.START, valign=Gtk.Align.START)
        hint.add_css_class("editor-placeholder")
        overlay.add_overlay(hint)
        buf = view.get_buffer()
        hint.set_visible(buf.get_char_count() == 0)
        buf.connect("changed", lambda b: hint.set_visible(b.get_char_count() == 0))
        frame = Gtk.Frame(child=overlay)
        frame.add_css_class("view")
        frame.set_size_request(-1, 90)
        self.body_view = view
        return frame

    def _commit_title(self):
        if self.item_id and hasattr(self, "title_entry"):
            self.repo.update(self.item_id, title=self.title_entry.get_text().strip())

    def _commit_body(self):
        if self.item_id and hasattr(self, "body_view") and self.body_view.get_root():
            self.repo.update(self.item_id, body=_text_of(self.body_view))

    def _build_menu(self, item):
        from gi.repository import Gio

        menu = Gio.Menu()
        if item["web_view_link"]:
            menu.append(_("Open in Google Tasks"), "editor.open-google")
        menu.append(_("Convert to Note") if item["kind"] == "task" else _("Convert to Task"),
                    "editor.convert")
        menu.append(_("Delete"), "editor.delete")
        self.menu_button.set_menu_model(menu)
        group = Gio.SimpleActionGroup()
        a = Gio.SimpleAction.new("open-google", None)
        a.connect("activate", lambda *_: Gtk.UriLauncher(uri=item["web_view_link"]).launch(
            self.get_root(), None, None, None))
        group.add_action(a)
        a = Gio.SimpleAction.new("convert", None)
        a.connect("activate", self._convert)
        group.add_action(a)
        a = Gio.SimpleAction.new("delete", None)
        a.connect("activate", lambda *_: self.emit("delete-request", self.item_id))
        group.add_action(a)
        self.insert_action_group("editor", group)

    def _convert(self, *_a):
        self.flush()
        new = self.repo.convert(self.item_id)
        self.emit("open-item", new)

    # ---------- task ----------
    def _build_task(self, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        top = Gtk.Box(spacing=8)
        check = Gtk.CheckButton(active=item["status"] == "done", valign=Gtk.Align.CENTER)
        check.add_css_class("selection-mode")
        check.connect("toggled", lambda c: self.repo.set_done(self.item_id, c.get_active()))
        top.append(check)
        top.append(self._title_entry(item, _("Task")))
        box.append(top)
        box.append(self._body_view(item, _("Add details")))

        group = Adw.PreferencesGroup()
        due_row = Adw.ActionRow(title=_("Due date"), icon_name="alarm-symbolic")
        due_row.set_subtitle(self._due_text(item["due_date"]))
        state = dates.due_state(item["due_date"])
        if state in (dates.OVERDUE, dates.TODAY):
            due_row.add_css_class("error" if state == dates.OVERDUE else "warning")
        pick = Gtk.MenuButton(icon_name="x-office-calendar-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text=_("Pick a date"))
        pick.add_css_class("flat")
        pick.set_popover(self._date_popover(item, pick))
        due_row.add_suffix(pick)
        if item["due_date"]:
            clear = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER,
                               tooltip_text=_("Remove date"))
            clear.add_css_class("flat")
            clear.connect("clicked", lambda *_: self._set_due(None))
            due_row.add_suffix(clear)
        due_row.set_activatable_widget(pick)
        group.add(due_row)

        if not item["parent_id"]:
            lists = self.repo.tasklists()
            model = Gtk.StringList.new([c["title"] for c in lists])
            combo = Adw.ComboRow(title=_("List"), model=model, icon_name="view-list-symbolic")
            ids = [c["id"] for c in lists]
            if item["collection_id"] in ids:
                combo.set_selected(ids.index(item["collection_id"]))
            combo.connect("notify::selected", lambda c, _p: self._loading or self.repo.update(
                self.item_id, collection_id=ids[c.get_selected()]))
            group.add(combo)
        else:
            parent = self.repo.item(item["parent_id"])
            row = Adw.ActionRow(title=_("Subtask of"), subtitle=parent["title"] if parent else "",
                                icon_name="go-up-symbolic", activatable=True)
            row.connect("activated", lambda *_: self.emit("open-item", item["parent_id"]))
            group.add(row)
        box.append(group)

        if not item["parent_id"]:
            self.subtasks_group = Adw.PreferencesGroup(title=_("Subtasks"))
            self.subtasks_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.subtasks_box.add_css_class("boxed-list")
            self.subtasks_group.add(self.subtasks_box)
            self._fill_subtasks(item)
            box.append(self.subtasks_group)

        links = json.loads(item["links_json"] or "[]")
        if links:
            lg = Adw.PreferencesGroup(title=_("Source"))
            for link in links:
                row = Adw.ActionRow(title=link.get("description") or link.get("link"),
                                    subtitle=link.get("type", ""), activatable=True,
                                    icon_name=link_icon(link.get("type")))
                row.add_suffix(Gtk.Image.new_from_icon_name("adw-external-link-symbolic"))
                row.connect("activated", lambda _r, url=link.get("link"): Gtk.UriLauncher(
                    uri=url).launch(self.get_root(), None, None, None))
                lg.add(row)
            box.append(lg)

        if item["completed_at"] and item["status"] == "done":
            done = Gtk.Label(label=_("Completed {}").format(item["completed_at"][:10]), xalign=0)
            done.add_css_class("dim-label")
            box.append(done)
        return box

    def _fill_subtasks(self, item):
        box = self.subtasks_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        for sub in self.repo.children(item["id"]):
            row = Adw.ActionRow(title=GLib.markup_escape_text(sub["title"] or _("Untitled")),
                                activatable=True)
            cb = Gtk.CheckButton(active=sub["status"] == "done", valign=Gtk.Align.CENTER)
            cb.add_css_class("selection-mode")
            cb.connect("toggled", lambda c, sid=sub["id"]: self.repo.set_done(sid, c.get_active()))
            row.add_prefix(cb)
            if sub["due_date"]:
                row.set_subtitle(dates.chip_text(sub["due_date"]))
            row.connect("activated", lambda _r, sid=sub["id"]: self.emit("open-item", sid))
            box.append(row)
        add = Adw.EntryRow(title=_("Add subtask"), show_apply_button=False)
        add.connect("entry-activated", self._add_subtask)
        box.append(add)
        self._add_row = add

    def _add_subtask(self, row):
        text = row.get_text().strip()
        if not text:
            return
        # new subtasks go to the bottom, like typing a list
        subs = self.repo.children(self.item_id)
        self.repo.create_task(text, parent_id=self.item_id,
                              after_id=subs[-1]["id"] if subs else None)
        row.set_text("")
        GLib.idle_add(lambda: (self._add_row.grab_focus(), False)[1])

    def _due_text(self, due):
        if not due:
            return _("No date")
        d = dates.parse_iso(due)
        return f"{dates.chip_text(due)} · {dates.weekday_long(d.weekday())}, {dates.short_date(d)}"

    def _date_popover(self, item, button):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6,
                      margin_bottom=6, margin_start=6, margin_end=6)
        quick = Gtk.Box(spacing=6, homogeneous=True)
        today = date.today()
        next_monday = today + timedelta(days=(7 - today.weekday()) or 7)
        for label, value in ((_("Today"), today), (_("Tomorrow"), today + timedelta(days=1)),
                             (_("Next week"), next_monday)):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, v=value: (pop.popdown(), self._set_due(v.isoformat())))
            quick.append(b)
        box.append(quick)
        cal = Gtk.Calendar()
        if item["due_date"]:
            d = dates.parse_iso(item["due_date"])
            cal.select_day(GLib.DateTime.new_local(d.year, d.month, d.day, 0, 0, 0))

        def picked(c):
            dt = c.get_date()
            pop.popdown()
            self._set_due(f"{dt.get_year():04d}-{dt.get_month():02d}-{dt.get_day_of_month():02d}")
        cal.connect("day-selected", picked)
        box.append(cal)
        pop.set_child(box)
        return pop

    def _set_due(self, value):
        self.repo.update(self.item_id, due_date=value)
        GLib.idle_add(lambda: (self.load(self.item_id), False)[1])

    # ---------- note ----------
    def _build_note(self, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        media = note_media(item)
        if media:
            pics = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for path in media:
                pic = media_picture(path, 900)
                if pic is None:
                    continue
                pic.add_css_class("editor-image")
                pic.set_cursor_from_name("pointer")
                pic.set_tooltip_text(_("Open the image"))
                click = Gtk.GestureClick()
                click.connect("released", lambda *_a, p=path: Gtk.FileLauncher.new(
                    Gio.File.new_for_path(str(p))).launch(self.get_root(), None, None, None))
                pic.add_controller(click)
                pics.append(pic)
            box.append(pics)
        box.append(self._title_entry(item, _("Title")))
        self.is_checklist = item["checklist_json"] is not None
        if self.is_checklist:
            self.check_entries = json.loads(item["checklist_json"])
            if getattr(self, "_check_item", None) != item["id"]:
                self._check_item, self._check_limit = item["id"], self.CHECK_PAGE
            self.check_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.check_box.add_css_class("boxed-list")
            self.check_clear = Gtk.Button(label=_("Delete ticked items"), halign=Gtk.Align.END)
            self.check_clear.add_css_class("flat")
            self.check_clear.add_css_class("destructive-action")
            self.check_clear.connect("clicked", self._check_clear_ticked)
            self._fill_checklist()
            lbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            lbox.append(self.check_box)
            lbox.append(self.check_clear)
            box.append(lbox)
        else:
            box.append(self._body_view(item, _("Write a note…")))

        group = Adw.PreferencesGroup()
        sw = Adw.SwitchRow(title=_("Checklist"), icon_name="view-list-bullet-symbolic",
                           active=self.is_checklist)
        sw.connect("notify::active", self._toggle_checklist)
        group.add(sw)
        pin = Adw.SwitchRow(title=_("Pinned"), icon_name="view-pin-symbolic", active=bool(item["pinned"]))
        pin.connect("notify::active", lambda r, _p: self._loading or self.repo.update(
            self.item_id, pinned=1 if r.get_active() else 0))
        group.add(pin)
        arch = Adw.SwitchRow(title=_("Archived"), icon_name="folder-symbolic",
                             active=bool(item["archived"]))
        arch.connect("notify::active", lambda r, _p: self._loading or self.repo.update(
            self.item_id, archived=1 if r.get_active() else 0))
        group.add(arch)
        box.append(group)

        colors = Adw.PreferencesGroup(title=_("Color"))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12,
                           column_spacing=6, row_spacing=6)
        provider = Gtk.CssProvider()
        provider.load_from_string("".join(
            f".color-dot.c-{k} {{ background-color: {v}; }}" for k, v in COLOR_HEX.items()))
        for c in NOTE_COLORS:
            b = Gtk.Button(tooltip_text=c.title())
            b.add_css_class("color-dot")
            b.add_css_class("c-" + c)
            b.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            if item["color"] == c:
                b.add_css_class("selected")
            b.connect("clicked", lambda _b, c=c: (self.repo.update(self.item_id, color=c),
                                                  self.load(self.item_id)))
            flow.append(b)
        colors.add(flow)
        box.append(colors)

        labels = self.repo.labels()
        lg = Adw.PreferencesGroup(title=_("Labels"))
        chosen = set(json.loads(item["labels_json"] or "[]"))
        for lab in labels:
            row = Adw.SwitchRow(title=GLib.markup_escape_text(lab["title"]), active=lab["id"] in chosen)
            row.connect("notify::active", self._toggle_label, lab["id"])
            lg.add(row)
        new = Adw.EntryRow(title=_("New label"))
        new.connect("entry-activated", self._new_label)
        lg.add(new)
        box.append(lg)
        return box

    def _toggle_label(self, row, _p, label_id):
        if self._loading:
            return
        item = self.repo.item(self.item_id)
        chosen = set(json.loads(item["labels_json"] or "[]"))
        chosen.add(label_id) if row.get_active() else chosen.discard(label_id)
        self.repo.update(self.item_id, labels_json=json.dumps(sorted(chosen)))

    def _new_label(self, row):
        title = row.get_text().strip()
        if not title:
            return
        lid = self.repo.ensure_collection("label", title)
        item = self.repo.item(self.item_id)
        chosen = set(json.loads(item["labels_json"] or "[]"))
        chosen.add(lid)
        self.repo.update(self.item_id, labels_json=json.dumps(sorted(chosen)))
        self.load(self.item_id)

    def _toggle_checklist(self, row, _p):
        if self._loading:
            return
        self.flush()
        item = self.repo.item(self.item_id)
        if row.get_active():
            lines = [l.strip() for l in (item["body"] or "").splitlines() if l.strip()]
            self.repo.update(self.item_id, body="", checklist_json=json.dumps(
                [{"text": l, "checked": False} for l in lines], ensure_ascii=False))
        else:
            entries = json.loads(item["checklist_json"] or "[]")
            self.repo.update(self.item_id, checklist_json=None,
                             body="\n".join(e.get("text", "") for e in entries))
        GLib.idle_add(lambda: (self.load(self.item_id), False)[1])

    CHECK_PAGE = 30  # items shown before "Load more"

    def _check_row(self, idx, entry):
        row = Gtk.ListBoxRow(activatable=False)
        h = Gtk.Box(spacing=8, margin_start=4, margin_end=6, margin_top=4, margin_bottom=4)
        handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        handle.add_css_class("dim-label")
        handle.set_cursor_from_name("grab")
        handle.set_tooltip_text(_("Drag to move"))
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda *_a: Gdk.ContentProvider.new_for_value(f"check:{idx}"))
        src.connect("drag-begin", lambda s, _d: s.set_icon(Gtk.WidgetPaintable.new(row), 20, 20))
        handle.add_controller(src)
        h.append(handle)
        cb = Gtk.CheckButton(active=bool(entry.get("checked")))
        cb.connect("toggled", self._check_toggled, idx)
        h.append(cb)
        e = Gtk.Entry(text=entry.get("text", ""), hexpand=True)
        e.add_css_class("flat")
        if entry.get("checked"):
            e.add_css_class("dim-label")
        e.connect("changed", self._check_text, idx)
        h.append(e)
        rm = Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text=_("Remove"))
        rm.add_css_class("flat")
        rm.connect("clicked", self._check_remove, idx)
        h.append(rm)
        row.set_child(h)
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        clear = insertion_indicator(row, target)
        target.connect("drop", lambda *a: (clear(), self._check_drop(*a, row, idx))[1])
        row.add_controller(target)
        self._check_entry_widgets[idx] = e
        return row

    def _fill_checklist(self):
        """The items in their own order, checked ones included: the add row on top,
        "Load more" at the bottom when the list is long. Keeps the scroll position."""
        adj = self.scroller.get_vadjustment()
        scroll = adj.get_value()
        box = self.check_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)
        self._check_entry_widgets = {}
        add = Adw.EntryRow(title=_("Add item"))
        add.connect("entry-activated", self._check_add)
        box.append(add)
        self._check_add_row = add
        self._check_more_row = None
        limit = getattr(self, "_check_limit", self.CHECK_PAGE)
        for idx, entry in enumerate(self.check_entries[:limit]):
            box.append(self._check_row(idx, entry))
        self._check_update_more()
        self._check_update_clear()
        # removing the rows took the focus away; GTK may scroll to wherever it lands
        GLib.idle_add(lambda: (adj.set_value(min(scroll, adj.get_upper() - adj.get_page_size())),
                               False)[1])

    def _check_update_more(self):
        rest = len(self.check_entries) - getattr(self, "_check_limit", self.CHECK_PAGE)
        if rest > 0:
            if self._check_more_row is None:
                self._check_more_row = Adw.ButtonRow()
                self._check_more_row.connect("activated", self._check_more)
                self.check_box.append(self._check_more_row)
            self._check_more_row.set_title(_("Load more ({})").format(rest))
        elif self._check_more_row is not None:
            self.check_box.remove(self._check_more_row)
            self._check_more_row = None

    def _check_update_clear(self):
        self.check_clear.set_visible(any(e.get("checked") for e in self.check_entries))

    def _check_more(self, *_a):
        """Adds the next page above the button: nothing is rebuilt, so nothing jumps."""
        adj = self.scroller.get_vadjustment()
        scroll = adj.get_value()
        shown = getattr(self, "_check_limit", self.CHECK_PAGE)
        self._check_limit = shown + self.CHECK_PAGE
        pos = self._check_more_row.get_index()
        first = None
        for idx in range(shown, min(self._check_limit, len(self.check_entries))):
            row = self._check_row(idx, self.check_entries[idx])
            self.check_box.insert(row, pos)
            pos += 1
            first = first or row
        if first is not None:
            # the button may go away: hand the focus to the first new item (its checkbox)
            # before that, or GTK moves it to the top of the list and scrolls there
            first.get_child().get_first_child().get_next_sibling().grab_focus()
        self._check_update_more()
        GLib.idle_add(lambda: (adj.set_value(scroll), False)[1])

    def _check_clear_ticked(self, _b):
        before = [dict(e) for e in self.check_entries]
        self.check_entries = [e for e in self.check_entries if not e.get("checked")]
        removed = len(before) - len(self.check_entries)
        self._fill_checklist()
        self._commit_checklist()
        item_id = self.item_id
        win = self.get_root()
        if not hasattr(win, "toasts"):
            return
        toast = Adw.Toast(title=ngettext("{} item deleted", "{} items deleted", removed).format(removed),
                          button_label=_("Undo"), timeout=6)

        def undo(_t):
            if self.item_id == item_id:
                self.check_entries = before
                self._fill_checklist()
                self._commit_checklist()
            else:
                self.repo.update(item_id, checklist_json=json.dumps(before, ensure_ascii=False))
        toast.connect("button-clicked", undo)
        win.toasts.add_toast(toast)

    def _check_toggled(self, cb, idx):
        self.check_entries[idx]["checked"] = cb.get_active()
        e = self._check_entry_widgets.get(idx)
        if e is not None:
            (e.add_css_class if cb.get_active() else e.remove_css_class)("dim-label")
        self._check_update_clear()
        self._save_checklist()

    def _check_drop(self, _t, value, _x, y, row, dst):
        if not isinstance(value, str) or not value.startswith("check:"):
            return False
        src = int(value[6:])
        if not 0 <= src < len(self.check_entries):
            return False
        pos = dst if y < row.get_height() / 2 else dst + 1
        entry = self.check_entries.pop(src)
        if src < pos:
            pos -= 1
        if pos == src:
            self.check_entries.insert(src, entry)
            return False
        self.check_entries.insert(pos, entry)
        self._fill_checklist()
        self._save_checklist()
        return True

    def _check_text(self, e, idx):
        self.check_entries[idx]["text"] = e.get_text()
        self._save_checklist()

    def _check_remove(self, _b, idx):
        del self.check_entries[idx]
        self._fill_checklist()
        self._save_checklist()

    def _check_add(self, row):
        text = row.get_text().strip()
        if not text:
            return
        self.check_entries.insert(0, {"text": text, "checked": False})
        row.set_text("")
        self._fill_checklist()
        self._save_checklist.flush() if self._save_checklist.src else None
        self._commit_checklist()
        GLib.idle_add(lambda: (self._check_add_row.grab_focus(), False)[1])

    def _commit_checklist(self):
        if self.item_id and getattr(self, "is_checklist", False):
            self.repo.update(self.item_id, checklist_json=json.dumps(self.check_entries,
                                                                     ensure_ascii=False))
