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
2. Installed-app GUI smoke на обеих ОС и Windows compiled smoke по DESKTOP_BUILD.
   После `7f369c0` собрана macOS arm64 0.4.0; compiled smoke backend/ресурсов
   из подписанного `.app` проверил SQLite, браузер, protected session и shutdown.
   Это не GUI-поиск в пользовательском workspace. Сам queue runner по-прежнему
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

## MSA-07: runtime и read-only API, 11.10.2026

Ветка `codex/multisource-auctions`, MSA-07 остаётся IN_PROGRESS.
Ядро времени, SQLite 11 / Alembic 0020 и история раундов описаны в
[backlog](MULTISOURCE_IMPLEMENTATION_BACKLOG.md). Новый API читает сохранённые
аукционные записи без обращения к источникам или изменения статусов/истории.

- `GET /api/projects/{id}/auction-lots` требует текущий `computer_timezone`;
  optional `today_only=true` выбирает только подтверждённый локальный день.
- `GET /api/cars/{id}/auction` требует тот же пояс; optional `project_id`
  ограничивает доступ конкретным собственным проектом.
- Возвращаются clock/day bounds, identity, current round/latest schedule,
  original evidence, money semantics и имеющееся MAIN-фото. Авторизация,
  владельцы и граница сайта проверены; Encar parser/legacy payload не используются.
- UI добавляет свежий Intl-пояс только этим запросам. Refresh после
  focus/pageshow/visibilitychange и раз в минуту в видимом окне затрагивает
  только query keys с префиксом `['auction']`. Экраны подключаются позже.
- START/END/UNKNOWN, date-only и неизвестная метка источника не угадываются.
  Today-фильтр не означает готовность к Telegram-публикации. Прошедшее время
  само по себе не подтверждает LIVE/SOLD.

Verified RED/GREEN: 19 API-тестов (13 первоначальных сценариев плюс edge cases)
и 6 клиентских. Полная регрессия: **515 backend passed, 3 opt-in browser smoke
skipped**, три известных SAWarning converter reflection; **26 frontend passed**.
`tsc --noEmit` и desktop Vite build — passed; Vite сообщил chunk-size warning.
Native Intl Node smoke проверил Warsaw → Los Angeles в отдельном процессе,
без изменения настроек ОС. Проверка настоящего QueryClient подтвердила, что
аукционный refresh не инвалидирует кэш Encar.

Playwright CLI UI smoke не запускался: отсутствует npx, установка не выполнялась.
На завершение runtime/API-среза новый sidecar/приложение ещё не собирались.
Последующая локальная сборка описана ниже. Открытые gates: Windows compiled
tzdata при отключённой системной базе, повторяемый timezone-smoke в workflows,
реальная смена пояса/сон WebView на обеих ОС и production writer новых источников.
Fixture/Node API evidence не подтверждает эти gates; live источники и Telegram
не включены.

### Отдельная macOS-сборка, 11.10.2026

Локальная `0.4.0` arm64 из незакоммиченного worktree; существующее приложение
и пользовательская база не заменялись. Артефакт и evidence:
`work/macos-artifact/msa07-69groIcA/`.

- Nuitka onefile: fingerprint `21eaeb3c3d28bbac`, tzdata package/data включены.
- `.app`: ad-hoc codesign deep/strict — passed; версия в Info.plist — 0.4.0.
- Compiled smoke из ресурсов готового `.app`: SQLite 11, FK/WAL/busy timeout,
  непустой Chromium PNG, protected session, bootstrap SPA, shutdown — passed.
- Compiled auction API probe с пустым `PYTHONTZPATH` и удалённым внешним
  `PYTHONPATH`: четыре компьютерных пояса, today, START/END/UNKNOWN, исходный
  неподтверждённый CST и неизменность round/version/observation counts — passed.
  Использована отдельная fixture-база; сайты/лицензия не вызывались.
- Native source queue smoke: 16 passed, local fixtures only; max 1 listing
  browser, после каждого сценария 0 новых дочерних процессов. Это source-runtime
  evidence, отдельно от compiled API smoke.
- Frontend: 26 tests и tsc passed, Vite build passed (chunk-size warning).
  Все 24 установленные прямые зависимости совпали с manifest. Pnpm 11.19
  остановил script-run из-за workspace metadata и попытки install без TTY;
  повторная упаковка использовала установленный Tauri CLI и уже проверенный
  frontend, без переустановки и без изменения manifest. Updater artifacts
  отключены только локальным CLI override; релиз не публиковался.
- ZIP integrity — passed; SHA-256:
  `337164f2accb7d78600cfc7ad94a17f107e97ed5315289a2904868572866f02c`.

Compiled probe и отчёты сохранены рядом с артефактом (ignored work directory),
пока не интегрированы в постоянный native CI runner. Installed GUI, реальный
поиск в пользовательском workspace, смена пояса ОС/пробуждение и Windows
этой сборкой не проверены. Полный MSA-07 gate остаётся открытым.

## MSA-08: срез 1 — рабочая оболочка сайтов

11.10.2026, `codex/multisource-auctions`. Production frontend, не HTML-макет.
Текущая установленная macOS-версия не заменена; локальная упаковка не выполнялась.

- Общая SourceNavigation: площадки / аукционы, Encar/Copart/IAA, три подпункта
  выбранного сайта, настройки приложения отдельно. Новые SVG 18×18; CSS оболочки
  не заменяет карточки, фильтры, историю и формы Encar.
- `sourceRead` включает источник и в запрос, и в query key. Глобальная активность
  и список проектов общих настроек имеют отдельные keys `scans/all`, `projects/all`.
  Навигация сохраняет текущую область и поддерживает Back/URL, восстановление
  старых Encar-ссылок и безопасный fallback повреждённого localStorage.
- Раздел каждого сайта и выбор проектов изолированы в текущем сеансе; вид и
  сортировка Encar сохраняются в прежнем/отдельном Encar localStorage. Полное
  межсеансовое хранение всех разделов сайтов остаётся следующим срезом.
- Чужой source_key у деталей проекта/машины предлагает явный переход; машина
  другого сайта не отмечается просмотренной из Encar. Ошибка истории имеет
  отдельное сообщение/повтор вместо недостоверного пустого успешного журнала.
- Copart/IAA: честное «поиск ещё не подключён», заблокированное обновление,
  никаких demo-лотов, обращений к источникам или публикации. Полный read-only
  auction UI и реальная доступность/entitlements ещё не подключены к этой оболочке.

### Проверки

Baseline: 26 существующих frontend tests passed. Первая попытка React-test runner
не смогла загрузить TSX/extensionless JS: это исправление harness, не RED продукта.
После исправления baseline Encar-render passed, три проверки реальной оболочки/
восстановления получили ожидаемый RED (нет сайтов, игнорируется сохранённый
Copart/IAA URL). Затем GREEN. Дополнительные RED: сброс сортировки и показ ошибки
истории как пустого журнала; оба устранены без ослабления assertions.

- `node --test tests/*.test.mjs`: 36 passed, в том числе 10 новых React/API/route
  tests. App импортируется через test-only TypeScript loader; это не копия макета.
- `tsc --noEmit --incremental false`: passed.
- `vite build --config desktop/frontend/vite.config.ts`: passed, прежнее предупреждение
  о JS chunk >500 kB. Зависимости/manifest/версия не изменялись.
- `pytest backend/tests/test_multisource_acceptance.py -q`: 13 passed;
  source-scoped GET, mixed history, owner boundary, HTTP → queue → worker regression.
- In-app browser, actual desktop/dist + disposable API fixture server:
  Encar ↔ Copart ↔ IAA, восстановление текущего раздела/перезагрузка, Back,
  Tab/Enter, сохранение выбранного Encar-проекта и сортировки при переходах,
  русская/украинская навигация, общие настройки из IAA. Проверены 800/360 px;
  после исправления DOM scrollWidth == viewport width. Это fixture frontend,
  не пользовательский workspace, live source или WebView2/native app.
- UI request trace: переключения не делали POST cancel; отдельное явное
  подтверждение отмены в проекте отправило `{ "project_ids": [1] }` в тестовый API.
  Общая лицензия и пользовательские данные не использовались.

Fixture server/logs/screenshots: ignored `work/msa08-ui/`.
Состояние среза 1 выше сохранено как история; актуальное продолжение ниже.

## MSA-08: срез 2 — общий диалог запуска

11.10.2026, `codex/multisource-auctions`. Рабочая оболочка, не HTML-макет.
Установленное приложение не заменено, macOS packaging/commit/push/release не выполнялись.

- Общая кнопка «Обновить данные» доступна из любой рабочей области. Native
  `<dialog>` с группами площадок/аукционов, выбором одного проекта, доступных
  проектов сайта и всех доступных проектов. При открытии и выборе POST нет.
- `/api/scans/launch-options`: owner-only, read-only preview зарегистрированных
  модулей, capability SEARCH, загрузки адаптера, действующей локально проверенной
  лицензии и разрешённых сайтов, maintenance, URL и занятости проектов. Не
  обращается к listing-сайтам, не обновляет lease/trusted-time, не создаёт jobs.
  Это не live-проверка сети, CAPTCHA или доступа Copart/IAA.
- UI отправляет только подтверждённые `project_ids` в прежний POST. Выбор сайта/
  всех сайтов разворачивается в явный состав доступных проектов: новые проекты,
  созданные между выбором и подтверждением, не включаются автоматически. POST
  и worker повторно проверяют права; frontend не является защитной границей.
- Общая проверка занятости используется enqueue и preview. Различаются QUEUED,
  RUNNING, CANCEL_REQUESTED, завершённая часть ещё активной партии; отменённые
  части освобождаются по прежним правилам очереди. Занятые/недоступные исключаются
  из выбора, изменение доступности показывает предупреждение и новый состав.
- Ответ POST показывает QUEUED/ALREADY_ACTIVE по проектам с указанием сайта.
  Ошибка не выдаётся за успешный запуск или за занятость. Старые Encar actions,
  карточки/формы/история сохранены; локальное сообщение тоже учитывает ответ API.
- Закрытие/Escape возвращает фокус на общую кнопку; Tab/Shift+Tab цикличны внутри
  dialog. Фокус результата/ошибки переводится на сообщение. Кнопки не перекрывают
  прокручиваемый список; ошибка вне scroll-области, остаётся видимой.

TDD: baseline 36 frontend / 28 queue+HTTP tests passed. RED: общей кнопки нет;
статический preview URL принимается за scan_id; завершённый проект ошибочно
показан ожидающим; общий сбой ошибочно называется занятостью. GREEN без ослабления
исходных проверок. Browser RED фокуса/перекрытия устранён отдельно.

- Frontend: 42 tests; `tsc --noEmit --incremental false` passed.
- Backend: 86 tests в `test_scan_launch_plan`, `test_source_registry`,
  `test_desktop_license`, `test_multisource_queue`, `test_multisource_acceptance`.
  Права/owner boundary, поломка одного адаптера, неподключённый сайт, invalid URL,
  maintenance, истёкшая лицензия, busy/queue и unchanged storage прошли на
  изолированной SQLite/fake-адаптерах. Это не пользовательская база.
- Desktop frontend Vite build passed; сохраняется прежнее предупреждение chunk >500 kB.
  Зависимости, версия и схемы БД этим срезом не менялись.
- In-app browser, actual desktop/dist + disposable fixture API: RU/UK,
  1280/800/360 px, один/сайт/все доступные, подтверждение/результат, ALREADY_ACTIVE
  и HTTP 503, Escape/возврат фокуса/Shift+Tab, длинное имя. DOM scrollWidth равен
  ширине окна и dialog/content, горизонтального переполнения нет.
- Request trace: сайт → `{project_ids:[2,3]}`, fake-все сайты → `[2,3,4,5]`, один →
  `[3]`; busy `[1]` исключён. Fake Copart/IAA существовали только в отдельном
  сценарии проверки общего picker, не включены в production registry/UI data.

Fixture server/logs/screenshots: ignored `work/msa08-ui/`; packaging/native WebView2
и macOS installed GUI не проверены. MSA-08 остаётся IN_PROGRESS: общий обзор и
адресная отмена партии, availability на рабочих экранах, read-only auction UI,
межсеансовое хранение всех разделов, native platform gate.

### Encar regression: завершённый запуск ошибочно выглядел активным

11.10.2026: read-only диагностика локальной истории обнаружила завершённые
FAILED jobs с оставшимися QUEUED project rows. Проекция сайта принимала эти
строки за активную очередь, хотя отмена закономерно возвращала
`scan_already_finished`. Исправлена только сериализация: незавершённые части
терминального job наследуют его статус; при общей CANCEL_REQUESTED активные
части показывают отмену. Сохранённые строки и отчёты не изменяются, завершённые
части смешанной партии сохраняют собственные статусы.

TDD RED: 16 HTTP regression cases воспроизвели ложный active/скрытую отмену.
GREEN: 69 queue/launch/HTTP checks; полный backend — 537 passed, 3 skipped.
Проверены история/детали/progress, запрет отмены завершённого job, повторный
enqueue свободного проекта и неизменность исторических строк. Это fixture/API
evidence, не выполненная отмена пользовательского live-поиска и не Windows smoke.

## MSA-08: срез 3 — общая очередь и адресная отмена

11.10.2026, `codex/multisource-auctions`. Проверен рабочий desktop frontend,
не макет. Пользовательская база, действующие jobs, лицензия и установленное
приложение не изменялись; packaging/commit/push/release не выполнялись.

- Общий индикатор активности открывает native dialog из любого сайта. Jobs
  группируются по запуску, затем по сайтам; проекты показывают собственный
  статус. Общий прогресс не выдаётся за прогресс отдельных сайтов. Frozen plans
  имеют приоритет над текущим именем/сайтом проекта; старые неизвестные ID
  остаются видимыми без выдуманного сайта.
- Новый owner-only read-only GET `/api/scans/active` возвращает полную активную
  очередь в порядке создания, включая фактические ProjectScanRun statuses.
  Это отдельный запрос, не последние 100 записей журнала; terminal jobs не
  могут воскреснуть в общей очереди. Чтение не создаёт jobs, браузеры или записи.
- Подтверждение фиксирует три явные цели: проект (`project_ids`), сайт внутри
  одного запуска (`source_keys`), весь запуск (`all_projects`). Название текущей
  рабочей области не участвует в выборе цели. CARS отменяется только целиком.
  Завершённые/отменённые части и CANCEL_REQUESTED не предлагают новую отмену.
  Backend сохраняет прежние проверки owner/CSRF/состава партии и terminal state.
- До подтверждения POST нет. Busy guard блокирует повтор во время отправки;
  ошибка команды видима и не повторяется автоматически. Устаревшая цель
  отключает подтверждение, предлагает обновить очередь. Принятый запрос отмены
  не описывается как уже остановленный worker; сохранённые результаты остаются.
- Фокус переходит к заголовку подтверждения, затем к результату/ошибке;
  «Не отменять» возвращает его на исходную кнопку либо доступную кнопку dialog,
  если job уже исчез. Escape/закрытие возвращает фокус на индикатор. Tab/Shift+Tab
  остаются внутри dialog. Подтверждение и длинные названия прокручиваются, footer
  остаётся доступным. Существующая композиция Encar не заменена.

TDD: RED actual App render — у индикатора нет действия/dialog; RED API —
`active` разбирается как integer scan_id (422). Первая API-fixture нарушила
single-active-worker index вторым RUNNING job: выяснена причина, чужой job стал
QUEUED, затем проверен настоящий RED. После реализации GREEN. Browser RED:
Tab пропускал подтверждение; после исчезновения цели возврат фокуса попадал на
удалённый элемент. Исправлены и повторно проверены без ослабления гарантий.

Проверки финального кода:

- Полный backend: **538 passed, 3 skipped**; пропуски — opt-in queue/browser smoke.
  Три прежних SQLAlchemy reflection warnings для expression index в backup tests.
- Queue/launch/HTTP subset: **70 passed**. Новый API-case проверяет 106 активных
  jobs, owner boundary, исключение terminal/foreign rows, ProjectScanRun status
  и неизменность сохранённых payload/status после чтения.
- Frontend: **49 passed**, включая actual App header/API requests, явные scope
  bodies, frozen names/sites, состояния очереди, неизвестные проекты, CARS,
  loading/error/empty и RU/UK. TypeScript passed; Vite passed с прежним chunk >500 kB.
- In-app browser, actual `desktop/dist` + disposable fixture API: RU/UK,
  1280/800/360 px, явное подтверждение трёх областей, HTTP 503/видимое сообщение,
  refresh + отдельное повторное подтверждение, pending guard, завершение job
  между выбором и подтверждением, Tab/Shift+Tab/Escape/возврат фокуса. На проверенных
  размерах DOM/dialog не имеют горизонтального переполнения.
- Request trace: проект Copart → `/scans/16/cancel` `{project_ids:[4]}`,
  сайт → тот же job `{source_keys:["copart"]}`, CARS → `/scans/18/cancel`
  `{all_projects:true}`. После первой 503 новая команда появилась только после
  явного подтверждения; Enter во время pending дополнительного POST не создал.
  Stale confirmation для завершённого job не отправляло отмену.

Fixtures/logs/screenshot: ignored `work/msa08-ui/`, `activity-ru-1280.jpg`.
Тестовые Copart/IAA jobs не означают подключение live-адаптеров. Native macOS/
Windows GUI и пользовательская отмена не проверялись. MSA-08 остаётся
IN_PROGRESS: availability/entitlements на рабочих экранах, сохранённые аукционные
данные, постоянное хранение разделов каждого сайта и native platform gate.

## MSA-08: срез 4 — доступность сайтов и лицензия

11.10.2026, `codex/multisource-auctions`. Рабочий desktop frontend, не макет.
Пользовательские данные, действующая лицензия/jobs и установленное приложение
не изменялись; commit/push/packaging/release не выполнялись.

- Повторно используется read-only `/api/scans/launch-options`. Sidebar и
  настройки различают неподключённый модуль, ошибку его загрузки, ограничение
  лицензии, обслуживание, ожидание и ошибку чтения. Статус модуля/лицензии
  не выдаётся за проверенную доступность сайта в сети или отсутствие CAPTCHA.
- Локальный toolbar, обновление проекта и отдельной сохранённой машины Encar
  отключаются при запрете/неизвестном состоянии/ошибке чтения. Устаревший
  положительный cache при ошибке не разрешает новый поиск. При неверной ссылке
  проекта показано объяснение; весь сайт не становится недоступным. Backend
  по-прежнему повторяет проверки POST/worker — UI не является security boundary.
- Прежние проекты/история/машины Encar остаются для чтения, активный запуск
  можно отменить. Состояние одного источника не блокирует разрешённый другой.
  При доступном Encar прежняя композиция не получает постоянного баннера.
  Создание проекта не запускает автоматический первый поиск при запрете;
  проверяется источник сохранённого проекта, не текущая рабочая область.
- Query доступности активен вне launch dialog: интервал 30 секунд, 5 секунд
  внутри picker, refetch при фокусе окна. Активация/перенос/обновление лицензии,
  в том числе ошибка обновления, инвалидируют доступность. Нового механизма
  продления lease/trusted-time или обращений к источникам здесь нет.
- Copart/IAA остаются без live-адаптеров. Статическая заглушка не утверждает,
  что Encar разрешён; состояние аукционного модуля/лицензии показано отдельно
  от пока не подключённого интерфейса сохранённых лотов.

TDD: baseline 49 frontend passed; новые actual React-render проверки сначала
воспроизвели отсутствие причины и незаблокированные локальные действия. Отдельный
RED для неверной ссылки выявил отключённую кнопку без объяснения; затем GREEN.
Одна прежняя source-text проверка требовала буквального старого условия disabled:
причина установлена, гарантия пустого списка/активного запуска перенесена в
actual React-render, а не ослаблена ради результата.

Финальные проверки: **58 frontend passed**, **103 backend passed**
(launch/registry/license/queue/acceptance), TypeScript и Vite passed
(прежнее предупреждение bundle >500 kB). Browser на `desktop/dist` + disposable
fixture API: запрет лицензии → fake license refresh → немедленная разблокировка,
ошибка HTTP 503 поверх ранее разрешённого cache → отключённое обновление и alert,
повторная проверка, ошибка модуля, maintenance, просмотр сохранённой машины,
невыбираемые проекты общего picker, доступная отмена, Enter/Escape/фокус,
RU/UK и 1280/800/360 px. На проверенных узких окнах горизонтального переполнения
нет. После тестового license refresh в trace нет POST запуска/отмены поиска.
Два диагностических повтора backend сначала не дошли до tests: отсутствовал
PYTHONPATH, затем default storage указывал на `/storage`. Причины установлены;
повтор с PYTHONPATH и disposable SQLite/STORAGE_ROOT прошёл без правки tests.

Evidence: ignored `work/msa08-ui/`, `availability-ru-1280.jpg`. Это UI/API-fixture
приёмка, не live license/source integration и не native macOS/Windows smoke.
MSA-08 остаётся IN_PROGRESS: сохранённые аукционные данные, постоянное хранение
разделов каждого сайта и native platform gate. Следующий срез — read-only
просмотр сохранённых аукционных данных без запуска ещё не подключённых адаптеров.
