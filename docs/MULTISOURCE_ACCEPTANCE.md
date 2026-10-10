# MSA-06: приёмка общей очереди

Срез 7, 10.10.2026. Ветка `codex/multisource-auctions`.
Локальная приёмка выполнена; **полный platform gate ещё не закрыт**.

## Что проверено

- HTTP API → frozen launch → атомарный claim → настоящий worker → история:
  общий запуск трёх fake-сайтов, повтор без дублей, чужой владелец, изменение
  настроек после enqueue, CAPTCHA и отмена только одного сайта.
- Непустой Encar fixture: два объявления, повторный поиск, PRICE_DROP,
  исчезновение NEW после следующего поиска, отсутствие дублей машин/snapshots;
  запись другого сайта с тем же внешним ID не изменена.
- Чтение по `source_key`: проекты, избранное, история и progress. История
  отбирает 100 записей **после** фильтра, понимает legacy `project_ids`/`project_id`,
  CARS и frozen history удалённого проекта. Общая запись ScanRun не копируется
  и не меняется при чтении части сайта. Проекты/карточки/детали возвращают
  source_key; карточки/детали также source_listing_id, legacy encar_id сохранён.
- Настоящий bundled Chromium и отдельно подготовленный Node driver на macOS
  arm64: успех, CAPTCHA, отмена сайта; один listing browser одновременно,
  закрытие browser/context, отсутствие переноса cookies, локали en-US/ko-KR/uk-UA,
  освобождение FIFO и отсутствие новых дочерних процессов после завершения.
  Все обращения браузера перехвачены локальным fixture HTML.

TDD для обнаруженного пробела GET API: RED показал игнорирование source_key,
чужие части общей истории и применение общего лимита до фильтра. Отдельный
RED выявил потерю legacy singular project_id и примесь чужого сайта в legacy
error text. Имена всех частей используются для разделения; одинаковые/переименованные
имена дают явное details_unavailable вместо недостоверной общей строки.
После исправления GREEN.
Изначальное падение нового snapshot-теста расследовано: snapshot создаётся
при изменении, поэтому неизменённое наблюдение не добавляет четвёртую запись.
Это ошибка ожидания теста, а не исправление приложения. Прочие сквозные тесты —
characterization существующего поведения, не искусственный RED.

Полная backend-регрессия: **413 passed, 3 skipped** (opt-in Chromium smoke),
два прежних SAWarning converter reflection. Отдельный нативный runner:
**16 passed** (13 API/worker checks + 3 Chromium smoke). Frontend Node:
**20 passed**; TypeScript `tsc --noEmit` — passed. Workflow YAML — parsed;
`git diff --check` — passed.

На финальном проверенном macOS запуске sampled process-tree RSS: примерно 453–459 MB,
не более шести процессов дерева теста, 7/5/5 открытий listing browser для
успеха/CAPTCHA/отмены, 0 новых дочерних процессов после завершения.
Это RSS Python + driver + Chromium в точках измерения, **не** строгий пик,
не размер приложения и не бюджет реального большого поиска с detail readers.
Отчёт содержит платформу, архитектуру и `installed_app: false`.

## Контракт чтения истории

Без source_key ответы и глобальные status/progress остаются прежними.
`GET /api/scans?source_key=…`, `GET /api/scans/{id}?source_key=…` и `/progress`
возвращают только часть сайта: планы/ID, pagination/detail_progress, report,
failures, summary и версии адаптера. Для отсутствующей части detail возвращает
404, список — пустой массив. Сохранённые данные доступны и без активного адаптера
или права на новый поиск.

Status отражает дочерние ProjectScanRun выбранного сайта; overall_status и
overall_progress сохраняют состояние общей партии. Progress scoped project
history — доля завершённых проектов (`progress_kind: PROJECT_COMPLETION`),
а не точный процент чтения текущей страницы. Текущие страницы/машины видны в
pagination/detail_progress. Для legacy/CARS без дочерних частей используется
доступное состояние общего job (`progress_kind: JOB`). В UI MSA-08 учитывать это различие.

## Повторяемый native smoke

Нужны Python build environment и уже подготовленные ресурсы по
[DESKTOP_BUILD.md](DESKTOP_BUILD.md). Runner не скачивает их и не собирает приложение.

macOS:

```bash
.desktop-build-venv/bin/python scripts/multisource_acceptance_smoke.py \
  --report-dir work/queue-smoke
```

Windows, PowerShell:

```powershell
& .desktop-build-venv/Scripts/python.exe scripts/multisource_acceptance_smoke.py `
  --report-dir work/queue-smoke
if ($LASTEXITCODE -ne 0) { throw "Queue acceptance failed" }
```

Runner создаёт временные default SQLite/storage и отдельные pytest databases,
проверяет manifest ресурсов, пишет acceptance.xml и три platform JSON.
Сам smoke не делает запросов к сайтам/лицензии, не меняет установленное
приложение и пользовательскую БД. Resource sampler читает PID/PPID/RSS,
не командные строки и не секреты. В sandbox доступ к процессам/browser может
требовать разрешения; отказ не считается успешной проверкой.

Оба существующих native build workflow вызывают runner после подготовки
ресурсов и **до** компиляции. Отдельный artifact с отчётами выгружается и при
падении последующих шагов. Изменение workflow не означает его удалённый запуск.

## Оставшиеся gates

1. Получить Windows x64 native JSON/XML с этой же ветки. Здесь Windows не запускался.
2. Installed-app/compiled smoke на обеих ОС по DESKTOP_BUILD; этот runner
   проверяет source Python с native resources, не упакованный sidecar/установку.
3. Реальные новые источники — только после разрешённого канала MSA-01.
   Реестр по-прежнему поставляет только Encar. Fake auction sources сейчас
   проверяют координацию/пустую выдачу, **не** запись непустого аукционного
   результата: Encar storage намеренно отказывает другим источникам. Общая
   схема уже допускает их; путь записи и нормализация проверяются при MSA-07/09/10.
4. Rollout source entitlements license-service отдельно: migration 0006 →
   разрешённый deploy → smoke, как описано в LICENSE_SERVICE. Здесь не выполнен.

MSA-06 не переводить целиком в DONE по локальному fixture/macOS результату.
Следующая техническая тема после приёмки — MSA-07 (аукционные данные/время);
недостающие platform/live evidence не выдавать за её реализованный результат.
