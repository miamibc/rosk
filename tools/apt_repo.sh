#!/bin/sh
# Adds Debian packages to a signed APT repository in a directory (published on GitHub Pages)
# and regenerates its indexes. Packages already in the pool stay, so older versions remain
# installable. Needs apt-ftparchive (apt-utils) and gpg with the signing key imported; a key
# with a passphrase takes it from the file named in $GPG_PASSPHRASE_FILE.
#   tools/apt_repo.sh <repo dir> <signing key id> <url> [package.deb ...]
# Users then add (see the apt.html this writes):
#   deb [signed-by=/etc/apt/keyrings/rosk.gpg] <url> stable main
set -eu

REPO=$1
KEY=$2
URL=$3
shift 3

SIGN="--batch --yes --local-user $KEY --digest-algo SHA512"  # not SHA-1 from old key prefs
if [ -n "${GPG_PASSPHRASE_FILE:-}" ]; then
    SIGN="$SIGN --pinentry-mode loopback --passphrase-file $GPG_PASSPHRASE_FILE"
fi
POOL=pool/main/r/rosk
ARCHES="amd64 arm64"   # Rosk is Architecture: all; it is listed for every architecture
mkdir -p "$REPO/$POOL"
for deb in "$@"; do
    cp "$deb" "$REPO/$POOL/"
done

cd "$REPO"
rm -rf dists
for arch in $ARCHES; do
    dir=dists/stable/main/binary-$arch
    mkdir -p "$dir"
    apt-ftparchive --arch "$arch" packages pool > "$dir/Packages"
    gzip -9kn "$dir/Packages"
done
apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=Rosk \
    -o APT::FTPArchive::Release::Label=Rosk \
    -o APT::FTPArchive::Release::Suite=stable \
    -o APT::FTPArchive::Release::Codename=stable \
    -o APT::FTPArchive::Release::Components=main \
    -o "APT::FTPArchive::Release::Architectures=$ARCHES" \
    -o APT::FTPArchive::Release::Description="Rosk: tasks and notes for GNOME" \
    release dists/stable > Release.tmp   # outside dists/stable: it must not list itself
mv Release.tmp dists/stable/Release
# shellcheck disable=SC2086
gpg $SIGN --clearsign -o dists/stable/InRelease dists/stable/Release
# shellcheck disable=SC2086
gpg $SIGN --detach-sign --armor -o dists/stable/Release.gpg dists/stable/Release
gpg --batch --yes --export "$KEY" > rosk-archive-keyring.gpg
gpg --batch --yes --armor --export "$KEY" > rosk-archive-keyring.asc
touch .nojekyll   # GitHub Pages: serve the files as they are

latest=$(ls "$POOL"/*.deb | sort -V | tail -1)
cat > apt.html <<EOF
<!DOCTYPE html>
<html lang="en">
<meta charset="utf-8">
<title>Rosk APT repository</title>
<style>body{font:16px/1.5 sans-serif;max-width:46em;margin:2em auto;padding:0 1em}
pre{background:#f4f4f4;padding:1em;overflow-x:auto}</style>
<h1>Rosk APT repository</h1>
<p>Rosk: quick tasks and notes for GNOME, synced with Google Tasks and Keep.
Needs GNOME 48 or newer (Ubuntu 25.04+, Debian 13+).
Source: <a href="https://github.com/miamibc/rosk">github.com/miamibc/rosk</a>.</p>
<pre>sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL $URL/rosk-archive-keyring.gpg | sudo tee /etc/apt/keyrings/rosk.gpg &gt;/dev/null
echo "deb [signed-by=/etc/apt/keyrings/rosk.gpg] $URL stable main" \\
  | sudo tee /etc/apt/sources.list.d/rosk.list
sudo apt update
sudo apt install rosk</pre>
<p>Updates then come with <code>sudo apt upgrade</code>.
Or download the latest package directly: <a href="$latest">$(basename "$latest")</a>.</p>
</html>
EOF
if [ ! -f index.html ]; then
    cp apt.html index.html
fi
echo "$REPO: $(ls "$POOL" | wc -l) package(s), signed by $KEY"
