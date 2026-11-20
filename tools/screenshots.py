"""Generates README screenshots from made-up demo data (never from the user's database).

    .venv/bin/python tools/screenshots.py            # writes docs/screenshots/*.png

Runs a separate app instance (own app ID, temporary data dir, no sync, no tray icon).
"""

import json
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
    s = repo.store
    lists = {name: repo.create_collection("tasklist", name)
             for name in ("Personal", "Work", "Home", "Trip to Lisbon")}

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
    task("Renew the domain name", "Work", d(-3))
    task("Reply to Anna about the hosting", "Work", d(-1),
         links=[{"type": "email", "description": "Hosting options", "link": "https://mail.google.com/"}])
    task("Book a table for Friday", "Personal", d(0))
    task("Water the plants", "Home", d(0))
    task("Prepare the quarterly report", "Work", d(0), body="Numbers from the sales dashboard",
         subs=("Collect numbers", "Draft slides", "Send to the team"), done_subs=("Collect numbers",))
    task("Call the bank", "Personal", d(1))
    task("Dentist appointment", "Personal", d(3))
    task("Flights and hotel", "Trip to Lisbon", d(5), subs=("Flights", "Hotel near Alfama", "Airport transfer"),
         done_subs=("Flights",))
    task("Pack the camera", "Trip to Lisbon", d(9))
    task("Pastéis de nata tour", "Trip to Lisbon")
    task("Fix the kitchen tap", "Home", d(12))
    task("Read “The Pragmatic Programmer”", "Personal")
    task("Update the website footer", "Work", done=True)
    task("Buy a birthday present for Mia", "Personal", d(-6), done=True)
    with s.tx():
        s.set_meta("default_list", lists["Personal"])

    ideas = repo.ensure_collection("label", "Ideas")
    shopping = repo.ensure_collection("label", "Shopping")
    reading = repo.ensure_collection("label", "Reading")

    def note(title, body="", items=None, color="DEFAULT", labels=(), pinned=False):
        checklist = [{"text": t, "checked": c} for t, c in items] if items is not None else None
        iid = repo.create_note(title, body=body, checklist=checklist, labels=labels, color=color,
                               notify=False)
        if pinned:
            repo.update(iid, pinned=1)

    note("Weekend", "Bike ride along the river, then lunch at the market.", color="TEAL")
    note("Books", items=[("Project Hail Mary", False), ("Dune", True), ("Piranesi", False),
                         ("The Left Hand of Darkness", False)], color="PURPLE", labels=[reading])
    note("Wi-Fi at the cottage", "Network: cottage-5G\nPassword is on the fridge.")
    note("App ideas", "A tiny timer that lives in the top bar.\n\nA shared shopping list "
         "that syncs with Keep.\n\nDark-mode friendly color picker.",
         color="YELLOW", labels=[ideas])
    note("Gift ideas", items=[("Headphones for Leo", False), ("Plant for mom", False),
                              ("Board game", True)], color="PINK")
    note("Quote", "“Simplicity is prerequisite for reliability.” — Edsger Dijkstra", color="BLUE")
    note("Groceries", items=[("Milk", False), ("Bread", False), ("Coffee beans", False),
                             ("Apples", False), ("Olive oil", True), ("Eggs", True)],
         color="ORANGE", labels=[shopping], pinned=True)
    note("Lisbon", items=[("Tram 28", False), ("LX Factory", False), ("Belém tower", False),
                          ("Time Out Market", True)], color="GREEN", pinned=True)


def pump(ms):
    end = time.time() + ms / 1000
    ctx = GLib.MainContext.default()
    while time.time() < end:
        ctx.iteration(False)
        time.sleep(0.005)


def compose(base, overlay, out, x, y):
    subprocess.run(["magick", str(base), str(overlay), "-geometry", f"+{x}+{y}", "-composite",
                    str(out)], check=True)


def shots(app):
    OUT.mkdir(parents=True, exist_ok=True)
    win = app._window()
    win.set_default_size(1180, 760)
    win.present()
    pump(800)
    style = Adw.StyleManager.get_default()

    def shot(name, target=None, wait=600):
        pump(wait)
        save_png(target or win, str(OUT / name))
        print("saved", name)

    style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    win.show_view("today")
    shot("today.png")

    work = app.repo.find_collection("tasklist", "Work")
    win.show_view("list:" + work["id"])
    report = app.repo.store.items("title='Prepare the quarterly report'")[0]
    win.open_item(report["id"], focus=False)
    shot("list-editor.png")
    win._close_editor()

    win.show_view("notes")
    shot("notes.png")

    win.show_view("today")
    win.add_entry.set_text("Call the plumber tomorrow #Home: - find the number - book a visit")
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

    app.activate_action("quick-add", None)
    pump(600)
    q = app.quick
    q.mode.set_active_name("task")
    q.entry.set_text("Pick up the parcel fri #")
    q.entry.set_position(-1)
    pump(700)
    tmp = Path(tempfile.mkdtemp())
    save_png(q, str(tmp / "q.png"))
    save_png(q.completer.popover, str(tmp / "p.png"))
    # place the suggestions under the entry, as they appear on screen
    subprocess.run(["magick", str(tmp / "q.png"), "-background", "none", "-gravity", "north",
                    "-extent", "520x400", str(tmp / "qx.png")], check=True)
    compose(tmp / "qx.png", tmp / "p.png", OUT / "quick-add.png", 2, 88)
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
