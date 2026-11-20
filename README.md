# Rosk

**English** · [Русский](README.ru.md)

Rosk is a quick note you write down and then cross out. It is a task and note manager for the
GNOME desktop, built for speed and brevity: getting a thought into your list takes a couple of
seconds, and checking it off even less.

> Status: design stage. The specification is ready; there is no code yet.

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

## Requirements

GNOME 50 or newer, with a Google account added in Settings → Online Accounts.

## Documentation

The design documents are in Russian.

- [Specification](docs/SPEC.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Google API capabilities and limits](docs/GOOGLE_APIS.md)
- [Decisions](docs/DECISIONS.md)
- [Roadmap](docs/ROADMAP.md)
