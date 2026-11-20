"""gettext setup. English strings in code, translations in po/."""

import gettext
import locale

from . import config

DOMAIN = "rosk"

try:
    locale.setlocale(locale.LC_ALL, "")
except locale.Error:
    pass

try:
    locale.bindtextdomain(DOMAIN, str(config.locale_dir()))
    locale.textdomain(DOMAIN)
except AttributeError:  # not available on every platform
    pass

_translation = gettext.translation(DOMAIN, localedir=str(config.locale_dir()), fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext


def C_(context: str, message: str) -> str:
    return _translation.pgettext(context, message)
