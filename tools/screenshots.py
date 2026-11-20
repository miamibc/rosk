"""Generates README screenshots from made-up demo data (never from the user's database).

    .venv/bin/python tools/screenshots.py            # writes docs/screenshots/*.png

Runs a separate app instance (own app ID, temporary data dir, no sync, no tray icon).
"""

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
DATA = Path(tempfile.mkdtemp(prefix="rosk-demo-"))

os.environ.update(ROSK_APP_ID="io.github.miamibc.rosk.Screenshots", ROSK_DATA_DIR=str(DATA),
                  ROSK_NO_SYNC="1", ROSK_NO_TRAY="1", LANGUAGE="en", LC_ALL="C.UTF-8")
sys.path.insert(0, str(ROOT))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from rosk.app import RoskApp  # noqa: E402
from rosk.store.repo import Repo  # noqa: E402
from rosk.ui.debug import save_png  # noqa: E402

TODAY = date.today()


def d(days):
    return (TODAY + timedelta(days=days)).isoformat()


def seed(repo):
    """Little Red Riding Hood's to-do lists."""
    s = repo.store
    lists = {name: repo.create_collection("tasklist", name)
             for name in ("Home", "Grandma", "Forest", "Woodcutter")}

    def task(title, lst, due=None, body="", subs=(), done_subs=(), links=None, done=False):
        iid = repo.create_task(title, collection_id=lists[lst], due=due, body=body, notify=False)
        prev = None
        for sub in subs:
            prev = repo.create_task(sub, parent_id=iid, after_id=prev, notify=False)
            if sub in done_subs:
                repo.set_done(prev)
        if links:
            with s.tx():
                s.update_item(iid, touch=False, links_json=json.dumps(links),
                              web_view_link="https://tasks.google.com/")
        if done:
            repo.set_done(iid)
        return iid

    # created bottom-up: new tasks go on top
    task("Return Grandma's pie dish", "Grandma", d(-3))
    task("Reply to Grandma's letter", "Grandma", d(-1),
         links=[{"type": "email", "description": "Come over on Sunday",
                 "link": "https://mail.google.com/"}])
    task("Ask the woodcutter about the short path", "Woodcutter", d(0))
    task("Wash the red hood", "Home", d(0))
    task("Bake pies for Grandma", "Home", d(0), body="Apple and cherry, six of each",
         subs=("Pick apples", "Make the dough", "Bake until golden"), done_subs=("Pick apples",))
    task("Pick flowers on the way", "Forest", d(1))
    task("Find the hare who borrowed my basket", "Forest", d(3))
    task("Visit Grandma", "Grandma", d(5),
         subs=("Pack the basket", "Take the long path", "Knock three times"),
         done_subs=("Pack the basket",))
    task("Mow the lawn at Grandma's", "Grandma", d(9))
    task("Buy a birch sapling", "Home", d(12))
    task("Learn the wolf-warning whistle", "Woodcutter")
    task("Mend the basket", "Home", done=True)
    task("Buy a red ribbon", "Home", d(-6), done=True)
    with s.tx():
        s.set_meta("default_list", lists["Home"])

    recipes = repo.ensure_collection("label", "Recipes")
    forest = repo.ensure_collection("label", "Forest")
    ideas = repo.ensure_collection("label", "Ideas")

    def note(title, body="", items=None, color="DEFAULT", labels=(), pinned=False):
        checklist = [{"text": t, "checked": c} for t, c in items] if items is not None else None
        iid = repo.create_note(title, body=body, checklist=checklist, labels=labels, color=color,
                               notify=False)
        if pinned:
            repo.update(iid, pinned=1)
        return iid

    note("Lost and found", "The hare borrowed the basket on Tuesday. Promised to bring it back "
         "by Friday.")
    note("Garden", items=[("Mow the lawn", False), ("Plant the birch", False),
                          ("Water the roses", True)], color="TEAL")
    note("Picnic ideas", items=[("Find the hare", False), ("Checkered blanket", False),
                                ("Lemonade", True)], color="PINK", labels=[ideas])
    note("The woodcutter", "Lives by the river. Knows every path in the forest and owes me "
         "a favour.", color="BLUE")
    note("Things the wolf said", "“What big eyes you have!” — All the better to see you with.\n\n"
         "“What big ears you have!” — All the better to hear you with.", color="GRAY",
         labels=[forest])
    note("Grandma's pie recipe", items=[("2 cups of flour", True), ("Butter", True),
                                        ("4 apples", False), ("Cinnamon", False), ("Sugar", False)],
         color="YELLOW", labels=[recipes])
    note("Forest path", "Take the long path past the old oak.\nDon't stop to chat with strangers.\n"
         "Be back before sunset.", color="GREEN", labels=[forest], pinned=True)
    note("Basket for Grandma", items=[("Apple pies", False), ("Cherry pies", False),
                                      ("Pot of butter", True), ("Honey", False),
                                      ("Fresh flowers", True), ("Reading glasses", False),
                                      ("Warm socks", True)],
         color="ORANGE", pinned=True)


def pump(ms):
    end = time.time() + ms / 1000
    ctx = GLib.MainContext.default()
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.005)


# every screenshot gets the proportions of the main window shots
WIDTH, HEIGHT = 1180, 760


def compose(base, overlay, out, x, y):
    subprocess.run(["magick", str(base), str(overlay), "-geometry", f"+{x}+{y}", "-composite",
                    str(out)], check=True)


def fit_ratio(path, trim=False):
    """Trims transparent margins (optional), then pads the image with transparency
    at the bottom or the sides so that width:height == WIDTH:HEIGHT."""
    if trim:
        subprocess.run(["magick", str(path), "-trim", "+repage", str(path)], check=True)
    size = subprocess.run(["magick", "identify", "-format", "%w %h", str(path)],
                          capture_output=True, text=True, check=True).stdout.split()
    w, h = int(size[0]), int(size[1])
    g = math.gcd(WIDTH, HEIGHT)
    rw, rh = WIDTH // g, HEIGHT // g  # 59:38 — exact proportions need multiples of these
    k = max(math.ceil(w / rw), math.ceil(h / rh))
    new_w, new_h = k * rw, k * rh
    gravity = "north" if w * HEIGHT >= h * WIDTH else "center"
    if (new_w, new_h) != (w, h):
        subprocess.run(["magick", str(path), "-background", "none", "-gravity", gravity,
                        "-extent", f"{new_w}x{new_h}", str(path)], check=True)


def shots(app):
    OUT.mkdir(parents=True, exist_ok=True)
    win = app._window()
    win.set_default_size(WIDTH, HEIGHT)
    win.present()
    pump(800)
    style = Adw.StyleManager.get_default()

    def shot(name, target=None, wait=600):
        pump(wait)
        save_png(target or win, str(OUT / name))
        fit_ratio(OUT / name)
        print("saved", name)

    style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    win.show_view("today")
    shot("today.png")

    home = app.repo.find_collection("tasklist", "Home")
    win.show_view("list:" + home["id"])
    pies = app.repo.store.items("title='Bake pies for Grandma'")[0]
    win.open_item(pies["id"], focus=False)
    shot("list-editor.png")
    win._close_editor()

    win.show_view("notes")
    shot("notes.png")

    basket = app.repo.store.items("title='Basket for Grandma'")[0]
    win.open_item(basket["id"], focus=False)
    pump(300)
    win.editor._show_checked = True  # unfold the checked items for the picture
    win.editor._fill_checklist()
    shot("note-editor.png")
    win._close_editor()

    win.show_view("today")
    win.add_entry.set_text("Bring the basket tomorrow #Grandma: - pies - butter - flowers")
    win.add_entry.set_position(-1)
    shot("quick-syntax.png")
    win.add_entry.set_text("")

    style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    win.show_view("upcoming")
    shot("upcoming-light.png", wait=900)
    win.show_view("notes")
    shot("notes-light.png", wait=900)
    style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    pump(500)

    win.show_view("today")
    app.activate_action("about", None)
    shot("about.png", wait=900)
    dialog = win.get_visible_dialog()
    if dialog:
        dialog.close()
    pump(400)

    app.activate_action("quick-add", None)
    pump(600)
    q = app.quick
    q.mode.set_active_name("task")
    q.entry.set_text("Pick strawberries fri #")
    q.entry.set_position(-1)
    pump(700)
    tmp = Path(tempfile.mkdtemp())
    save_png(q, str(tmp / "q.png"))
    save_png(q.completer.popover, str(tmp / "p.png"))
    # place the suggestions under the entry, as they appear on screen
    subprocess.run(["magick", str(tmp / "q.png"), "-background", "none", "-gravity", "north",
                    "-extent", "520x400", str(tmp / "qx.png")], check=True)
    compose(tmp / "qx.png", tmp / "p.png", OUT / "quick-add.png", 2, 88)
    fit_ratio(OUT / "quick-add.png", trim=True)
    print("saved quick-add.png")
    q.close()
    shutil.rmtree(tmp, ignore_errors=True)
    app.activate_action("quit", None)


def main():
    seed(Repo(DATA / "rosk.db"))
    app = RoskApp()
    app.connect("activate", lambda *_: GLib.timeout_add(300, lambda: (shots(app), False)[1]))
    app.run([sys.argv[0]])
    shutil.rmtree(DATA, ignore_errors=True)


if __name__ == "__main__":
    main()
