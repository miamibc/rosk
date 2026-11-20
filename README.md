# Rosk

**English** · [Русский](README.ru.md)

Rosk is a quick note you write down and then cross out. It is a task and note manager for the
GNOME desktop, built for speed and brevity: getting a thought into your list takes a couple of
seconds, and checking it off even less.

> Status: first working version (0.1). Tasks sync with Google Tasks; Keep sync needs a one-time login in Preferences.

![Rosk: today's tasks](docs/screenshots/today.png)

## What it does

- **Quick capture from the top bar.** Rosk adds an icon to the GNOME panel. Click it or press a
  shortcut, type one line such as `Call the bank tomorrow #work`, and press Enter.
- **Your Google tasks and notes in one place.** Tasks sync with Google Tasks and notes with
  Google Keep. Rosk shows them side by side but keeps them apart in Google: tasks stay in Tasks,
  notes stay in Keep.
- **Works offline.** Every change is saved locally right away and sent to Google once the
  connection is back.
- **Everything at a glance.** All lists in one window, with subtasks and deadlines. Whatever is due
  today or overdue is highlighted.
- **Minimal and native to GNOME.** GTK 4 and libadwaita, light and dark themes, full keyboard
  control.

## Screenshots

| | |
|---|---|
| ![Task list with the editor](docs/screenshots/list-editor.png) | ![Notes](docs/screenshots/notes.png) |
| Lists, subtasks and the editor pane | Google Keep notes, pinned and colored |
| ![Quick-capture syntax](docs/screenshots/quick-syntax.png) | ![Upcoming, light style](docs/screenshots/upcoming-light.png) |
| One-line syntax: date, list and subtasks are recognized as you type | Upcoming tasks by day, light style |
| ![Quick add](docs/screenshots/quick-add.png) | ![Notes, light style](docs/screenshots/notes-light.png) |
| Quick add: task or note, `#` suggests your lists | Notes in the light style |

Screenshots use made-up data; `tools/screenshots.py` regenerates them.

## Requirements

GNOME 48 or newer, with a Google account added in Settings → Online Accounts, and these
packages (preinstalled on Ubuntu 26.04):

```sh
sudo apt install python3-gi python3-venv gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-goa-1.0 gir1.2-secret-1
# for the Google Keep sign-in window:
sudo apt install gir1.2-webkit-6.0
```

## Install

```sh
make install      # into ~/.local, no root needed
make uninstall    # removes the app, keeps your data in ~/.local/share/rosk
make status       # is it running, what is due, is the panel extension active
```

Rosk then runs in the background, its icon appears in the top bar and it starts with your
session. On Wayland GNOME loads new Shell extensions only at login, so until you log out and
back in Rosk shows a compact AppIndicator icon instead.

## Use

- **Super+Alt+R** or a click on the top-bar icon: type one line, press Enter. Shift+Enter keeps
  the box open for the next one.
- Quick syntax: `tomorrow`, `fri`, `12.12`, `in 3 days` (Russian works too: `завтра`, `в пт`,
  `через неделю`), `#list` for the list, `n:` at the start for a note, and
  `Trip: - tickets - hotel` for subtasks or checklist items.
- Search tasks and notes right from the GNOME overview.
- Google Keep: Preferences → Google Keep → Sign in with Google. Google has no public Keep API for personal accounts,
  so Rosk uses the unofficial [gkeepapi](https://github.com/kiwiz/gkeepapi) library.

## Make targets

Run `make` to list them all.

| Command | What it does |
|---|---|
| **Install and service** | |
| `make install` | Install into `~/.local`: app, background service, panel extension, search provider, autostart. Run it again to update |
| `make uninstall` | Remove the installation; your data in `~/.local/share/rosk` stays |
| `make status` | Whether the service is running, what is due today, whether the panel extension is active |
| `make sync` | Sync with Google now |
| `make restart` / `start` / `quit` | Restart, start or stop the installed service |
| `make logs` | Recent log lines of the service and the extension |
| **Development** | |
| `make venv` | Create `.venv` on top of the system PyGObject, with gkeepapi, pytest and ruff |
| `make run` | Run from the source tree next to the installed Rosk, with its own data in `build/dev-data` (syncs with Google) |
| `make run-offline` | The same without sync and without the panel icon, to try things safely |
| `make test` | Run the tests |
| `make lint` | Check Python code with ruff |
| `make check` | Everything before a commit: lint, tests, extension syntax, settings schema, translations |
| `make pot` | Update `po/rosk.pot` from the code and merge it into the translations |
| `make locale` | Compile translations |
| `make screenshots` | Regenerate the README screenshots from made-up demo data |
| **Release** | |
| `make dist` | Source archive `dist/rosk-<version>.tar.gz` |
| `make clean` | Remove `build/`, `dist/` and caches (not your data) |

## Documentation

The design documents are in Russian.

- [Specification](docs/SPEC.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Google API capabilities and limits](docs/GOOGLE_APIS.md)
- [Decisions](docs/DECISIONS.md)
- [Roadmap](docs/ROADMAP.md)
