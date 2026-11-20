"""A single-page About dialog: everything that matters is visible at once."""

from pathlib import Path

from gi.repository import Adw, Gdk, Gtk

from .. import APP_ID, __version__
from ..i18n import _

WEBSITE = "https://github.com/miamibc/rosk"
LICENSE_URL = "https://www.gnu.org/licenses/gpl-3.0.html"
ICON_FILE = Path(__file__).resolve().parent.parent / "data" / "icons" / f"{APP_ID}.svg"


def _app_icon():
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    if theme.has_icon(APP_ID):
        image = Gtk.Image.new_from_icon_name(APP_ID)
    else:  # running from the source tree
        image = Gtk.Image.new_from_file(str(ICON_FILE))
    image.set_pixel_size(128)
    image.add_css_class("icon-dropshadow")
    return image


def _label(text, *css, markup=False):
    label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=40)
    label.set_markup(text) if markup else label.set_text(text)
    for c in css:
        label.add_css_class(c)
    return label


class AboutDialog(Adw.Dialog):
    def __init__(self):
        super().__init__(title=_("About Rosk"), content_width=380)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_title=False)
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=24,
                      margin_end=24, margin_bottom=24)
        box.append(_app_icon())
        title = _label("Rosk", "title-1")
        title.set_margin_top(12)
        box.append(title)
        box.append(_label(_("Version {}").format(__version__), "dim-label"))
        box.append(_label(_("A note you write down and cross out."), "body"))
        box.append(_label(_("Tasks sync with Google Tasks through GNOME Online Accounts, "
                            "notes with Google Keep."), "dim-label", "caption"))

        site = Gtk.LinkButton(uri=WEBSITE, label=_("Website"), halign=Gtk.Align.CENTER)
        site.add_css_class("pill")
        site.set_margin_top(12)
        site.set_margin_bottom(12)
        box.append(site)

        box.append(_label("© 2026 Sergei Miami", "caption"))
        box.append(_label(_("This program comes with absolutely no warranty. It is free software "
                            "under the <a href=\"{}\">GNU General Public License, version 3 or later</a>.")
                          .format(LICENSE_URL), "dim-label", "caption", markup=True))

        clamp = Adw.Clamp(maximum_size=360, child=box)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      propagate_natural_height=True)
        scroller.set_child(clamp)
        toolbar.set_content(scroller)
        self.set_child(toolbar)
