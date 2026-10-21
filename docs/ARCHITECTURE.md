# Архитектура

Код разложен по слоям. Модуль импортирует только свой слой и слои ниже — это проверяет `tests/test_architecture.py`.

```
app/
  __init__.py        версия (__version__)
  paths.py           основание: где лежат «Данные», ресурсы, FFmpeg; импортируется отовсюду
  main.py            точка входа: защита от второго запуска, тема, главное окно

  core/              1. Предметная логика. Без Qt, сети и SQLite.
    models.py          VideoResult, VideoFile, LicenseInfo, SearchFilters — данные о роликах
    licenses.py        таблица лицензий → статус
    processes.py       справочник услуг (группы, варианты, уровни поиска)
    query.py           план поиска: фразы ru/en, перевод по словарю
    topic.py           слова полиграфии и чужих областей, основы слов
    similar.py         dHash превью, похожие дубли
    design.py          дизайн-токены: палитры, размеры, шрифты (общие для окна и экспорта)

  storage/           2. Данные на диске в «Данные». Без Qt.
    settings.py        настройки и ключи (settings.json, defaults.json)
    catalog.py         каталог SQLite: ролики, привязки, отметки, покрытие, история
    backup.py          резервная копия «Данных» в ZIP

  sources/           3. Площадки. Без Qt.
    base.py            интерфейс Source и ошибки площадок
    http.py            общая часть HTTP-площадок: кэш, лимиты, ошибки
    cache.py           SQLite-кэш ответов (24 ч) и учёт лимитов
    registry.py        какие площадки подключены (all_sources, make_sources) — по настройкам
    pixabay.py, coverr.py, vimeo.py, … — по модулю на площадку

  services/          4. Сценарии программы. Без Qt: синхронные функции и классы, отмена — threading.Event.
    search.py          Search: поиск по плану на всех площадках, отсев, ранжирование
    downloader.py      скачивание: файл лицензии, докачка, FFmpeg
    frames.py          кадры из ролика
    maintenance.py     проверка ссылок, хеши превью
    manual.py          ролик по ссылке пользователя
    updater.py         автообновление с GitHub

  export/            5. Выгрузки: Excel, CSV, HTML, M3U, сводка, отчёт PDF, шоурил.
                       Qt — только в report.py (PDF через QTextDocument и QPdfWriter).

  workers/           6. Фоновые потоки Qt. Тонкие обёртки: запускают сценарий из services/export
                       в QThread и передают результат сигналами. Логики здесь нет.
    search.py, batch.py, download.py, frames.py, maintenance.py, manual.py, backup.py,
    report.py, showreel.py, update.py

  ui/                7. Интерфейс PyQt6.
    main_window.py     главное окно: вкладки, меню, связь действий с потоками
    theme.py           стиль Qt из дизайн-токенов
    update.py          проверка и установка обновления из интерфейса
    tabs/              вкладки: пакетный поиск (batch_view), каталог (catalog_view)
    dialogs/           диалоги: настройки, справочник услуг, ссылка, шоурил, история и словарь, руководство
    widgets/           виджеты: плеер, карточки и фильтры выдачи, кадры, плавная прокрутка

resources/           встроенные данные: справочник, словарь, логотип, шрифты
docs/                документация; release-notes/ — примечания к выпускам, mockups/ — макеты
scripts/             служебные скрипты CI (release_version.py)
tests/               тесты в тех же слоях: core/, storage/, sources/, services/, export/, ui/,
                     release/ (выпуск), regression/ (сквозные проверки по итогам ревью);
                     fixtures/ — сохранённые ответы API, fakes.py — подставные сессии и часы
```

## Правила

- **Новый модуль — в папку своего слоя.** Прямо в `app/` лежат только `__init__.py`, `paths.py`, `main.py`.
- **Связи — только вниз:** core → storage → sources → services → export → workers → ui. Единственное исключение — `sources/registry.py`: он собирает площадки по настройкам из `storage/settings.py`.
- **Qt — только в `workers/` и `ui/`** (и для PDF в `export/report.py`). Логику проверяют обычные тесты без окна: например, `services.search.Search` сообщает о результатах обратными вызовами, а `workers.search.SearchWorker` превращает их в сигналы.
- **Фоновый поток — только в `workers/`.** Сначала пишется синхронная функция с параметром `cancel: threading.Event`, затем тонкий `QThread` вокруг неё. Окно при закрытии отменяет поток и дожидается его конца.
- **Площадка** — подкласс `sources.http.HttpSource`, подключается в `sources/registry.py`.
- **Цвета и размеры** — только в `core/design.py`.
- **Тесты** кладутся в папку слоя проверяемого модуля и работают без сети, на фикстурах.

## Что дальше

- Разбить `ui/main_window.py` (около 1100 строк): вкладка «Поиск» → `ui/tabs/search_tab.py`; экспорт, обслуживание каталога и скачивание → отдельные модули действий в `ui/`. Главное окно должно только собирать вкладки и меню.
