# MSA-00: исходное состояние перед подключением источников

Дата проверки: 6 октября 2026 года, до 09:18 UTC.
Задача и статусы исполнения: [MSA-backlog](MULTISOURCE_IMPLEMENTATION_BACKLOG.md).
Статус: baseline зафиксирован; код приложения не изменён.

## Git и границы

- Ветка: `codex/multisource-auctions`.
- HEAD: `f8523ba46f9492b5bfc6746315e596f92ff3cf34`.
- Версия приложения в `package.json`: `0.4.0`.
- Проверялся текущий рабочий каталог, не чистый checkout HEAD.
- Исходный tracked diff: 5 файлов, 151 добавленная и 3 удалённые строки.
- SHA-256 вывода `git diff` до этой задачи:
  `bb90bbf7fcf1c0017e912c7e3c7f9bd3e4f8b43cda57cac1ada30159cfbd1d93`.

| Уже изменённый файл | Добавлено / удалено |
| --- | --- |
| `.github/workflows/windows-desktop.yml` | 6 / 0 |
| `docs/CURRENT_PRODUCT_PLAN.md` | 28 / 2 |
| `docs/DESKTOP_BUILD.md` | 3 / 1 |
| `docs/FUTURE_PRODUCT_PLAN.md` | 7 / 0 |
| `scripts/diagnose_windows_installation.ps1` | 107 / 0 |

Эти правки сохранены без редактирования. Уже существовали untracked:
восемь skills с UI metadata в `.agents/skills/`, три роли в `.codex/agents/`,
`AGENTS.md`, `BOOST.md`, а также документы `AUCTION_MONITORING_PLAN.md`,
`DESIGN_SYSTEM_PLAN.md`, `MULTISOURCE_ARCHITECTURE_PLAN.md`,
`MULTISOURCE_IMPLEMENTATION_BACKLOG.md`, `PRODUCT_SKILLSET.md`,
`PRODUCT_SKILLSET_EVALS.md`, `PRODUCT_SKILLSET_PLAN.md`,
`SKILL_AUTHORING_GUIDE.md`, `SOURCE_NAVIGATION_UX_PLAN.md` в `docs/`.
Наличие этих файлов не означает реализацию аукционов.

В MSA-00 добавлен этот отчёт и обновлён только статус в backlog.
Коммит, push, смена версии, запуск workflow и публикация не выполнялись.

## Окружение и изоляция

- macOS: Darwin `24.6.0`, `arm64`.
- Python: существующий `.desktop-build-venv/bin/python`, `3.12.14`;
  pytest `8.4.1`.
- Node.js: bundled runtime `v24.19.0`; pnpm `11.19.0`.
- В manifest указан pnpm `11.17.0`, в CI Node.js 22/Python 3.12.
  Локальный baseline не объявляется проверкой точного CI-окружения.
- В обычном PATH Node/pnpm отсутствовали; использованы готовые runtime-пути,
  без установки пакетов и постоянного изменения PATH.

Node runtime и pnpm найдены через workspace-dependencies:

```sh
export PATH=/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:/Users/sergeykaplun/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback:$PATH
```

Тестовые backend storage/SQLite созданы в отдельном `mktemp -d` каталоге
`/private/tmp/searchcar-msa00.gK4rnF`. Пользовательская база и рабочие данные
приложения не использовались. При повторе создать новый временный каталог,
не использовать сохранённый путь как постоянную тестовую БД.

## Команды и наблюдаемые результаты

Backend:

```sh
PYTHONPATH="$PWD/backend" \
STORAGE_ROOT=/private/tmp/searchcar-msa00.gK4rnF/storage \
DATABASE_URL=sqlite+pysqlite:////private/tmp/searchcar-msa00.gK4rnF/tests.sqlite3 \
.desktop-build-venv/bin/python -m pytest backend/tests -q
```

Результат: **213 passed, 1 warning**, 4.10 s, exit 0.
Предупреждение: в `test_sqlite_source_conversion_is_read_only_and_portable`
SQLAlchemy пропускает reflection expression-based index
`uq_scan_runs_single_active_worker` (`backend/app/postgres_converter.py:156`).
Тест проходит; это ограничение reflection, не падение миграции. Проверка
будущих миграций MSA-05 должна отдельно сохранять гарантии уникальности/очереди.

Frontend-команды после расследования pnpm:

```sh
export pnpm_config_verify_deps_before_run=false
pnpm build
pnpm test
pnpm typecheck
pnpm desktop:frontend:build
```

| Проверка | Результат |
| --- | --- |
| `pnpm build` | PASS, exit 0; свежий `dist/server/index.js` |
| `pnpm test` после build | PASS: 20 tests, 20 pass, 0 fail/skip, exit 0 |
| `pnpm typecheck` | PASS, exit 0, без diagnostics |
| `pnpm desktop:frontend:build` | PASS, exit 0; свежий `desktop/dist/` |
| `git diff --check` | PASS, exit 0 |

Обе frontend-сборки предупреждают о chunks >500 kB; это предупреждение
размера, не ошибка. vinext сообщает неизвестную статическую классификацию
маршрута `/`; HTML-тест отдельно получает HTTP 200 от свежего server build.
Проверки TypeScript и desktop build независимы от основной сборки; HTML-тест
запущен только после успешного `pnpm build`.

## Первоначальный сбой pnpm: причина до обхода

Обычные `pnpm build` и `pnpm typecheck` завершились до исполнения своих scripts:
pnpm попытался автоматически вызвать `install`, получил
`ERR_PNPM_META_FETCH_FAIL` и `ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY`.
Это не RED функциональности приложения и не failing assertion.

Диагностический запуск с `pnpm_config_verify_deps_before_run=error` показал:
`The workspace structure has changed since last install`.
Причина подтверждена по `runDepsStatusCheck`/`checkDepsStatus` установленного
pnpm и выборочно прочитанному `node_modules/.pnpm-workspace-state-v1.json`:
кеш проекта содержит версию **0.3.1**, текущий manifest — **0.4.0**.
pnpm сравнивает также версию workspace, не только зависимости.

Дополнительно проверено соответствие установленного набора зависимостей:

- importers, packages и snapshots установленного `node_modules/.pnpm/lock.yaml`
  совпадают с текущим `pnpm-lock.yaml`;
- specifiers всех dependencies/devDependencies manifest совпадают с lockfile;
- необходимые локальные bin scripts существуют.

Поэтому для baseline использован установленный набор зависимостей с
**временным** `pnpm_config_verify_deps_before_run=false`, без удаления
`node_modules`, обновления lockfile, установки зависимостей или изменения тестов.
Кеш pnpm не отредактирован вручную. Обычный запуск без override по-прежнему
требует обновления install-state; frozen install в точном CI-окружении отдельно
не проверялся. Это воспроизводимое ограничение окружения, не устранённое
изменением кода приложения.

## Ограничения доказательств и следующий этап

- Падающих assertions backend/frontend в текущем baseline нет.
- Функционального изменения нет: искусственный RED не добавлялся. Для будущего
  переноса Encar сначала нужны characterization-тесты, затем сравнение поведения.
- Frontend содержит также проверки исходного текста/regex. Их PASS не заменяет
  интерактивный UX-smoke; при переносе функций нельзя просто удалять assertions.
- Rust, установленное macOS-приложение, Windows, live Encar/Copart/IAA,
  реальные Telegram-публикации и миграция пользовательского backup не запускались.
  MSA-00 не подтверждает поддержку новых источников или работу Windows runtime.
- Не запускались независимые агенты; это применение skills на реальной задаче,
  не независимый A/B-eval всего skillset.

Следующая задача — **MSA-01**: доказать пригодный публичный канал Copart/IAA.
До этого адаптеры не пишем. MSA-02 требует отдельного выбора хранения,
а MSA-03 — согласованных макетов; сохранённый план дизайн-системы их не закрывает.
