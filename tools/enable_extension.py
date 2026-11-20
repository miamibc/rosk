"""Adds the extension to org.gnome.shell enabled-extensions (takes effect at the next login
on Wayland) and tries to enable it right away (works when the Shell already knows it)."""

import ast
import subprocess
import sys

uuid = sys.argv[1]
out = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
                     capture_output=True, text=True).stdout.strip()
current = ast.literal_eval(out.replace("@as ", "") or "[]")
if uuid not in current:
    current.append(uuid)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(current)])
subprocess.run(["gnome-extensions", "enable", uuid], capture_output=True)
