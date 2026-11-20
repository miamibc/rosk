from datetime import date

from rosk.core import quickparse as qp
from rosk.core import dates

TODAY = date(2026, 11, 20)  # Friday


def p(text, **kw):
    return qp.parse(text, today=TODAY, **kw)


def test_plain():
    r = p("Позвонить в банк")
    assert r.kind == qp.TASK and r.title == "Позвонить в банк" and r.due is None


def test_tomorrow_and_list():
    r = p("Позвонить в банк завтра #работа")
    assert r.title == "Позвонить в банк"
    assert r.due == date(2026, 11, 21)
    assert r.list_name == "работа"


def test_weekday_with_preposition():
    r = p("Встреча в пн")
    assert r.due == date(2026, 11, 23) and r.title == "Встреча"
    r = p("Buy milk on fri #home")
    assert r.due == date(2026, 11, 27) and r.title == "Buy milk" and r.list_name == "home"


def test_numeric_and_month_names():
    assert p("Отчёт 12.12").due == date(2026, 12, 12)
    assert p("Отчёт 5 января").due == date(2027, 1, 5)
    assert p("Report dec 3").due == date(2026, 12, 3)
    assert p("Report 1/2/2027").due == date(2027, 2, 1)


def test_relative():
    assert p("через 3 дня позвонить").due == date(2026, 11, 23)
    assert p("in 2 weeks call").due == date(2026, 12, 4)
    assert p("через неделю").due == date(2026, 11, 27)
    assert p("послезавтра").due == date(2026, 11, 22)


def test_note_prefix():
    r = p("n: идея для статьи завтра")
    assert r.kind == qp.NOTE and r.title == "идея для статьи завтра" and r.due is None


def test_subtasks():
    r = p("Поездка: - билеты - отель #отпуск")
    assert r.title == "Поездка" and r.subtasks == ["билеты", "отель"] and r.list_name == "отпуск"


def test_ignore_span():
    r = p("Купить 2 мая")
    assert r.due is not None
    r2 = p("Купить 2 мая", ignore={r.spans[0].start})
    assert r2.due is None and r2.title == "Купить 2 мая"


def test_word_inside_other_word_is_not_date():
    assert p("Пятничный отчёт").due is None
    assert p("Monitoring").due is None


def test_due_states():
    assert dates.due_state("2026-11-19", TODAY) == dates.OVERDUE
    assert dates.due_state("2026-11-20", TODAY) == dates.TODAY
    assert dates.due_state("2026-11-21", TODAY) == dates.TOMORROW
    assert dates.due_state("2026-11-23", TODAY) == dates.SOON
    assert dates.due_state("2026-12-23", TODAY) == dates.LATER


def test_chip_text():
    assert dates.chip_text("2026-11-19", TODAY) == "Yesterday"
    assert dates.chip_text("2026-11-25", TODAY) == "Wed"
    assert dates.chip_text("2026-12-12", TODAY) == "12 Dec"
    assert dates.chip_text("2027-01-05", TODAY) == "5 Jan 2027"


def test_tag_with_spaces_resolves_existing_list(tmp_path):
    from rosk.store.repo import Repo

    repo = Repo(tmp_path / "r.db")
    my = repo.create_collection("tasklist", "My Tasks")
    assert qp.tag_for("My Tasks") == "My_Tasks"
    iid = repo.quick_add("call #my_tasks")
    assert repo.item(iid)["collection_id"] == my
    iid = repo.quick_add("call #New_List")
    assert repo.store.collection(repo.item(iid)["collection_id"])["title"] == "New List"
