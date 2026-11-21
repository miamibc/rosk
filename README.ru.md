# Rosk

[English](README.md) · **Русский**

Rosk — это заметка, которую написал и вычеркнул. Менеджер задач и заметок для рабочего стола
GNOME, главное в нём — скорость и краткость: записать мысль — пара секунд, вычеркнуть — ещё
быстрее.

> Статус: первая рабочая версия (0.1). Задачи синхронизируются с Google Tasks; для Keep нужно один раз войти в настройках.

![Rosk: задачи на сегодня](docs/screenshots/today.png?v=2)

## Что умеет

- **Быстрый ввод из верхней панели.** Rosk добавляет значок в панель GNOME. Клик или горячая
  клавиша, одна строка вроде `Позвонить в банк завтра #работа`, Enter — готово.
- **Задачи и заметки Google в одном месте.** Задачи синхронизируются с Google Tasks, заметки —
  с Google Keep. В Rosk они рядом, но в Google не смешиваются: задачи остаются в Tasks, заметки —
  в Keep.
- **Работает без сети.** Все изменения сохраняются локально сразу и уходят в Google, когда связь
  появится.
- **Всё на виду.** Все списки в одном окне, подзадачи и сроки. То, что пора делать сегодня или уже
  просрочено, подсвечивается.
- **Минимализм и родной вид GNOME.** GTK 4 и libadwaita, светлая и тёмная тема, полное управление
  с клавиатуры.

## Скриншоты

<table>
<tr>
<td width="50%" align="center" valign="top">
<img src="docs/screenshots/quick-syntax.png?v=2" alt="Синтаксис быстрого ввода"><br>
<em>Дата, список и подзадачи распознаются на лету</em>
</td>
<td width="50%" align="center" valign="top">
<img src="docs/screenshots/upcoming-light.png?v=2" alt="Предстоящие, светлая тема"><br>
<em>Предстоящие задачи по дням, светлая тема</em>
</td>
</tr>
<tr>
<td width="50%" align="center" valign="top">
<img src="docs/screenshots/notes.png?v=3" alt="Заметки"><br>
<em>Заметки Google Keep: закреплённые и цветные</em>
</td>
<td width="50%" align="center" valign="top">
<img src="docs/screenshots/notes-light.png?v=3" alt="Заметки, светлая тема"><br>
<em>Заметки в светлой теме</em>
</td>
</tr>
<tr>
<td width="50%" align="center" valign="top">
<img src="docs/screenshots/list-editor.png?v=2" alt="Список задач и редактор"><br>
<em>Списки, подзадачи и панель редактирования</em>
</td>
<td width="50%" align="center" valign="top">
<img src="docs/screenshots/note-editor.png?v=3" alt="Редактирование заметки-списка"><br>
<em>Списки с флажками в своём порядке, пункты перетаскиваются</em>
</td>
</tr>
<tr>
<td width="50%" align="center" valign="top">
<img src="docs/screenshots/quick-add.png?v=2" alt="Быстрый ввод"><br>
<em>Быстрый ввод: задача или заметка, после <code>#</code> — подсказки списков</em>
</td>
<td width="50%" align="center" valign="top">
<img src="docs/screenshots/about.png?v=2" alt="О программе"><br>
<em>О программе</em>
</td>
</tr>
</table>

## Требования

GNOME 48 или новее, аккаунт Google в «Настройки → Сетевые учётные записи» и пакеты (в Ubuntu
26.04 уже стоят):

```sh
sudo apt install python3-gi python3-venv gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-goa-1.0 gir1.2-secret-1
# для окна входа в Google Keep:
sudo apt install gir1.2-webkit-6.0
```

## Установка

Через apt (Ubuntu 25.04+, Debian 13+), обновления потом приходят с `apt upgrade`:

```sh
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://miamibc.github.io/rosk/rosk-archive-keyring.gpg | sudo tee /etc/apt/keyrings/rosk.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/rosk.gpg] https://miamibc.github.io/rosk stable main" \
  | sudo tee /etc/apt/sources.list.d/rosk.list
sudo apt update && sudo apt install rosk
```

Или скачайте `rosk_<версия>_all.deb` со страницы [Releases](https://github.com/miamibc/rosk/releases)
и установите командой `sudo apt install ./rosk_<версия>_all.deb`.

Или без root, из клона репозитория или из архива `rosk-<версия>.tar.gz` со страницы
[Releases](https://github.com/miamibc/rosk/releases) (сначала распакуйте):

```sh
make install      # в ~/.local, root не нужен
make uninstall    # удаляет приложение, данные остаются в ~/.local/share/rosk
make status       # работает ли, что на сегодня, активно ли расширение панели
```

После установки Rosk работает в фоне, его значок появляется в верхней панели, а сам он
запускается вместе с сеансом. На Wayland GNOME загружает новые расширения Shell только при
входе, поэтому до перезахода в сеанс Rosk показывает компактный значок через AppIndicator.

## Как пользоваться

- **Super+Alt+R** или клик по значку в панели: одна строка, Enter. Shift+Enter оставляет поле
  открытым для следующей задачи.
- Синтаксис: `завтра`, `в пт`, `12.12`, `через 3 дня` (английский тоже: `tomorrow`, `fri`),
  `#список`, `n:` в начале — заметка, `Поездка: - билеты - отель` — подзадачи или пункты списка.
- Поиск задач и заметок прямо из обзора GNOME.
- Google Keep: Настройки → Google Keep → Войти через Google. Для личных аккаунтов у Google нет публичного API Keep,
  поэтому Rosk использует неофициальную библиотеку [gkeepapi](https://github.com/kiwiz/gkeepapi).

## Команды make

Полный список: `make`.

| Команда | Что делает |
|---|---|
| **Установка и служба** | |
| `make install` | Установка в `~/.local`: приложение, фоновая служба, расширение панели, поиск, автозапуск. Повторный запуск обновляет |
| `make uninstall` | Удаляет установку; данные в `~/.local/share/rosk` остаются |
| `make status` | Работает ли служба, что на сегодня, активно ли расширение панели |
| `make sync` | Синхронизировать с Google сейчас |
| `make restart` / `start` / `quit` | Перезапустить, запустить или остановить установленную службу |
| `make logs` | Последние строки журнала службы и расширения |
| **Разработка** | |
| `make venv` | Создать `.venv` поверх системного PyGObject, с gkeepapi, pytest и ruff |
| `make run` | Запустить из исходников рядом с установленным Rosk, со своими данными в `build/dev-data` (синхронизируется с Google) |
| `make run-offline` | То же без синхронизации и значка в панели — чтобы спокойно пробовать |
| `make test` | Тесты |
| `make lint` | Проверка Python-кода через ruff |
| `make check` | Всё перед коммитом: линтер, тесты, синтаксис расширения, схема настроек, переводы |
| `make pot` | Обновить `po/rosk.pot` по коду и влить в переводы |
| `make locale` | Скомпилировать переводы |
| `make screenshots` | Пересоздать скриншоты README |
| **Выпуск** | |
| `make dist` | Архив исходников `dist/rosk-<версия>.tar.gz` |
| `make clean` | Удалить `build/`, `dist/` и кэши (не ваши данные) |

## Лицензия

Rosk — свободная программа под лицензией [GNU GPL версии 3 или новее](LICENSE).

## Документация

- [Спецификация](docs/SPEC.md)
- [Архитектура](docs/ARCHITECTURE.md)
- [Возможности и ограничения API Google](docs/GOOGLE_APIS.md)
- [Решения](docs/DECISIONS.md)
- [План разработки](docs/ROADMAP.md)
