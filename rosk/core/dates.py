"""Deadline states and short, relative chip texts."""

from datetime import date, timedelta

from ..i18n import C_, _

OVERDUE = "overdue"
TODAY = "today"
TOMORROW = "tomorrow"
SOON = "soon"  # within 3 days
LATER = "later"

SOON_DAYS = 3


def parse_iso(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(value[:10])


def due_state(due, today=None):
    due = parse_iso(due)
    if due is None:
        return None
    today = today or date.today()
    delta = (due - today).days
    if delta < 0:
        return OVERDUE
    if delta == 0:
        return TODAY
    if delta == 1:
        return TOMORROW
    if delta <= SOON_DAYS:
        return SOON
    return LATER


def month_short(m):
    return [
        C_("month", "Jan"), C_("month", "Feb"), C_("month", "Mar"), C_("month", "Apr"),
        C_("month", "May"), C_("month", "Jun"), C_("month", "Jul"), C_("month", "Aug"),
        C_("month", "Sep"), C_("month", "Oct"), C_("month", "Nov"), C_("month", "Dec"),
    ][m - 1]


def weekday_short(wd):
    return [
        C_("weekday", "Mon"), C_("weekday", "Tue"), C_("weekday", "Wed"), C_("weekday", "Thu"),
        C_("weekday", "Fri"), C_("weekday", "Sat"), C_("weekday", "Sun"),
    ][wd]


def weekday_long(wd):
    return [
        _("Monday"), _("Tuesday"), _("Wednesday"), _("Thursday"),
        _("Friday"), _("Saturday"), _("Sunday"),
    ][wd]


def short_date(d, today=None):
    today = today or date.today()
    text = f"{d.day} {month_short(d.month)}"
    if d.year != today.year:
        text += f" {d.year}"
    return text


def chip_text(due, today=None):
    """Yesterday / Today / Tomorrow / Fri / 12 Dec."""
    due = parse_iso(due)
    if due is None:
        return ""
    today = today or date.today()
    delta = (due - today).days
    if delta == -1:
        return _("Yesterday")
    if delta == 0:
        return _("Today")
    if delta == 1:
        return _("Tomorrow")
    if 1 < delta < 7:
        return weekday_short(due.weekday())
    return short_date(due, today)


def day_heading(d, today=None):
    """Heading for the Upcoming view: Today, Tomorrow, Friday, 12 Dec."""
    today = today or date.today()
    delta = (d - today).days
    if delta == 0:
        return _("Today")
    if delta == 1:
        return _("Tomorrow")
    if 1 < delta < 7:
        return weekday_long(d.weekday())
    return f"{weekday_short(d.weekday())}, {short_date(d, today)}"


def to_google(d):
    d = parse_iso(d)
    return None if d is None else f"{d.isoformat()}T00:00:00.000Z"


def from_google(value):
    return value[:10] if value else None


def add_days(d, n):
    return parse_iso(d) + timedelta(days=n)
