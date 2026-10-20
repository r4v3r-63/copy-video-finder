# Инструкции для агентов (Codex и др.)

Работа над проектом продолжается после Claude Code. Прочитать по порядку:

1. **`CLAUDE.md`** — полные правила проекта: архитектура, площадки, лицензии, жёсткие правила, решения пользователя. Всё, что там написано, действует и здесь.
2. **`docs/STATUS.md`** — ТЗ по пунктам: что готово, что нет, что в работе, ближайшие шаги.
3. `docs/vk.md` — вход в VK ID (для пользователя).

Полное ТЗ: https://claude.ai/code/artifact/01bf5451-bb56-4330-8ba7-38faf84738ca (выдержки — в `docs/STATUS.md`).

## Коротко о проекте

- Портативная программа для Windows (PyQt6), репозиторий `r4v3r-63/copy-video-finder`, ветка `main`, текущая версия кода — `app/__init__.py` (`__version__`, сейчас 0.24.0).
- Пользователь один, общается по-русски. Интерфейс, сообщения, комментарии в коде, коммиты — на русском.
- Пользователю нужны только бесплатные варианты (поисковики, API).

## Команды

```
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen pytest -q                  # на Linux без экрана; сейчас 458 passed, 1 skipped
ruff check app tests
python -m app.main                                   # запуск
```

Перед каждым коммитом — `ruff check app tests` и `pytest`. Workflow «Проверка кода» (`check.yml`) гоняет их на каждый пуш; сборка — на windows-latest, поэтому пути в тестах и коде должны работать и с `\`.

## Выпуск версии

1. Поднять `__version__` в `app/__init__.py`, коммит, пуш в `main`.
2. Дождаться зелёного `check.yml`.
3. `gh api -X POST repos/r4v3r-63/copy-video-finder/actions/workflows/build.yml/dispatches -f ref=main -f "inputs[version]=X.Y.Z"` (или Actions → «Сборка для Windows» → Run workflow).
4. Workflow сам создаст тег `vX.Y.Z` и выпуск с `CopyVideoFinder-vX.Y.Z-windows.zip` и `.sha256` — имена не менять (на них завязано автообновление). Неудачная сборка тег не создаёт — ту же версию можно запустить снова.
5. Старые выпуски — workflow `cleanup-releases.yml` вручную (`keep`, `dry_run=false`).

## Секреты — строго

- API-ключи не хранить в коде и не коммитить. Встроенные ключи — только секреты репозитория (`PIXABAY_API_KEY`, `VIMEO_TOKEN`, `COVERR_API_KEY`, `PEXELS_API_KEY`, `EUROPEANA_API_KEY`, `TAVILY_API_KEY`, `BRAVE_API_KEY`); сборка пишет их в `defaults.json`. Тест `test_no_api_keys_in_repository`.
- Токены VK, ключи пользователя — только в «Данные/settings.json» (в `.gitignore`). Пароли пользователя программа не принимает и не хранит.
- В VK ID приложения пользователя (ID 54837826) нельзя записывать в репозиторий защищённый и сервисный ключи — они не нужны (вход PKCE).

## Важные места кода

- Справочник услуг — `resources/processes.json`, версия 5: 20 разделов (`Category`), 88 групп (`Process`), 376 услуг сайта (`variants`, эталон — `tests/fixtures/site_services.txt`, каждая ровно в одной группе). Файл нормализован через `processes.save_catalog` — править в том же формате. Старые коды → новые: `"migrated"` в JSON, перенос привязок — `Catalog._migrate_links` (метка `meta.directory_version`).
- Новые фразы поиска проверять тестом `test_level_phrases_pass_own_topic_filter` (фраза одним широким словом тему не подтверждает).
- Площадки — `app/sources/`, общий HTTP-слой с кэшем и лимитами — `sources/http.py`. Wikimedia Commons исключена из поиска (решение пользователя), модуль остаётся.
- Фоновые потоки при закрытии окна отменяются и дожидаются (`closeEvent`); FFmpeg — через Popen с опросом отмены.
- Цвета/размеры — только `app/design.py` (тест `test_no_colors_outside_tokens`).
- Добавление по ссылке — `app/manual.py`, `ui/add_link_dialog.py`, кнопка в каталоге. Повторная ссылка открывает прежнюю запись без изменения пользовательских отметок; без ключа известной площадки сохраняется только ссылка, без утверждения о свободной лицензии.
- Ревью 0.24.0 и внешние ограничения — `docs/CODE_REVIEW.md`; подготовленные примечания выпуска — `docs/RELEASE_NOTES_0.24.0.md`. Не путать подготовленную версию с опубликованным выпуском.
- Тесты окон: одно `QApplication` из `tests/conftest.py`; не вызывать `processEvents`/`show` без нужды — роняло полный прогон.

## Работа

- Коммит после каждого значимого шага, понятное сообщение на русском, пуш.
- Решения пользователя фиксировать в `CLAUDE.md` с датой; после изменений обновлять `docs/STATUS.md`.
- README — только общее описание программы, без подробностей работы и источников роликов.
