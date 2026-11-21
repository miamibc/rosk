"""Main window: sidebar with smart views and categories, task list / note grid, editor pane."""

import json
from datetime import date, timedelta

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from ..core import dates, quickparse
from ..i18n import _, ngettext
from ..sync import manager as sync_state
from .complete import TagCompleter
from .masonry import Masonry
from .editor import Debounce, Editor
from .widgets import GroupHeader, NoteCard, TaskRow, chip, empty_state, note_media

TODAY, UPCOMING, ALL, NOTES, COMPLETED, ARCHIVE, SEARCH = (
    "today", "upcoming", "all", "notes", "completed", "archive", "search")

SMART = [
    (TODAY, _("Today"), "starred-symbolic"),
    (UPCOMING, _("Upcoming"), "x-office-calendar-symbolic"),
    (ALL, _("All Tasks"), "view-list-symbolic"),
    (NOTES, _("Notes"), "document-edit-symbolic"),
    (COMPLETED, _("Completed"), "object-select-symbolic"),
]


class SidebarRow(Gtk.ListBoxRow):
    def __init__(self, view, title, icon, count=None, css=None):
        super().__init__()
        self.view = view
        box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=2, margin_bottom=2)
        box.append(Gtk.Image.new_from_icon_name(icon))
        box.append(Gtk.Label(label=title, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        if count:
            c = Gtk.Label(label=str(count))
            c.add_css_class("sidebar-count")
            if css:
                c.add_css_class(css)
            box.append(c)
        self.set_child(box)


class SidebarHeader(Gtk.ListBoxRow):
    def __init__(self, title, action=None, tooltip=None):
        super().__init__(activatable=False, selectable=False)
        box = Gtk.Box(margin_start=12, margin_end=6, margin_top=12)
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        label.add_css_class("heading")
        label.add_css_class("dim-label")
        box.append(label)
        if action:
            b = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=tooltip,
                           action_name=action)
            b.add_css_class("flat")
            b.add_css_class("circular")
            box.append(b)
        self.set_child(box)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Rosk", default_width=1000, default_height=680,
                         hide_on_close=True)
        self.app = app
        self.repo = app.repo
        self.sync = app.sync
        self.view = TODAY
        self.pending_deletes = set()
        self.ignore_spans = set()
        self._refresh = Debounce(60, self._do_refresh)
        self._show_completed = False

        self._build()
        self._actions()
        self.repo.connect_changed(lambda *_: self._refresh())
        self.sync.connect("status-changed", lambda *_: self._update_sync_button())
        self.sync.connect("data-changed", lambda *_: self._refresh())
        self.connect("notify::is-active", lambda w, _p: self.sync.set_active(w.is_active()))
        self.connect("close-request", self._on_close)
        self._do_refresh()
        self._update_sync_button()

    # ---------- layout ----------
    def _build(self):
        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        self.split = Adw.NavigationSplitView(min_sidebar_width=220, max_sidebar_width=280)
        self.toasts.set_child(self.split)

        # sidebar
        side_tb = Adw.ToolbarView()
        side_header = Adw.HeaderBar()
        side_header.set_title_widget(Adw.WindowTitle(title="Rosk"))
        menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("New List"), "win.new-list")
        section.append(_("Sync Now"), "app.sync")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Preferences"), "app.preferences")
        section.append(_("Keyboard Shortcuts"), "win.show-help-overlay")
        section.append(_("About Rosk"), "app.about")
        section.append(_("Quit"), "app.quit")
        menu.append_section(None, section)
        side_header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                            tooltip_text=_("Main Menu"), primary=True))
        side_tb.add_top_bar(side_header)
        self.sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar.add_css_class("navigation-sidebar")
        self.sidebar.connect("row-activated", self._on_sidebar)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.sidebar)
        side_tb.set_content(scroller)
        self.split.set_sidebar(Adw.NavigationPage(title="Rosk", child=side_tb))

        # content
        self.content_tb = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title=_("Today"))
        header.set_title_widget(self.title_widget)
        self.search_button = Gtk.ToggleButton(icon_name="system-search-symbolic",
                                              tooltip_text=_("Search"))
        header.pack_start(self.search_button)
        self.sync_button = Gtk.Button(tooltip_text=_("Sync"))
        self.sync_button.add_css_class("flat")
        self.sync_button.connect("clicked", self._on_sync_clicked)
        header.pack_end(self.sync_button)
        self.content_tb.add_top_bar(header)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("Search tasks and notes"), hexpand=True)
        clamp = Adw.Clamp(maximum_size=700, child=self.search_entry)
        self.search_bar.set_child(clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_button.bind_property("active", self.search_bar, "search-mode-enabled",
                                         GObject.BindingFlags.BIDIRECTIONAL)
        self.search_entry.connect("search-changed", lambda *_: self._do_refresh())
        self.search_bar.connect("notify::search-mode-enabled", lambda *_: self._do_refresh())
        self.content_tb.add_top_bar(self.search_bar)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # quick add
        add_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10,
                          margin_bottom=4, margin_start=12, margin_end=12)
        self.add_entry = Gtk.Entry(hexpand=True)
        self.add_entry.add_css_class("add-entry")
        self.add_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "list-add-symbolic")
        self.add_entry.connect("activate", self._on_add)
        self.add_entry.connect("changed", self._on_add_changed)
        add_box.append(self.add_entry)
        self.completer = TagCompleter(self.add_entry, self._tag_names)
        add_keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        add_keys.connect("key-pressed", lambda _c, kv, _k, _s: self.completer.handle_key(kv))
        self.add_entry.add_controller(add_keys)
        self.preview = Gtk.Box(spacing=6)
        self.preview_revealer = Gtk.Revealer(child=self.preview,
                                             transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        add_box.append(self.preview_revealer)
        self.add_clamp = Adw.Clamp(maximum_size=760, child=add_box)
        body.append(self.add_clamp)

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        # tasks
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self._on_row_activated)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_list_key)
        self.list.add_controller(keys)
        ls = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        ls.set_child(Adw.Clamp(maximum_size=760, child=self.list, margin_start=6, margin_end=6,
                               margin_bottom=24))
        self.stack.add_named(ls, "tasks")
        # notes
        notes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12,
                            margin_end=12, margin_bottom=24, margin_top=6)
        self.pinned_label = Gtk.Label(label=_("Pinned"), xalign=0)
        self.pinned_label.add_css_class("group-header")
        self.pinned_flow = Masonry()
        self.others_label = Gtk.Label(label=_("Others"), xalign=0)
        self.others_label.add_css_class("group-header")
        self.others_flow = Masonry()
        for w in (self.pinned_label, self.pinned_flow, self.others_label, self.others_flow):
            notes_box.append(w)
        ns = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        ns.set_child(Adw.Clamp(maximum_size=1300, child=notes_box))
        ns.get_vadjustment().connect("value-changed", self._notes_scrolled)
        self._notes_pending = []
        self._notes_key = None
        self.stack.add_named(ns, "notes")
        self.empty = Adw.Bin()
        self.stack.add_named(self.empty, "empty")
        body.append(self.stack)
        self.content_tb.set_content(body)

        self.editor = Editor(self.repo)
        self.editor.set_size_request(340, -1)
        self.editor.connect("close-request", lambda *_: self._close_editor())
        self.editor.connect("delete-request", lambda _e, iid: self.delete_item(iid))
        self.editor.connect("open-item", lambda _e, iid: self.open_item(iid))
        self.detail = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END, show_sidebar=False,
                                           content=self.content_tb, sidebar=self.editor,
                                           min_sidebar_width=340, max_sidebar_width=440,
                                           sidebar_width_fraction=0.4)
        self.content_page = Adw.NavigationPage(title=_("Today"), child=self.detail)
        self.split.set_content(self.content_page)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 720sp"))
        bp.add_setter(self.split, "collapsed", True)
        bp.add_setter(self.detail, "collapsed", True)
        self.add_breakpoint(bp)

        css = Gtk.CssProvider()
        css.load_from_path(str(__import__("pathlib").Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _actions(self):
        def add(name, cb, accels=None, param=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.app.set_accels_for_action(f"win.{name}" + (f"::{accels[1]}" if param else ""),
                                               accels[0] if param else accels)

        add("new-list", lambda *_: self.prompt_new_list())
        add("focus-add", lambda *_: self.focus_add(), ["<Control>n"])
        add("new-note", lambda *_: self.focus_add(note=True), ["<Control><Shift>n"])
        add("search", lambda *_: self.search_button.set_active(True), ["<Control>f"])
        add("show-help-overlay", lambda *_: self._shortcuts(), ["<Control>question"])
        view = Gio.SimpleAction.new("view", GLib.VariantType.new("s"))
        view.connect("activate", lambda _a, v: self.show_view(v.get_string()))
        self.add_action(view)
        for i, (v, _t, _i) in enumerate(SMART, start=1):
            self.app.set_accels_for_action(f"win.view::{v}", [f"<Control>{i}"])
        self.app.set_accels_for_action("window.close", ["<Control>w"])

    # ---------- sidebar ----------
    def _fill_sidebar(self, counts):
        sb = self.sidebar
        selected = self.view
        while (child := sb.get_first_child()) is not None:
            sb.remove(child)
        for v, title, icon in SMART:
            count, css = None, None
            if v == TODAY:
                count = counts["today"]
                css = "overdue" if counts["overdue"] else None
            elif v == UPCOMING:
                count = counts["upcoming"]
            elif v == NOTES:
                count = counts["notes"]
            sb.append(SidebarRow(v, title, icon, count, css))
        sb.append(SidebarHeader(_("Lists"), "win.new-list", _("New List")))
        for col in self.repo.tasklists():
            row = SidebarRow("list:" + col["id"], col["title"], "view-list-bullet-symbolic",
                             counts["lists"].get(col["id"]))
            self._make_drop_target(row, col["id"])
            self._list_row_menu(row, col)
            sb.append(row)
        labels = self.repo.labels()
        sb.append(SidebarHeader(_("Labels")))
        for lab in labels:
            row = SidebarRow("label:" + lab["id"], lab["title"], "tag-symbolic")
            self._list_row_menu(row, lab)
            sb.append(row)
        sb.append(SidebarRow(ARCHIVE, _("Archive"), "folder-symbolic"))
        row = sb.get_first_child()
        while row is not None:
            if getattr(row, "view", None) == selected:
                sb.select_row(row)
                break
            row = row.get_next_sibling()

    def _list_row_menu(self, row, col):
        gesture = Gtk.GestureClick(button=3)

        def pressed(_g, _n, x, y):
            menu = Gio.Menu()
            menu.append(_("Rename…"), f"win.rename-collection::{col['id']}")
            if col["kind"] == "tasklist":
                menu.append(_("Set as Default"), f"win.default-list::{col['id']}")
                menu.append(_("Delete Completed"), f"win.clear-completed::{col['id']}")
            menu.append(_("Delete…"), f"win.delete-collection::{col['id']}")
            pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
            pop.set_parent(row)
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
            pop.set_pointing_to(rect)
            pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
            pop.popup()
        gesture.connect("pressed", pressed)
        row.add_controller(gesture)
        if not self.lookup_action("rename-collection"):
            for name, cb in (("rename-collection", self.prompt_rename),
                             ("default-list", lambda cid: (self.repo.set_default_tasklist(cid),
                                                           self.toast(_("Default list changed")))),
                             ("clear-completed", lambda cid: self.repo.clear_completed(cid)),
                             ("delete-collection", self.prompt_delete_collection)):
                a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
                a.connect("activate", lambda _a, v, cb=cb: cb(v.get_string()))
                self.add_action(a)

    def _on_sidebar(self, _lb, row):
        if hasattr(row, "view"):
            self.show_view(row.view)
            self.split.set_show_content(True)

    def show_view(self, view):
        self.view = view
        self.search_button.set_active(False)
        self._close_editor()
        self._do_refresh()

    # ---------- refresh ----------
    def _view_title(self):
        for v, title, _i in SMART:
            if v == self.view:
                return title
        if self.view == ARCHIVE:
            return _("Archive")
        kind, _sep, cid = self.view.partition(":")
        col = self.repo.store.collection(cid)
        if col is None:
            self.view = TODAY
            return _("Today")
        return col["title"]

    def _is_notes_view(self):
        return self.view in (NOTES, ARCHIVE) or self.view.startswith("label:")

    def _do_refresh(self):
        counts = self.repo.counts()
        self._fill_sidebar(counts)
        title = self._view_title()
        searching = self.search_bar.get_search_mode() and self.search_entry.get_text().strip()
        self.title_widget.set_title(_("Search") if searching else title)
        self.content_page.set_title(title)
        self.add_clamp.set_visible(not searching and self.view != COMPLETED)
        self.add_entry.set_placeholder_text(
            _("Take a note…  #label") if self._is_notes_view()
            else _("Add a task…  tomorrow  #list  : - subtask"))
        if searching:
            self._render_search(self.search_entry.get_text())
        elif self._is_notes_view():
            self._render_notes()
        else:
            self._render_tasks()
        self.editor.refresh()

    def _clear_list(self):
        selected = self.list.get_selected_row()
        sel_id = getattr(selected, "item_id", None)
        while (child := self.list.get_first_child()) is not None:
            self.list.remove(child)
        return sel_id

    def _subtask_counts(self):
        rows = self.repo.store.conn.execute(
            "SELECT parent_id, sum(status='done'), count(*) FROM item WHERE parent_id IS NOT NULL "
            "AND deleted=0 GROUP BY parent_id").fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    def _list_titles(self):
        return {c["id"]: c["title"] for c in self.repo.tasklists()}

    def _add_row(self, item, counts, list_title=None, is_subtask=False):
        if item["id"] in self.pending_deletes:
            return None
        row = TaskRow(item, list_title=list_title, subtasks=counts.get(item["id"], (0, 0)),
                      is_subtask=is_subtask)
        row.connect("toggled", self._on_toggled)
        self._make_draggable(row)
        self.list.append(row)
        return row

    def _render_tasks(self):
        sel_id = self._clear_list()
        counts = self._subtask_counts()
        titles = self._list_titles()
        today = date.today()
        n = 0

        def with_children(item):
            nonlocal n
            if self._add_row(item, counts):
                n += 1
            for child in self.repo.children(item["id"]):
                if child["status"] != "done":
                    self._add_row(child, counts, is_subtask=True)

        if self.view == TODAY:
            items = self.repo.today_tasks(today)
            overdue = [i for i in items if i["due_date"] < today.isoformat()]
            due_today = [i for i in items if i["due_date"] == today.isoformat()]
            if overdue:
                self.list.append(GroupHeader(_("Overdue"), "overdue"))
                for i in overdue:
                    n += bool(self._add_row(i, counts, titles.get(i["collection_id"])))
            if due_today:
                if overdue:
                    self.list.append(GroupHeader(_("Today"), "today"))
                for i in due_today:
                    n += bool(self._add_row(i, counts, titles.get(i["collection_id"])))
            empty = (_("All done for today"), _("Nothing is due. Add a task above or enjoy the day."),
                     "object-select-symbolic")
        elif self.view == UPCOMING:
            items = self.repo.upcoming_tasks(today)
            current = None
            week_end = today + timedelta(days=7)
            for i in items:
                d = dates.parse_iso(i["due_date"])
                heading = dates.day_heading(d, today) if d <= week_end else _("Later")
                if heading != current:
                    self.list.append(GroupHeader(heading))
                    current = heading
                n += bool(self._add_row(i, counts, titles.get(i["collection_id"])))
            empty = (_("Nothing planned"), _("Tasks with a future due date appear here."),
                     "x-office-calendar-symbolic")
        elif self.view == ALL:
            for col in self.repo.tasklists():
                tops = [t for t in self.repo.tasks_in(col["id"]) if not t["parent_id"]]
                if not tops:
                    continue
                self.list.append(GroupHeader(col["title"]))
                for t in tops:
                    with_children(t)
            empty = (_("No tasks"), _("Add your first task above."), "view-list-symbolic")
        elif self.view == COMPLETED:
            current = None
            for i in self.repo.completed_tasks():
                day = dates.parse_iso((i["completed_at"] or "")[:10] or None)
                if day is None:
                    heading = _("Earlier")
                elif day == today:
                    heading = _("Today")
                elif day == today - timedelta(days=1):
                    heading = _("Yesterday")
                else:
                    heading = dates.short_date(day, today)
                if heading != current:
                    self.list.append(GroupHeader(heading))
                    current = heading
                n += bool(self._add_row(i, counts, titles.get(i["collection_id"])))
            empty = (_("Nothing completed yet"), _("Crossed-out tasks appear here."),
                     "object-select-symbolic")
        else:  # list:<id>
            cid = self.view.split(":", 1)[1]
            tops = [t for t in self.repo.tasks_in(cid) if not t["parent_id"]]
            for t in tops:
                with_children(t)
            done = [t for t in self.repo.tasks_in(cid, done=True) if not t["parent_id"] and not t["hidden"]]
            if done:
                header = Gtk.ListBoxRow(selectable=False)
                header.toggle_completed = True
                lbl = Gtk.Label(label=(_("Hide completed ({})") if self._show_completed
                                       else _("Show completed ({})")).format(len(done)), xalign=0)
                lbl.add_css_class("group-header")
                header.set_child(lbl)
                self.list.append(header)
                if self._show_completed:
                    for t in done:
                        self._add_row(t, counts)
            empty = (_("No tasks in this list"), _("Add one above."), "view-list-bullet-symbolic")

        if n == 0 and not self.list.get_first_child():
            title, desc, icon = empty
            self.empty.set_child(empty_state(icon, title, desc))
            self.stack.set_visible_child_name("empty")
        else:
            self.stack.set_visible_child_name("tasks")
            self._reselect(sel_id)

    def _reselect(self, sel_id):
        if not sel_id:
            return
        row = self.list.get_first_child()
        while row is not None:
            if getattr(row, "item_id", None) == sel_id:
                self.list.select_row(row)
                return
            row = row.get_next_sibling()

    NOTES_PAGE = 60

    def _render_notes(self):
        label_id = self.view.split(":", 1)[1] if self.view.startswith("label:") else None
        notes = [n for n in self.repo.notes(label_id=label_id, archived=self.view == ARCHIVE)
                 if n["id"] not in self.pending_deletes]
        label_titles = {c["id"]: c["title"] for c in self.repo.labels()}
        key = (self.view, tuple(label_titles.items()), tuple(
            (n["id"], n["title"], n["body"], n["checklist_json"], n["labels_json"], n["pinned"],
             n["color"], len(note_media(n))) for n in notes))
        if not notes:
            self._notes_key = None
            if self.view == ARCHIVE:
                self.empty.set_child(empty_state("folder-symbolic", _("Archive is empty")))
            else:
                self.empty.set_child(empty_state("document-edit-symbolic", _("No notes"),
                                                 _("Notes you add here sync with Google Keep.")))
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("notes")
        if key == self._notes_key:
            return  # nothing changed: keep the cards (and scroll position)
        self._notes_key = key
        self._label_titles = label_titles
        self._label_id = label_id
        for m in (self.pinned_flow, self.others_flow):
            m.remove_all()
        pinned = [n for n in notes if n["pinned"]]
        others = [n for n in notes if not n["pinned"]]
        for n in pinned:
            self.pinned_flow.append(self._note_card(n))
        # cards are added page by page while scrolling: hundreds of notes stay fast
        self._notes_pending = others
        self._more_notes()
        self.pinned_label.set_visible(bool(pinned))
        self.pinned_flow.set_visible(bool(pinned))
        self.others_label.set_visible(bool(pinned) and bool(others))

    def _note_card(self, n):
        titles = [self._label_titles[l] for l in json.loads(n["labels_json"] or "[]")
                  if l in self._label_titles and l != self._label_id]
        card = NoteCard(n, titles)
        card.connect("checklist-changed", lambda c, js: self.repo.update(c.item_id, checklist_json=js))
        card.connect("open-note", lambda c: self.open_item(c.item_id, focus=False))
        return card

    def _more_notes(self):
        batch, self._notes_pending = (self._notes_pending[:self.NOTES_PAGE],
                                      self._notes_pending[self.NOTES_PAGE:])
        for n in batch:
            self.others_flow.append(self._note_card(n))

    def _notes_scrolled(self, adj):
        if self._notes_pending and adj.get_value() + adj.get_page_size() > adj.get_upper() - 800:
            self._more_notes()

    def _render_search(self, text):
        self._clear_list()
        counts = self._subtask_counts()
        titles = self._list_titles()
        results = self.repo.search(text)
        for item in results:
            if item["kind"] == "task":
                self._add_row(item, counts, titles.get(item["collection_id"]))
            else:
                row = Gtk.ListBoxRow()
                row.item_id = item["id"]
                box = Gtk.Box(spacing=10, margin_start=10, margin_top=8, margin_bottom=8)
                box.append(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
                box.append(Gtk.Label(label=item["title"] or (item["body"] or "")[:80] or _("Untitled"),
                                     xalign=0, ellipsize=Pango.EllipsizeMode.END))
                row.set_child(box)
                self.list.append(row)
        if results:
            self.stack.set_visible_child_name("tasks")
        else:
            self.empty.set_child(empty_state("system-search-symbolic", _("Nothing found")))
            self.stack.set_visible_child_name("empty")

    # ---------- quick add ----------
    def focus_add(self, note=False):
        if note and not self._is_notes_view():
            self.show_view(NOTES)
        elif not note and self._is_notes_view():
            self.show_view(TODAY)
        self.present()
        self.add_entry.grab_focus()

    def _tag_names(self):
        notes = self._is_notes_view() or self._parse_add().kind == quickparse.NOTE
        return [c["title"] for c in (self.repo.labels() if notes else self.repo.tasklists())]

    def _parse_add(self):
        kind = quickparse.NOTE if self._is_notes_view() else quickparse.TASK
        return quickparse.parse(self.add_entry.get_text(), ignore=self.ignore_spans,
                                default_kind=kind)

    def _on_add_changed(self, entry):
        if not entry.get_text():
            self.ignore_spans.clear()
        r = self._parse_add()
        while (c := self.preview.get_first_child()) is not None:
            self.preview.remove(c)
        for sp in r.spans:
            if sp.kind == "date":
                text, icon = dates.chip_text(sp.value), "alarm-symbolic"
            elif sp.kind == "list":
                is_label = self._is_notes_view() or r.kind == "note"
                icon = "tag-symbolic" if is_label else "view-list-symbolic"
                col = self.repo.find_collection("label" if is_label else "tasklist", sp.value)
                text = col["title"] if col else _("New: {}").format(sp.value.replace("_", " "))
            elif sp.kind == "note":
                text, icon = _("Note"), "document-edit-symbolic"
            else:
                text = ngettext("{} subtask", "{} subtasks", len(sp.value)).format(len(sp.value))
                icon = "view-list-bullet-symbolic"
            btn = Gtk.Button(child=chip(text, "preview-" + sp.kind, icon=icon),
                             tooltip_text=_("Click to keep as text"))
            btn.add_css_class("flat")
            btn.add_css_class("chip")
            btn.connect("clicked", self._reject_span, sp.start)
            self.preview.append(btn)
        self.preview_revealer.set_reveal_child(bool(r.spans))

    def _reject_span(self, _b, start):
        self.ignore_spans.add(start)
        self._on_add_changed(self.add_entry)
        self.add_entry.grab_focus_without_selecting()

    def _on_add(self, entry):
        text = entry.get_text().strip()
        if not text:
            return
        if self._is_notes_view():
            r = self._parse_add()
            labels = []
            label_id = self.view.split(":", 1)[1] if self.view.startswith("label:") else None
            if label_id:
                labels.append(label_id)
            if r.list_name:
                labels.append(self.repo.ensure_collection("label", r.list_name))
            checklist = [{"text": s, "checked": False} for s in r.subtasks] if r.subtasks else None
            self.repo.create_note(title=r.title, checklist=checklist, labels=labels)
        else:
            r = quickparse.parse(text, ignore=self.ignore_spans)
            collection = self.view.split(":", 1)[1] if self.view.startswith("list:") else None
            iid = self.repo.quick_add(text, collection_id=collection, ignore=self.ignore_spans)
            item = self.repo.item(iid) if iid else None
            if item and item["kind"] == "task" and not r.due and self.view == TODAY:
                self.repo.update(iid, due_date=date.today().isoformat())
            if item and item["kind"] == "note":
                self.toast(_("Note added"))
        entry.set_text("")
        self.ignore_spans.clear()
        self.sync.request()

    # ---------- rows ----------
    def _on_row_activated(self, _lb, row):
        if getattr(row, "toggle_completed", False):
            self._show_completed = not self._show_completed
            self._do_refresh()
            return
        if hasattr(row, "item_id"):
            self.open_item(row.item_id, focus=False)

    def open_item(self, iid, focus=True):
        item = self.repo.item(iid)
        if item is None:
            return
        searching = self.search_bar.get_search_mode() and self.search_entry.get_text()
        if not searching and item["kind"] == "note" and not self._is_notes_view():
            self.show_view(NOTES)
        self.editor.load(iid)
        self.detail.set_show_sidebar(True)
        self.split.set_show_content(True)
        if focus:
            self.editor.focus_title()

    def _close_editor(self):
        self.editor.flush()
        self.detail.set_show_sidebar(False)
        self.editor.load(None)

    def _on_toggled(self, row, done):
        iid = row.item_id
        self.repo.set_done(iid, done)
        self.sync.request()
        if done:
            toast = Adw.Toast(title=_("Completed"), button_label=_("Undo"), timeout=5)
            toast.connect("button-clicked", lambda *_: (self.repo.set_done(iid, False), self.sync.request()))
            self.toasts.add_toast(toast)

    def _on_list_key(self, _c, keyval, _code, state):
        row = self.list.get_selected_row() or (self.get_focus() if isinstance(self.get_focus(), TaskRow) else None)
        focus = self.get_focus()
        if isinstance(focus, Gtk.ListBoxRow):
            row = focus
        if row is None or not hasattr(row, "item_id"):
            return False
        if keyval == Gdk.KEY_space and isinstance(row, TaskRow):
            row.toggle()
            return True
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            self.delete_item(row.item_id)
            return True
        return False

    def delete_item(self, iid):
        item = self.repo.item(iid)
        if item is None:
            return
        self.editor.flush()
        if self.editor.item_id == iid:
            self._close_editor()
        self.pending_deletes.add(iid)
        self._do_refresh()
        undone = {"v": False}
        toast = Adw.Toast(title=_("Deleted “{}”").format(item["title"] or _("Untitled")),
                          button_label=_("Undo"), timeout=5)

        def undo(*_a):
            undone["v"] = True
            self.pending_deletes.discard(iid)
            self._do_refresh()

        def dismissed(*_a):
            if not undone["v"] and iid in self.pending_deletes:
                self.pending_deletes.discard(iid)
                self.repo.delete(iid)
                self.sync.request()
        toast.connect("button-clicked", undo)
        toast.connect("dismissed", dismissed)
        self.toasts.add_toast(toast)

    def commit_pending_deletes(self):
        for iid in list(self.pending_deletes):
            self.pending_deletes.discard(iid)
            self.repo.delete(iid)

    # ---------- drag and drop ----------
    def _make_draggable(self, row):
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(row.item_id))
        src.connect("drag-begin", lambda s, _d: s.set_icon(Gtk.WidgetPaintable.new(row), 20, 20))
        row.add_controller(src)
        if self.view.startswith("list:") or self.view == ALL:
            target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
            target.connect("drop", self._on_drop_row, row)
            row.add_controller(target)

    def _on_drop_row(self, _t, value, _x, y, target_row):
        src_id = value
        dst = target_row.item
        if src_id == dst["id"]:
            return False
        src = self.repo.item(src_id)
        if src is None or src["kind"] != "task":
            return False
        if self.repo.children(src_id) and dst["parent_id"]:
            return False  # one level of subtasks only
        before = y < target_row.get_height() / 2
        parent = dst["parent_id"]
        siblings = (self.repo.children(parent) if parent else
                    [t for t in self.repo.tasks_in(dst["collection_id"]) if not t["parent_id"]])
        ids = [s["id"] for s in siblings if s["id"] != src_id]
        idx = ids.index(dst["id"]) if dst["id"] in ids else len(ids)
        if before:
            after_id = ids[idx - 1] if idx > 0 else None
            before_id = dst["id"]
        else:
            after_id = dst["id"]
            before_id = ids[idx + 1] if idx + 1 < len(ids) else None
        self.repo.move(src_id, collection_id=dst["collection_id"], parent_id=parent,
                       before_id=before_id, after_id=after_id)
        self.sync.request()
        return True

    def _make_drop_target(self, row, collection_id):
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)

        def drop(_t, value, _x, _y):
            item = self.repo.item(value)
            if item is None or item["kind"] != "task":
                return False
            self.repo.update(value, collection_id=collection_id, parent_id=None)
            self.sync.request()
            self.toast(_("Moved to “{}”").format(self.repo.store.collection(collection_id)["title"]))
            return True
        target.connect("drop", drop)
        row.add_controller(target)

    # ---------- collections ----------
    def _prompt(self, heading, initial, ok_label, callback):
        dialog = Adw.AlertDialog(heading=heading)
        entry = Gtk.Entry(text=initial, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", ok_label)
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")
        dialog.set_close_response("cancel")

        def done(d, response):
            if response == "ok" and entry.get_text().strip():
                callback(entry.get_text().strip())
        dialog.connect("response", done)
        dialog.present(self)
        entry.grab_focus()

    def prompt_new_list(self):
        def create(title):
            cid = self.repo.create_collection("tasklist", title)
            self.sync.request()
            self.show_view("list:" + cid)
        self._prompt(_("New List"), "", _("Create"), create)

    def prompt_rename(self, cid):
        col = self.repo.store.collection(cid)
        self._prompt(_("Rename"), col["title"], _("Rename"),
                     lambda t: (self.repo.rename_collection(cid, t), self.sync.request()))

    def prompt_delete_collection(self, cid):
        col = self.repo.store.collection(cid)
        body = (_("All tasks in this list will be deleted from Google Tasks too.")
                if col["kind"] == "tasklist" else _("Notes keep their content, only the label is removed."))
        dialog = Adw.AlertDialog(heading=_("Delete “{}”?").format(col["title"]), body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def done(_d, response):
            if response == "delete":
                if self.view.endswith(cid):
                    self.view = TODAY
                self.repo.delete_collection(cid)
                self.sync.request()
        dialog.connect("response", done)
        dialog.present(self)

    # ---------- sync ----------
    def _update_sync_button(self):
        s = self.sync
        pending = s.pending
        if s.state == sync_state.SYNCING:
            self.sync_button.set_child(Adw.Spinner())
            tip = _("Syncing…")
        else:
            icon = {sync_state.OFFLINE: "network-offline-symbolic",
                    sync_state.ERROR: "dialog-warning-symbolic",
                    sync_state.NO_ACCOUNT: "avatar-default-symbolic"}.get(s.state, "view-refresh-symbolic")
            self.sync_button.set_child(Gtk.Image.new_from_icon_name(icon))
            tip = s.message or _("Synced")
        if pending:
            tip += "\n" + ngettext("{} change waiting to be sent", "{} changes waiting to be sent",
                                   pending).format(pending)
        if s.keep_message:
            tip += "\n" + s.keep_message
        self.sync_button.set_tooltip_text(tip)

    def _on_sync_clicked(self, _b):
        if self.sync.state in (sync_state.ERROR, sync_state.NO_ACCOUNT) and self.sync.message:
            self.toast(self.sync.message)
        self.sync.now()

    # ---------- misc ----------
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def _shortcuts(self):
        dialog = Adw.Dialog(title=_("Keyboard Shortcuts"), content_width=420)
        tb = Adw.ToolbarView()
        tb.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        for keys, what in (("Ctrl+N", _("Add a task")), ("Ctrl+Shift+N", _("Add a note")),
                           ("Ctrl+F", _("Search")), ("Ctrl+1…5", _("Smart views")),
                           ("Space", _("Mark selected task as done")),
                           ("Delete", _("Delete selected item")), ("Enter", _("Open selected item")),
                           ("F5", _("Sync now")), ("Ctrl+,", _("Preferences")),
                           ("Ctrl+W", _("Close window")), ("Ctrl+Q", _("Quit"))):
            row = Adw.ActionRow(title=what)
            row.add_suffix(Gtk.Label(label=keys, css_classes=["dim-label"]))
            group.add(row)
        page.add(group)
        tb.set_content(page)
        dialog.set_child(tb)
        dialog.present(self)

    def _on_close(self, *_a):
        self.editor.flush()
        self.commit_pending_deletes()
        return False
