"""One-line quick capture syntax (Russian and English).

    Позвонить в банк завтра #работа
    Buy milk on fri #home
    n: идея для статьи
    Поездка: - билеты - отель #отпуск

Returns a ParseResult with the cleaned title, the recognized parts and their
character spans, so a UI can highlight them as chips.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

TASK = "task"
NOTE = "note"

_MONTHS = {
    # Russian: genitive and nominative stems
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7,
    "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_WEEKDAYS = {
    "пн": 0, "понедельник": 0, "вт": 1, "вторник": 1, "ср": 2, "среда": 2, "среду": 2,
    "чт": 3, "четверг": 3, "пт": 4, "пятница": 4, "пятницу": 4, "сб": 5, "суббота": 5,
    "субботу": 5, "вс": 6, "воскресенье": 6,
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}

_RELATIVE = {
    "сегодня": 0, "today": 0, "tod": 0,
    "завтра": 1, "tomorrow": 1, "tmr": 1,
    "послезавтра": 2,
    "вчера": -1, "yesterday": -1,
}

_PREP = r"(?:(?:в|во|на|к|до|on|by|next)\s+)?"
_B = r"(?<![\w#])"  # word start
_E = r"(?![\w])"   # word end

_month_names = "|".join(sorted(
    [r"янв\w*", r"фев\w*", r"мар\w*", r"апр\w*", r"ма[яй]", r"июн\w*", r"июл\w*", r"авг\w*",
     r"сен\w*", r"окт\w*", r"ноя\w*", r"дек\w*",
     r"jan\w*", r"feb\w*", r"mar\w*", r"apr\w*", r"may", r"jun\w*", r"jul\w*", r"aug\w*",
     r"sep\w*", r"oct\w*", r"nov\w*", r"dec\w*"], key=len, reverse=True))
_weekday_names = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_relative_names = "|".join(sorted(_RELATIVE, key=len, reverse=True))

_DATE_PATTERNS = [
    ("rel", re.compile(_B + r"(?:day after tomorrow|" + _relative_names + r")" + _E, re.I)),
    ("in", re.compile(_B + r"(?:через|in)\s+(?:(\d+)\s+)?(дн\w*|день|недел\w*|месяц\w*|days?|weeks?|months?)" + _E, re.I)),
    ("wd", re.compile(_B + _PREP + r"(" + _weekday_names + r")" + _E, re.I)),
    ("dmy", re.compile(_B + r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?" + _E)),
    ("dmon", re.compile(_B + r"(\d{1,2})\s+(" + _month_names + r")(?:\s+(\d{4}))?" + _E, re.I)),
    ("mond", re.compile(_B + r"(" + _month_names + r")\s+(\d{1,2})(?:,?\s+(\d{4}))?" + _E, re.I)),
]

_NOTE_PREFIX = re.compile(r"^\s*(?:n|н|note|заметка)\s*:\s*", re.I)
_LIST = re.compile(r"(?<![\w#])#([\w][\w\-./]*)", re.U)


def tag_for(name):
    """How a list or label name is written after '#': spaces become underscores."""
    return re.sub(r"\s+", "_", name.strip())


def tag_key(text):
    """Normalized form for matching '#My_Tasks' / '#my tasks' / 'My Tasks'."""
    return re.sub(r"[\s_]+", "", text).lower()


@dataclass
class Span:
    start: int
    end: int
    kind: str  # "date", "list", "note", "subtasks"
    value: object = None


@dataclass
class ParseResult:
    kind: str = TASK
    title: str = ""
    list_name: str = None
    due: date = None
    subtasks: list = field(default_factory=list)
    spans: list = field(default_factory=list)


def _month_from(name):
    return _MONTHS.get(name[:3].lower())


def _safe_date(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _future_year(today, m, d):
    candidate = _safe_date(today.year, m, d)
    if candidate and candidate < today - timedelta(days=30):
        candidate = _safe_date(today.year + 1, m, d)
    return candidate


def _date_from_match(kind, m, today):
    if kind == "rel":
        word = m.group(0).lower()
        if word == "day after tomorrow":
            return today + timedelta(days=2)
        return today + timedelta(days=_RELATIVE[word])
    if kind == "in":
        n = int(m.group(1) or 1)
        unit = m.group(2).lower()
        if unit.startswith(("нед", "week")):
            return today + timedelta(weeks=n)
        if unit.startswith(("мес", "month")):
            month = today.month - 1 + n
            year = today.year + month // 12
            month = month % 12 + 1
            day = min(today.day, 28)
            return date(year, month, day)
        return today + timedelta(days=n)
    if kind == "wd":
        wd = _WEEKDAYS[m.group(1).lower()]
        ahead = (wd - today.weekday()) % 7 or 7
        if m.group(0).lower().startswith("next "):
            ahead = ahead if ahead > 0 else 7
        return today + timedelta(days=ahead)
    if kind == "dmy":
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if y:
            y = int(y)
            if y < 100:
                y += 2000
            return _safe_date(y, mo, d)
        return _future_year(today, mo, d)
    if kind == "dmon":
        d, mo, y = int(m.group(1)), _month_from(m.group(2)), m.group(3)
        if not mo:
            return None
        return _safe_date(int(y), mo, d) if y else _future_year(today, mo, d)
    if kind == "mond":
        mo, d, y = _month_from(m.group(1)), int(m.group(2)), m.group(3)
        if not mo:
            return None
        return _safe_date(int(y), mo, d) if y else _future_year(today, mo, d)
    return None


def parse(text, today=None, ignore=(), default_kind=TASK):
    """Parses a quick-capture line. `ignore` holds start offsets of spans the user
    rejected (a click on a chip) — those stay part of the title. `default_kind` is
    what the line becomes without an explicit "n:" prefix."""
    today = today or date.today()
    result = ParseResult(kind=default_kind)
    taken = []  # (start, end)

    def free(s, e):
        return all(e <= a or s >= b for a, b in taken)

    m = _NOTE_PREFIX.match(text)
    if m and m.start() not in ignore:
        result.kind = NOTE
        result.spans.append(Span(m.start(), m.end(), "note", NOTE))
        taken.append((m.start(), m.end()))

    # Sub-items: "Title: - one - two"
    body_start = taken[0][1] if taken else 0
    colon = text.find(":", body_start)
    if colon != -1 and re.match(r"\s*-\s+\S", text[colon + 1:]) and colon not in ignore:
        rest = text[colon + 1:]
        parts = [p.strip() for p in re.split(r"(?:^|\s)-\s+", rest) if p.strip()]
        # Remove list tags from sub-items; they apply to the whole entry.
        clean = []
        for p in parts:
            lm = _LIST.search(p)
            if lm and result.list_name is None:
                result.list_name = lm.group(1)
            clean.append(_LIST.sub("", p).strip())
        result.subtasks = [c for c in clean if c]
        result.spans.append(Span(colon, len(text), "subtasks", result.subtasks))
        taken.append((colon, len(text)))

    for lm in _LIST.finditer(text):
        if lm.start() in ignore or not free(lm.start(), lm.end()):
            continue
        if result.list_name is None:
            result.list_name = lm.group(1)
        result.spans.append(Span(lm.start(), lm.end(), "list", lm.group(1)))
        taken.append((lm.start(), lm.end()))
        break

    if result.kind == TASK:
        found = []
        for kind, rx in _DATE_PATTERNS:
            for dm in rx.finditer(text):
                if dm.start() in ignore or not free(dm.start(), dm.end()):
                    continue
                value = _date_from_match(kind, dm, today)
                if value:
                    found.append((dm.start(), dm.end(), value))
        if found:
            # Prefer the last date mentioned ("встреча в пн, перенести на ср" -> ср).
            s, e, value = max(found, key=lambda f: f[0])
            result.due = value
            result.spans.append(Span(s, e, "date", value.isoformat()))
            taken.append((s, e))

    title = text
    for s, e in sorted(taken, reverse=True):
        title = title[:s] + " " + title[e:]
    title = re.sub(r"\s+", " ", title).strip(" ,;")
    result.title = title
    result.spans.sort(key=lambda sp: sp.start)
    return result
