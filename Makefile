# Rosk — tasks and notes for GNOME. Run `make` to see the targets.

APP_ID    = io.github.miamibc.rosk
EXT_UUID  = rosk@miamibc.github.io
VERSION   = $(shell python3 -c "import rosk; print(rosk.__version__)")

PREFIX   ?= $(HOME)/.local
BINDIR    = $(PREFIX)/bin
LIBDIR    = $(PREFIX)/lib/rosk
DATADIR   = $(PREFIX)/share
EXTDIR    = $(DATADIR)/gnome-shell/extensions/$(EXT_UUID)
AUTOSTART = $(HOME)/.config/autostart
PYTHON   ?= /usr/bin/python3
VENV      = .venv/bin/python
LANGS     = ru

# development copy: own app ID and data, so it runs next to the installed Rosk
DEV_ENV   = ROSK_APP_ID=$(APP_ID).Dev ROSK_DATA_DIR=$(CURDIR)/build/dev-data
SERVICE   = --session --dest $(APP_ID) --object-path /io/github/miamibc/rosk

.DEFAULT_GOAL := help
.PHONY: help venv run run-offline test lint check pot locale screenshots check-deps install \
	uninstall restart start quit status sync logs dist clean

help:  ## Show this help
	@echo "Rosk $(VERSION)"
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "} {printf "  make %-13s %s\n", $$1, $$2}'

# ---------- development ----------

venv:  ## Create .venv (system PyGObject + gkeepapi, pytest, ruff)
	$(PYTHON) -m venv --system-site-packages .venv
	.venv/bin/pip install -q -r requirements.txt pytest ruff

$(VENV):
	$(MAKE) venv

run: $(VENV) locale  ## Run from the source tree with its own data in build/dev-data (syncs with Google)
	$(DEV_ENV) $(VENV) -m rosk

run-offline: $(VENV) locale  ## Same, without sync and without the panel icon
	$(DEV_ENV) ROSK_NO_SYNC=1 ROSK_NO_TRAY=1 $(VENV) -m rosk

test: $(VENV)  ## Run the tests
	$(VENV) -m pytest -q tests

lint: $(VENV)  ## Check Python code with ruff
	@$(VENV) -m ruff --version >/dev/null 2>&1 || .venv/bin/pip install -q ruff
	$(VENV) -m ruff check --select E9,F --ignore F401 rosk tests tools

check: lint test  ## Everything before a commit: lint, tests, extension, schema, translations
	node --check --input-type=module < extension/extension.js
	glib-compile-schemas --strict --dry-run extension/schemas
	@for l in $(LANGS); do msgfmt -c -o /dev/null po/$$l.po; done
	@echo "All checks passed"

pot:  ## Update po/rosk.pot and merge it into the translations
	xgettext --from-code=UTF-8 --keyword=_ --keyword=C_:1c,2 --keyword=ngettext:1,2 \
		-L Python -o po/rosk.pot $$(find rosk -name '*.py' | sort)
	xgettext --from-code=UTF-8 --keyword=_ -L JavaScript -j -o po/rosk.pot extension/extension.js
	for l in $(LANGS); do msgmerge -q -U --backup=none po/$$l.po po/rosk.pot; done

locale:  ## Compile translations into build/locale
	@for l in $(LANGS); do \
		mkdir -p build/locale/$$l/LC_MESSAGES; \
		msgfmt -o build/locale/$$l/LC_MESSAGES/rosk.mo po/$$l.po; \
	done

screenshots: $(VENV) locale  ## Regenerate docs/screenshots
	$(VENV) tools/screenshots.py

# ---------- install ----------

check-deps:
	@$(PYTHON) -c "import gi; \
	[gi.require_version(n, v) for n, v in (('Gtk','4.0'),('Adw','1'),('Goa','1.0'),('Secret','1'))]; \
	from gi.repository import Gtk, Adw, Goa, Secret" 2>/dev/null || { \
		echo 'Missing system packages. Install them with:'; \
		echo '  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-goa-1.0 gir1.2-secret-1 python3-venv'; \
		exit 1; }

install: check-deps locale  ## Install into ~/.local: app, background service, Shell extension
	@echo "Installing Rosk $(VERSION) into $(PREFIX)"
	rm -rf "$(LIBDIR)/rosk" "$(LIBDIR)/locale"
	mkdir -p "$(LIBDIR)" "$(BINDIR)"
	cp -r rosk "$(LIBDIR)/rosk"
	find "$(LIBDIR)/rosk" -name __pycache__ -prune -exec rm -rf {} +
	cp -r build/locale "$(LIBDIR)/locale"
	[ -x "$(LIBDIR)/venv/bin/python" ] || $(PYTHON) -m venv --system-site-packages "$(LIBDIR)/venv"
	"$(LIBDIR)/venv/bin/pip" install -q --disable-pip-version-check -r requirements.txt
	printf '#!%s -I\nimport sys\nsys.path.insert(0, "%s")\nfrom rosk.app import main\nsys.exit(main())\n' \
		"$(LIBDIR)/venv/bin/python" "$(LIBDIR)" > "$(BINDIR)/rosk"
	chmod 755 "$(BINDIR)/rosk"
	install -Dm644 data/icons/hicolor/scalable/apps/$(APP_ID).svg \
		"$(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg"
	install -Dm644 data/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg \
		"$(DATADIR)/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg"
	mkdir -p "$(DATADIR)/applications" "$(DATADIR)/dbus-1/services" \
		"$(DATADIR)/gnome-shell/search-providers" "$(AUTOSTART)"
	sed 's|@BINDIR@|$(BINDIR)|g' data/$(APP_ID).desktop.in > "$(DATADIR)/applications/$(APP_ID).desktop"
	sed 's|@BINDIR@|$(BINDIR)|g' data/$(APP_ID).service.in > "$(DATADIR)/dbus-1/services/$(APP_ID).service"
	install -m644 data/$(APP_ID).search-provider.ini "$(DATADIR)/gnome-shell/search-providers/"
	sed 's|@BINDIR@|$(BINDIR)|g' data/$(APP_ID)-autostart.desktop.in > "$(AUTOSTART)/$(APP_ID).desktop"
	-gtk-update-icon-cache -q -f -t "$(DATADIR)/icons/hicolor" 2>/dev/null
	-update-desktop-database -q "$(DATADIR)/applications" 2>/dev/null
	rm -rf "$(EXTDIR)"
	mkdir -p "$(EXTDIR)"
	cp -r extension/* "$(EXTDIR)/"
	glib-compile-schemas "$(EXTDIR)/schemas"
	cp -r build/locale "$(EXTDIR)/locale"
	-$(PYTHON) tools/enable_extension.py $(EXT_UUID)
	$(MAKE) --no-print-directory restart
	@echo
	@echo "Done. Rosk is in the app grid and its icon is in the top bar."
	@echo "On Wayland the full panel extension starts after the next login;"
	@echo "until then a compact icon is shown through AppIndicator."

uninstall:  ## Remove the installation (your data in ~/.local/share/rosk stays)
	-gdbus call $(SERVICE) --method org.gtk.Actions.Activate quit [] {} >/dev/null 2>&1
	-gnome-extensions disable $(EXT_UUID) 2>/dev/null
	rm -rf "$(LIBDIR)" "$(EXTDIR)"
	rm -f "$(BINDIR)/rosk" \
		"$(DATADIR)/applications/$(APP_ID).desktop" \
		"$(DATADIR)/dbus-1/services/$(APP_ID).service" \
		"$(DATADIR)/gnome-shell/search-providers/$(APP_ID).search-provider.ini" \
		"$(DATADIR)/icons/hicolor/scalable/apps/$(APP_ID).svg" \
		"$(DATADIR)/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg" \
		"$(AUTOSTART)/$(APP_ID).desktop"
	@echo "Removed. Your data stays in ~/.local/share/rosk"

# ---------- the installed service ----------

restart: quit start  ## Restart the installed background service

start:  ## Start the installed service (through D-Bus, so its log goes to the journal)
	-@gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
		--method org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1
	@gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
		--method org.freedesktop.DBus.StartServiceByName $(APP_ID) 0 >/dev/null && echo "Rosk started"

quit:  ## Stop the installed service
	-@gdbus call $(SERVICE) --method org.gtk.Actions.Activate quit [] {} >/dev/null 2>&1 \
		&& echo "Rosk stopped" || echo "Rosk was not running"
	@sleep 1

status:  ## Is the service running, what is due, is the panel extension active
	@if gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
		--method org.freedesktop.DBus.NameHasOwner $(APP_ID) | grep -q true; then \
		echo "service:   running"; \
		echo "counts:    $$(gdbus call $(SERVICE)/Service --method $(APP_ID).Service.Counts \
			| sed "s/^('//; s/',)$$//")"; \
	else echo "service:   not running (make start)"; fi
	@state=$$(gnome-extensions info $(EXT_UUID) 2>/dev/null | grep -i 'state' | sed 's/^ *State: *//'); \
		echo "extension: $${state:-not loaded yet (log out and back in)}"

sync:  ## Sync with Google now
	@gdbus call $(SERVICE)/Service --method $(APP_ID).Service.Sync >/dev/null && echo "Sync requested"

logs:  ## Recent log lines of the service and the Shell extension
	@journalctl --user -b -o short-iso --no-pager 2>/dev/null | grep -i rosk | tail -n 40 || true

# ---------- release ----------

dist:  ## Source archive in dist/
	mkdir -p dist
	git archive --format=tar.gz --prefix=rosk-$(VERSION)/ -o dist/rosk-$(VERSION).tar.gz HEAD
	@echo "dist/rosk-$(VERSION).tar.gz"

clean:  ## Remove build/, dist/ and caches (not your data)
	rm -rf build dist .pytest_cache .ruff_cache
