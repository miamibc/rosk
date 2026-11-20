"""Developer helpers: ROSK_SNAPSHOT=/path.png saves the window, ROSK_SCRIPT runs UI steps."""

import os

from gi.repository import GLib, Gtk


def save_png(widget, path):
    paintable = Gtk.WidgetPaintable.new(widget)
    w, h = widget.get_width(), widget.get_height()
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return False
    renderer = widget.get_native().get_renderer()
    texture = renderer.render_texture(node, None)
    texture.save_to_png(path)
    return True


def schedule_snapshot(app, win, path):
    steps = [s for s in os.environ.get("ROSK_SCRIPT", "").split(";") if s]
    delay = int(os.environ.get("ROSK_SNAPSHOT_DELAY", "2500"))
    shots = []

    def run_steps():
        for step in steps:
            name, _sep, arg = step.partition("=")
            if name == "view":
                win.show_view(arg)
            elif name == "type":
                win.add_entry.set_text(arg)
            elif name == "open-first":
                row = win.list.get_first_child()
                while row is not None and not hasattr(row, "item_id"):
                    row = row.get_next_sibling()
                if row is not None:
                    win.open_item(row.item_id, focus=False)
            elif name == "open-note":
                for flow in (win.pinned_flow, win.others_flow):
                    if flow.children:
                        win.open_item(flow.children[0].item_id, focus=False)
                        break
            elif name == "search":
                win.search_button.set_active(True)
                win.search_entry.set_text(arg)
            elif name == "prefs":
                app.activate_action("preferences", None)
            elif name == "quick":
                app.activate_action("quick-add", None)
                app.quick.entry.set_text(arg)
            elif name == "size":
                w, h = arg.split("x")
                win.set_default_size(int(w), int(h))
        return False

    def shoot():
        target = win
        if app.quick is not None and app.quick.get_visible():
            target = app.quick
        dialog = win.get_visible_dialog() if hasattr(win, "get_visible_dialog") else None
        save_png(target, path)
        shots.append(path)
        if os.environ.get("ROSK_SNAPSHOT_QUIT", "1") == "1":
            app.activate_action("quit", None)
        return False

    GLib.timeout_add(600, run_steps)
    GLib.timeout_add(delay, shoot)
