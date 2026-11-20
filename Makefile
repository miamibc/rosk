# Rosk — install into your home directory (no root needed), like picco.
#   make install     install the app, the background service and the GNOME Shell extension
#   make uninstall   remove everything except your data (~/.local/share/rosk)
#   make run         run from the source tree
#   make test        run the tests

.PHONY: all venv run test pot locale check-deps install restart uninstall dist clean

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
LANGS     = ru

all: locale

venv:
	$(PYTHON) -m venv --system-site-packages .venv
	.venv/bin/pip install -q -r requirements.txt pytest

run: locale
	@[ -x .venv/bin/python ] || $(MAKE) venv
	.venv/bin/python -m rosk

test:
	@[ -x .venv/bin/python ] || $(MAKE) venv
	.venv/bin/python -m pytest -q tests

pot:
	xgettext --from-code=UTF-8 --keyword=_ --keyword=C_:1c,2 --keyword=ngettext:1,2 \
		-L Python -o po/rosk.pot $$(find rosk -name '*.py' | sort)
	xgettext --from-code=UTF-8 --keyword=_ -L JavaScript -j -o po/rosk.pot extension/extension.js
	for l in $(LANGS); do msgmerge -q -U --backup=none po/$$l.po po/rosk.pot; done

locale:
	@for l in $(LANGS); do \
		mkdir -p build/locale/$$l/LC_MESSAGES; \
		msgfmt -o build/locale/$$l/LC_MESSAGES/rosk.mo po/$$l.po; \
	done

check-deps:
	@$(PYTHON) -c "import gi; \
	[gi.require_version(n, v) for n, v in (('Gtk','4.0'),('Adw','1'),('Goa','1.0'),('Secret','1'))]; \
	from gi.repository import Gtk, Adw, Goa, Secret" 2>/dev/null || { \
		echo 'Missing system packages. Install them with:'; \
		echo '  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-goa-1.0 gir1.2-secret-1 python3-venv'; \
		exit 1; }

install: check-deps locale
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

restart:
	-gdbus call --session --dest $(APP_ID) --object-path /io/github/miamibc/rosk \
		--method org.gtk.Actions.Activate quit [] {} >/dev/null 2>&1
	@sleep 1
	-gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
		--method org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1
	setsid "$(BINDIR)/rosk" --background >/dev/null 2>&1 < /dev/null &

uninstall:
	-gdbus call --session --dest $(APP_ID) --object-path /io/github/miamibc/rosk \
		--method org.gtk.Actions.Activate quit [] {} >/dev/null 2>&1
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

dist:
	mkdir -p dist
	git archive --format=tar.gz --prefix=rosk-$(VERSION)/ -o dist/rosk-$(VERSION).tar.gz HEAD
	@echo "dist/rosk-$(VERSION).tar.gz"

clean:
	rm -rf build dist .pytest_cache
