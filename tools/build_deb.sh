#!/bin/sh
# Builds dist/rosk_<version>_all.deb: a system-wide install into /usr (and /etc/xdg/autostart),
# the same pieces `make install` puts into ~/.local. Run through `make deb` (it compiles the
# translations first). Needs: dpkg-deb, glib-compile-schemas, pip.
#   tools/build_deb.sh <version> <python with pip>
set -eu

VERSION=$1
PIP_PYTHON=$2
APP_ID=io.github.miamibc.rosk
EXT_UUID=rosk@miamibc.github.io
ROOT=build/deb/root
OUT=dist/rosk_${VERSION}_all.deb

rm -rf build/deb
mkdir -p "$ROOT/DEBIAN" dist

# the application and the Python packages Ubuntu does not have
LIB=$ROOT/usr/lib/rosk
mkdir -p "$LIB"
cp -r rosk "$LIB/rosk"
cp -r build/locale "$LIB/locale"
"$PIP_PYTHON" -m pip install -q --disable-pip-version-check --no-deps --no-compile \
    --target "$LIB/vendor" -r packaging/vendor.txt
# keep *.dist-info: gpsoauth reads its own version from it
find "$LIB" \( -name __pycache__ -o -name bin \) -prune -exec rm -rf {} +

mkdir -p "$ROOT/usr/bin"
cat > "$ROOT/usr/bin/rosk" <<'EOF'
#!/usr/bin/python3 -I
import sys
sys.path[:0] = ["/usr/lib/rosk", "/usr/lib/rosk/vendor"]
from rosk.app import main
sys.exit(main())
EOF
chmod 755 "$ROOT/usr/bin/rosk"

# desktop integration, as in `make install` with BINDIR=/usr/bin
SHARE=$ROOT/usr/share
install -Dm644 data/icons/hicolor/scalable/apps/$APP_ID.svg \
    "$SHARE/icons/hicolor/scalable/apps/$APP_ID.svg"
install -Dm644 data/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg \
    "$SHARE/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
mkdir -p "$SHARE/applications" "$SHARE/dbus-1/services" "$SHARE/gnome-shell/search-providers" \
    "$ROOT/etc/xdg/autostart"
sed 's|@BINDIR@|/usr/bin|g' data/$APP_ID.desktop.in > "$SHARE/applications/$APP_ID.desktop"
sed 's|@BINDIR@|/usr/bin|g' data/$APP_ID.service.in > "$SHARE/dbus-1/services/$APP_ID.service"
install -m644 data/$APP_ID.search-provider.ini "$SHARE/gnome-shell/search-providers/"
sed 's|@BINDIR@|/usr/bin|g' data/$APP_ID-autostart.desktop.in > "$ROOT/etc/xdg/autostart/$APP_ID.desktop"

# the Shell extension; Rosk turns it on for each user once, at the first start
EXT=$SHARE/gnome-shell/extensions/$EXT_UUID
mkdir -p "$EXT"
cp -r extension/* "$EXT/"
glib-compile-schemas "$EXT/schemas"
cp -r build/locale "$EXT/locale"

install -Dm644 LICENSE "$SHARE/doc/rosk/copyright"

# metadata
chmod -R u+rwX,go+rX,go-w "$ROOT"
SIZE=$(du -sk --exclude=DEBIAN "$ROOT" | cut -f1)
sed -e "s|@VERSION@|$VERSION|" -e "s|@SIZE@|$SIZE|" packaging/control.in > "$ROOT/DEBIAN/control"
printf '/etc/xdg/autostart/%s.desktop\n' "$APP_ID" > "$ROOT/DEBIAN/conffiles"

dpkg-deb --root-owner-group -Zxz --build "$ROOT" "$OUT" >/dev/null
echo "$OUT"
