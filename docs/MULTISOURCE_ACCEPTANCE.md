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

## MSA-08: срез 5 — чтение сохранённых аукционов

11.10.2026, `codex/multisource-auctions`. Перед началом среза по запросу пользователя
создан commit `3aa457b` с предыдущими MSA-07/08 изменениями; push не запрошен.
Изменения этого среза пока не закоммичены. Пользовательская БД, лицензия, jobs,
live-источники и установленное приложение не изменялись; macOS-пакет не собирался.

- Copart/IAA читают сохранённые проекты/историю через прежние source-scoped
  запросы; проекты/одна машина/«Сегодня» — через read-only auction API MSA-07.
  Состояние доступности поиска показано отдельно: сохранённые данные доступны
  без подключённого адаптера, но сбор и Telegram не имитируются.
- Одна `AuctionCard` для двух аукционов, отдельная от Encar. Акцент на одометр,
  его достоверность, повреждения, состояние, START/END и локальное расписание.
  Нет нижней панели прежнего макета. Используется существующий MAIN image URL;
  отсутствие фото не запускает получение/скриншот. Неизвестные значения остаются
  оригинальными, React экранирует их; исходные сохранённые детали доступны у машины.
- «Сегодня» берётся из серверной проекции по свежему IANA-поясу компьютера,
  не из сравнения строки даты в браузере. DATE_ONLY/неясный локальный день не
  получают точное время или countdown; исходная дата/зона не теряются. Прошедшее
  расписание не становится LIVE/проданным: текущий этап помечен неподтверждённым.
  LIVE/CLOSED/CANCELLED показываются только из сохранённого явного этапа источника.
- День/месяц компактны, текущий год опущен; у другого года нет одинокого «г.».
  Цены сохраняют тип и валюту: USD cents/KRW won, ноль не равен неизвестному,
  оценка не ставка, ставка чужого аукционного раунда не показывается как текущая.
  Ставка названа снимком, последнее наблюдение источника видно отдельно.
- «Объявление» принимает сохранённые HTTPS detail URLs Copart/IAA, не search URL,
  с проверкой источника/формата, а у Copart — lot ID. Используется прежний desktop
  external opener, не автоматическое открытие сайта. Просмотр машины не делает
  PATCH viewed; error/retry и stale cache отличимы от успешной пустой выборки.

TDD: baseline **58 frontend passed** перед commit. Пять новых actual App-render
tests сначала подтвердили, что заглушка скрывала сохранённые проекты/машины.
Дополнительные component RED выявили недостоверный перевод NOT_ACTUAL miles
в километры и деление KRW на 100; причины проверены по money contract. Fixture
с неверными money enums исправлен после сверки контракта, а не ради GREEN.
Отдельные RED — потеря исходной DATE_ONLY даты и отсутствие desktop opener.
После исправлений **70 frontend passed**, TypeScript и desktop Vite passed
(прежний warning bundle >500 kB). **99 backend passed**: auction API/time и
multisource acceptance, disposable SQLite/STORAGE_ROOT. Backend-код не менялся.
ESLint нового компонента: 0 errors, 1 Next `<img>` warning; обычный lazy img
сохранён для существующего static desktop frontend/MAIN image URL, без нового
Next image-сервера или зависимости.

Browser: собранный `desktop/dist` + disposable `saved-auctions` fixture API,
production `normalize_schedule/schedule_view/local_day_bounds` для проекции времени.
Проверены все сохранённые → «Сегодня» (3 → 1, завтра END и DATE_ONLY исключены),
детальная страница/обратный переход, общая IAA-карточка с прошедшим расписанием
без ложного LIVE, ошибка HTTP 503 поверх сохранённой карточки, восстановление
чтения через refresh, переход назад в прежний Encar, RU/UK, Enter, 1280/800/360 px.
На проверенных узких экранах document.scrollWidth == clientWidth.
Trace: только GET для `/auction-lots` и `/auction` с computer_timezone; из writes
только disposable переключатель ошибки и PATCH тестового языка. Нет POST запуска,
отмены, публикации и PATCH viewed. External opener проверен callback-тестом;
реальный сайт из браузера не открывался. Ошибки двух locator-click после смены
viewport разобраны через свежую UI-state; переключение языка проверено Enter.

Evidence: ignored `work/msa08-ui/requests.jsonl`, `saved-auction-ru-1280.jpg`,
`saved-auction-uk-360.jpg`. `product-ux` сохранил разделение аукционной и Encar
композиций и явные неизвестные/ошибочные состояния. Это UI/API-fixture приёмка,
не подтверждение live Copart/IAA, не полная MSA-11 и не native Windows/macOS GUI.
MSA-08 остаётся IN_PROGRESS: следующее — постоянное хранение разделов сайтов,
затем platform gate. Никакие следующие срезы автоматически не включены.

## MSA-08: срез 6 — межсеансовая навигация

11.10.2026, `codex/multisource-auctions`. Scope: сохранение последнего раздела
Encar/Copart/IAA между перезапусками, без нового дизайна, live-адаптеров или jobs.
Предыдущие незакоммиченные изменения среза 5 сохранены. Коммит/push/сборка
приложения в этом запросе не выполнялись; пользовательская БД не использовалась.

`searchcar-workspace` хранит current + sites атомарно одной записью localStorage.
Читается прежний формат одного маршрута; сохраняются site/project/car IDs,
URL имеет приоритет, legacy URL без source остаётся Encar. Глобальные settings/
admin не заменяют предыдущий раздел сайта. Сохранённые маршруты нормализуются:
неизвестные сайты/разделы и cross-site записи игнорируются, неверные IDs ведут
к списку проектов своего сайта. При запрете storage/превышении quota навигация
продолжает работать в текущем сеансе. Поисковые выделения, формы и jobs не
сохраняются этим механизмом, запуск/отмена/публикация не вызываются.

TDD: baseline 27 source-workspace checks passed. Новый RED восстановления
multi-site записи возвращал Encar/projects вместо Copart/car; после расширения
чтения — GREEN. Дополнительные проверки выявили ошибочное ожидание существующего
заголовка тестом и лишнюю запись Encar при пустом/заблокированном хранилище:
первое сверено с реальным заголовком, второе устранено в readSiteRoutes.
**78 frontend tests passed**, TypeScript и Vite passed; прежний bundle >500 kB
warning сохраняется. Зависимости, версия, схема и backend не менялись.

Browser: финальный desktop/dist + disposable saved-auctions API. Через UI
запомнены Encar/scans, Copart/car (project=4, car=40), IAA/source-settings.
Переход на корневой URL восстановил последнюю область, выбор других сайтов —
их прежние разделы. После общих настроек закрыта вкладка и открыта новая:
current/settings восстановился, Copart/car и IAA/source-settings не потерялись.
Back и переключение RU → UK сохранили маршруты. Реальные сайты не открывались;
единственная запись API — PATCH языка disposable пользователя. Evidence:
ignored `work/msa08-ui/requests.jsonl`, `work/msa08-ui/persistence-uk.jpg`.
Журнал общий с предыдущими срезами: более ранние POST в нём не относятся
к этой проверке навигации.

Это frontend/browser-fixture приёмка, не доказательство перезапуска установленного
Windows/macOS приложения. Предыдущая macOS-сборка не содержит срез 6.
MSA-08 остаётся IN_PROGRESS: следующий этап — native platform gate.

## MSA-08: срез 7. Навигация при смене native-порта

11.10.2026, `codex/multisource-auctions`. При подготовке native gate найден
реальный пробел среза 6: `available_port()` в Tauri резервирует localhost port 0,
т.е. новый origin при запуске. Browser reload/new tab на прежнем порту не доказывал
перезапуск native-приложения; старый localStorage на новом origin недоступен.

TDD RED: пустое storage нового origin возвращало Encar/projects вместо
сохранённого Copart/car; desktop profile не возвращал существующий owner snapshot.
Причины подтверждены по native startup и profile, а не изменением ожиданий.
GREEN: desktop auth/profile возвращает валидированный снимок из
`SEARCHCAR_DESKTOP_DATA_DIR/ui/workspace-{authenticated_user_id}.json`.
PUT `/api/auth/workspace` требует сессию, CSRF и допустимый origin; owner/filename
не задаются клиентом. Только известные маршруты и безопасные числовые IDs,
site map не принимает чужой source или глобальный раздел. Файл заменяется
атомарно закрытым tempfile + fsync/os.replace; прежний файл остаётся при ошибке.
Непригодный/слишком большой/symlink файл не блокирует открытие приложения.

App использует owner snapshot вместо port-scoped storage, явный URL приоритетнее.
Записи последовательны, keepalive не меняет auth/CSRF. При отказе диска показан
постоянный role=alert на RU/UK, а не сообщение об успехе; успешная запись снимает
предупреждение. Нет нового storage framework, зависимости или миграции БД.
Web profile не получает desktop field и сохраняет прежний localStorage путь.
На первом запуске без owner-файла используются явный URL/Encar по умолчанию;
старое storage другого origin/владельца не обещаем мигрировать. В preferences
нет jobs, выделения поиска, форм, лицензии или информации о машинах; файл не
включается в переносимую резервную копию бизнес-данных.

Проверки финального состояния:

- **83 frontend passed**, в том числе owned snapshot, null vs undefined,
  заблокированный browser storage, explicit URL и восстановление actual App.
- **553 backend passed, 3 skipped** (прежние opt-in browser smoke), 3 прежних
  SAWarning reflection. Включены 15 новых проверок: реальные auth/CSRF/origin,
  два владельца/порта, schema rejection, corrupt file, failed atomic replace,
  storage 503 и отсутствие jobs.
- TypeScript и desktop Vite passed; прежний warning bundle >500 kB.
  `pnpm` wrapper пытался проверить зависимости через недоступный registry;
  проверены существующие локальные TypeScript/Vite через Node без install,
  package/lockfile/dependencies не менялись. `git diff --check` passed.

Runtime: текущий `desktop/dist` + production `configure_desktop_environment`,
migrations, `create_desktop_app`, bootstrap/session и настоящие API. Только
disposable database в `work/msa08-native/runtime`, без worker, license service,
живых сайтов, Telegram или пользовательской БД. Через UI сохранены Encar/scans,
Copart/car (project=4, car=40), IAA/source-settings. Сервер остановлен и запущен
на 18782 вместо 18781; пустой URL bootstrap восстановил IAA/settings и отдельные
разделы двух других сайтов. Общие настройки восстановились на следующем origin,
не заменив Copart/car. Введён тестовый отказ диска на 18783: реальный HTTP 503,
видимое предупреждение RU/UK, переходы доступны, прежний файл неизменен. После
возврата записи на 18784 маршрут снова сохраняется; Back не теряет контекст.
В тестовой БД ScanRun = 0. Временные серверы и вкладки остановлены/закрыты.
Evidence: ignored `work/msa08-native/port-restart-uk.jpg`, `storage-error-uk.jpg`.

Это desktop HTTP/browser runtime evidence, **не проверка собранного Tauri GUI**.
Запущенное пользовательское приложение и его данные не заменялись; имеющийся
macOS-пакет содержит прежний срез 5. Windows VM/устройство здесь недоступно.
Версия 0.4.0 не менялась, новый .app/installer, commit/push/release не делались.
MSA-08 остаётся IN_PROGRESS, live-адаптеры Copart/IAA не включены.

### Оставшийся native GUI checklist

Проверять сборку со срезами 6–8 в отдельной тестовой папке данных на macOS и
Windows, без подмены пользовательского приложения:

1. Запомнить разные разделы трёх сайтов и страницу машины. Полностью закрыть
   приложение через подтверждение, открыть снова (не Reload) и проверить
   current + site/project/car IDs. Повторить из общих настроек, после быстрых
   переходов и немедленного закрытия; ошибку записи не скрывать.
2. Encar: прежние проекты/карточки/история/фильтры. Переходы между сайтами не
   запускают и не отменяют jobs; общая очередь и адресная отмена независимы
   от текущей области (поиск только в авторизованном тестовом сценарии).
3. RU/UK, клавиатура, узкое native-окно; clipboard и detail URL в системном
   браузере, а не внутри WebView. Copart/IAA без адаптера остаются read-only.
4. «Сегодня», новый день/сон и свежий пояс компьютера — без ложного LIVE.
   На Windows повторный старт через ярлык должен открывать тот же новый binary.
5. Compiled sidecar содержит `app/sources/catalog.json`, успешно читает его
   и проходит bootstrap/API smoke. Source-code/command tests не заменяют этот gate.

До этих проверок platform gate не DONE. MSA-09 также сохраняет MSA-01
prerequisite: браузерный fixture не подтверждает разрешённый/работающий live-сбор.

## MSA-08, срез 8: расширяемый каталог источников

11.10.2026; ветка `codex/multisource-auctions`, версия 0.4.0 без изменения.
Новый срез по требованию расширяемости, не подключение live источника.
Предыдущие незакоммиченные срезы 5–7 сохранены.

### Изменено

- Один trusted build-time JSON каталог для Python и React: ключ/название,
  семейство, домены, UI entry и capabilities. Фабрики адаптеров регистрируются
  отдельно и только явно кодом, без пользовательских Python/JS import.
- SourceKey, sidebar, launch picker/activity labels и текущая/последние
  рабочие области используют каталог вместо фиксированных трёх ключей.
  Native API проверяет известность ключа, а не Literal/максимум 3 записи;
  файловая граница размера растёт с каталогом и не становится неограниченной.
- Frontend UI entry выбирается явно. Новое семейство AUCTION может использовать
  AuctionWorkspace; MARKETPLACE может подключить собственный entry. Отсутствующий
  entry показывает ошибку подключения UI, не fallback Encar/auction.
- Auction rounds и owner-scoped read API проверяют семейство каталога вместо
  пары Copart/IAA. Detail URL проверяется site-validator поверх общего HTTPS/
  домена/credentials/port guard. Desktop opener поддерживает точные домены
  каталога; прежнее поведение Encar/Telegram сохранено.
- Builder включает JSON в payload и fingerprint. Unit tests проверяют команды
  Darwin/Windows и смену cache fingerprint без bump; это не compiled smoke.

### TDD и регрессии

Исходные RED: четвёртый сайт восстанавливался Encar, отсутствовал в меню/
picker и не имел своего renderer; native API отклонял >3 сайта; auction round
отклонял ключ не из пары. Новые detail/external URL также отклонялись. Причины
прослежены до фиксированных enum/массивов/ветвлений; тесты не ослаблялись.
При GREEN обнаружены TS-undefined в Partial selections и hook вне React в
прежней проверке прямого вызова AuctionCard. Карточка остаётся чистой по props,
context читается семейным контейнером; отсутствующая selection обрабатывается.
Отдельный RED поймал неверное «Готов к запуску» при отсутствии source availability;
подпись теперь соответствует блокировке, не только project.can_search.

- 90 frontend tests passed: реальный render App с дополнительным аукционом,
  отдельной площадкой и отсутствующим UI; маршруты, picker, ссылка и Encar regression.
- 567 backend tests passed; 3 прежних opt-in browser smoke skipped,
  3 прежних SAWarning expression-index reflection. Native preferences API
  сохраняет/читает 5 зарегистрированных сайтов. Новый auction family проходит
  round persistence и оба read endpoints; Encar не становится аукционом.
  Каталог не включает несуществующий адаптер: Copart/IAA get_source отклоняются.
- TypeScript и desktop Vite passed; прежний bundle warning >500 kB.
  Nuitka `--help` подтвердил include-data-files syntax; реальная компиляция не запускалась.

### Runtime

Ignored `work/msa08-extensible` — real App + QueryClient + общий CSS, Vite на
127.0.0.1:18801; fake API, два test-only источника, без пользовательской БД,
worker, license-network, реальных сайтов или Telegram. Каталог продукта не менялся
фиктивными ключами. Новый auction использовал семейный projects/history UI;
marketplace — свой renderer без аукционной композиции. Раздел истории аукциона
восстановился после смены сайта и Reload. В общем picker видны все 5 сайтов;
«все доступные» выбрало только fixture Encar, неподключённые оставались disabled;
Escape закрыл диалог без отправки поиска. RU/UK; 360 px, document width=360,
ошибок console нет. Encar вернулся к прежнему projects toolbar/rows/filters.
Evidence: `work/msa08-extensible/five-source-check.png`; тестовая вкладка/сервер закрыты,
viewport reset. Этот fixture не подтверждает live-адаптер или native UI.

Нативная сборка, commit/push/release этим запросом не выполнялись. MSA-08 остаётся
IN_PROGRESS до platform gate; MSA-09 сохраняет MSA-01 prerequisites.
Добавление следующих модулей: [SOURCE_MODULE_REGISTRATION.md](SOURCE_MODULE_REGISTRATION.md).

## MSA-08, срез 9: compiled macOS gate и CI

11.10.2026, `codex/multisource-auctions`, 0.4.0 без bump. Следующий срез —
проверка compiled артефакта после изменений навигации/каталога, не live-адаптер.
Запущенная пользовательская `.app` со срезом 5 сохранена без закрытия/замены;
новый bundle имеет тот же single-instance identifier и отдельно не открывался.

### Повторяемая проверка

- Добавлен opt-in `backend/tests/test_compiled_navigation_smoke.py` и маленький
  runner `scripts/compiled_navigation_smoke.py`, без нового framework/dependencies.
  Временные SQLite/storage и owner создаются только для проверки. Секрет сессии
  случайный; license/session settings разработчика не наследуются child-процессом.
- Три полностью остановленных/перезапущенных compiled HTTP процесса на разных
  localhost-портах: current + отдельные Encar/scans, Copart/car IDs и IAA/settings
  сохраняются в owner-файле и восстанавливаются, в том числе общие настройки.
  Проверены отказ без bootstrap, CSRF, неизвестный source и launch-options
  каталога. После сценария ScanRun=0; workers/live-сайты/Telegram не использовались.
- При ошибке runner завершает только свой процесс и сохраняет исходную причину
  assertion/startup, не подменяет её ошибкой cleanup. JSON/JUnit и startup logs
  сохраняются в report-dir. В стандартном pytest этот тест пропущен как opt-in.
- В macOS/Windows workflows добавлен тот же runner после compiled smoke,
  с отдельным always-upload evidence (14 дней). Workflow YAML разобран успешно.
  Windows и удалённые Actions здесь не выполнялись; добавление шага не означает GREEN CI.

### Baseline / GREEN

Старый bundle `msa08-saved-auctions-ukCgw1Xn` на **отдельной временной БД**:
RED — профиль не содержит `workspace`, то есть в старом compiled backend нет
API среза 7. Это не ошибка зависимостей или health; bootstrap и auth доступны.
Ожидания не ослаблялись, тестовая копия backend штатно завершилась.
Evidence: ignored `work/msa08-compiled/baseline/compiled-navigation.xml` и логи.

Новый Nuitka onefile собран из текущего checkout. Реальный compilation report
и standalone payload содержат `app/sources/catalog.json` (535 bytes),
fingerprint `99d5034f4f8daa86`. Backend запускается и читает каталог;
SHA-256 исходного sidecar `9c1139305967b6f05df0c1b36baf1dc478948987a7e47b71108b8f635df01869`.
Старые предупреждения Nuitka о повторных tzdata-файлах не остановили компиляцию.

Tauri arm64 bundle собран offline, updater-ключи не использовались. Отдельная
локальная копия ad-hoc подписана и проходит `codesign --verify --deep --strict`:
`work/macos-artifact/msa08-native-gate.zCbxjFFE/SearchCar Desktop.app`.
Это локальная тестовая сборка, не release/notarized артефакт и не замена установленной.
Подпись меняет hash sidecar: packaged
`ad1a288fbeeafe8f772accff208f68c4987b4e51b2c259993cca5208a0864241`.

Проверены именно **ресурсы этой подписанной `.app`**, а не только build inputs:

- compiled navigation: 1 passed, три разных порта, catalog/restore/CSRF OK, jobs=0;
  `work/msa08-compiled/bundled-navigation/compiled-navigation.json` и XML/logs;
- стандартный compiled smoke: SQLite schema 11, FK=1/WAL/busy_timeout=5000,
  bundled Chromium screenshot 10 708 bytes, protected SPA и graceful shutdown OK;
  `work/msa08-compiled/bundled-results/compiled-smoke.json`;
- регрессии: 567 backend passed, 4 opt-in skipped (новый compiled smoke +
  3 прежних browser smoke), 3 прежних SAWarning reflection; 90 frontend passed,
  TypeScript/Vite passed (прежний >500 kB warning), 3 Rust unit tests passed.

### Осталось проверить

MSA-08 остаётся IN_PROGRESS. Нужны native GUI сценарии из checklist выше:
Encar без изменения дизайна, раздельные разделы сайтов после **полного** выхода,
RU/UK/клавиатура/узкое окно, clipboard и detail links, даты/сон/пояс компьютера.
Нативная оболочка пока использует штатную папку пользователя без тестового
data-dir override; disposable данные относятся к HTTP smoke, не к GUI.
Перед открытием новой `.app` полностью закрыть старую через подтверждение,
иначе single-instance вернёт уже работающую старую версию. Windows compiled
и installed restart через ярлык также требуют отдельного выполнения.
Commit/push/publication/remote CI не выполнялись. MSA-09 сохраняет MSA-01 prerequisite.

## Исправление закрытия desktop при открытом SSE — 11.10.2026

После проверки новой `.app` выявлена задержка выхода. Локальные логи backend:
18:36:31 UTC `Shutting down` → 18:37:26 `Finished server process` (~55 секунд),
с ожиданием соединений, затем background tasks. Причина по цепочке: React
держит `/api/events`; native window ждёт backend до 40 секунд, backend ждёт
бесконечный stream из синхронной очереди (`get(timeout=20)`); graceful HTTP drain
ранее не имел ограниченного timeout. Предыдущий smoke не открывал этот SSE.

**RED:** реальный отдельный source-runtime backend, временная SQLite/owner,
bootstrap и открытый SSE; `/desktop/shutdown` принят, но процесс не вышел за
5 секунд. Не импорт/health/environment failure. Broker не имел close, а
shutdown-controller не подавал сигнал подписчику. 3 failed / 2 baseline passed.
Тест завершил только собственный disposable процесс, пользовательское приложение
не закрывалось и данные не менялись.

**Исправлено:** broker close будит все очереди маркером конца, включая полные;
publish/close сериализуются существующим lock, поздний publish не затирает конец,
новый subscriber после close завершается. Stream выходит и отписывается;
GracefulShutdownController делает это при подтверждённом выходе/parent watchdog,
не при отмене диалога. Worker cancellation и сохранение отчёта не удалены;
HTTP drain ограничен 2 секундами отдельно от 35-секундного worker grace.
Rust/frontend, подтверждение выхода и 40-секундный native fallback не менялись.

**GREEN:** source-runtime закрыт с удерживаемым SSE за 0.312 секунды,
exit code 0, чистый конец потока без forced HTTP timeout, data lock освобождён.
Отдельный управляемый worker сохраняет частичный отчёт до `server.should_exit`;
RUNNING → CANCEL_REQUESTED → CANCELLED, прежние результаты сохранены,
повторный request идемпотентен. Broker owner isolation/latest update/full queues
и существующие desktop/queue checks: 48 passed. Полная регрессия:
570 backend passed, 4 opt-in skipped, 3 прежних SAWarning; 90 frontend passed.

Compiled navigation smoke теперь открывает SSE на каждом старте, держит его до
exit и требует idle exit ≤5 секунд, записывает `open_sse_shutdown` в JSON.
Этот обновлённый opt-in smoke **ещё не прогонялся на пересобранном binary**.
Существующая `.app` `msa08-native-gate.zCbxjFFE` создана ДО этого исправления
и фикса не содержит. Сборка/commit/push/release этим запросом не выполнялись.
Следующая проверка: новая сборка, закрытие без поиска, во время поиска и
немедленное повторное открытие; отдельно Windows installed scenario.
MSA-08 native GUI gate остаётся IN_PROGRESS.

## MSA-08, срез 10: пересборка и compiled SSE shutdown

11.10.2026, `codex/multisource-auctions`, 0.4.0 без bump. Пересобран текущий
незакоммиченный checkout, включая исправление SSE выше; предыдущие копии и
пользовательские данные сохранены. Новая отдельная локальная ad-hoc подписанная
сборка: `work/macos-artifact/msa08-sse-exit.16xhFMoF/SearchCar Desktop.app`.
`codesign --verify --deep --strict` passed, CFBundleShortVersionString=0.4.0.
Это не notarized release и не замена установленной `.app`.

Nuitka onefile fingerprint `aec56cc578146751`; cache старого backend не использован.
Compilation report/payload содержат `app/sources/catalog.json` (535 bytes).
Raw sidecar SHA-256:
`935429a9d18af653c5231a976ce9a13331cc1e5c32f6b00a1e87297ef3dda40e`.
После подписи bundled sidecar SHA-256:
`d72cf156026699a42e9b19fef038c163b3e3325bcaf29b744bd00269e5ae8b6e`.

Проверки на одноразовых данных, без live-сайтов/Telegram/license activation:

- Raw compiled navigation: 1 passed, 3 новых порта, catalog/restore/CSRF OK,
  jobs=0, `open_sse_shutdown=ok`; `work/msa08-sse/compiled-navigation`.
- Та же проверка **из подписанной `.app`**: 1 passed, порты 49509/49531/49541,
  owner-scoped навигация восстановлена, jobs=0. SSE остаётся открытым до выхода
  процесса, каждый idle exit ≤5 секунд, чистый EOF без forced HTTP timeout.
  `work/msa08-sse/bundled-navigation/compiled-navigation.json`, XML/startup logs.
- Стандартный smoke из ресурсов этой `.app`: SQLite schema 11, FK=1/WAL/
  busy_timeout=5000, bundled Chromium screenshot 10 708 bytes, protected SPA,
  bootstrap и graceful shutdown OK; `work/msa08-sse/bundled-results/compiled-smoke.json`.

При параллельном build tsc сначала дал TS6053: его глоб включал временные
Playwright declarations в `work/nuitka`, которые компилятор пересоздавал.
Причина проверена до правки. Новый regression test через настоящий TypeScript
config parser сначала RED (в scope попадали build outputs), затем GREEN:
исключён только `work/`; app/page.tsx и desktop/frontend остаются в scope.
TypeScript повторно прошёл во время сборки. 91 frontend tests и 3 Rust unit
tests passed; desktop Vite passed (прежний >500 kB warning). Полная backend
регрессия исправления из предыдущего шага: 570 passed, 4 opt-in skipped,
3 прежних SAWarning; production backend в этом срезе не изменялся.

Осталось: native GUI закрытие без поиска/во время поиска/быстрое повторное
открытие, сохранение разделов после полного выхода; Windows compiled и installed
restart через ярлык. HTTP smoke не подтверждает время выхода native окна.
Перед открытием новой копии полностью закрыть старую (single-instance identifier
общий). MSA-08 остаётся IN_PROGRESS, MSA-09 сохраняет MSA-01 prerequisite.
Commit/push/publication/remote CI не выполнялись.

## MSA-07: повторяемая compiled проверка времени и CI

11.10.2026, `codex/multisource-auctions`, 0.4.0 без bump. Закрыт пункт
автоматизации timezone-smoke из platform gate MSA-07; весь gate остаётся
IN_PROGRESS до Windows и реальных WebView/OS timezone/wake сценариев.
Разовый probe перенесён в существующий runner
`scripts/compiled_navigation_smoke.py`, без дополнительного framework/runner
или изменений production расчёта времени/UI/адаптеров.

### Проверяемая граница

Существующий compiled navigation baseline из подписанной `.app`: 1 passed.
Дополнительный characterization-тест также прошёл на том же backend без
изменения продукта; это не искусственный RED. Он создаёт 10 fixture-лотов
Copart/IAA в собственной временной SQLite/owner, затем читает **compiled API**.
У child-процесса пустой `PYTHONTZPATH`, удалены `PYTHONPATH`/`PYTHONHOME` и
разработческие license/session настройки. Источники/Telegram не вызываются.

- Четыре текущих request-пояса: UTC, Warsaw, Los Angeles, Tokyo; независимые
  фиксированные ожидаемые ISO-значения вокруг US spring DST gap и двух fall
  repeated hours. Проверяется переход календарной даты между США/Европой/Азией.
- START/END различаются, UNKNOWN не получает таймер. Остаток +4 часа сверяется
  с `generated_at` ответа, а не приблизительным sleep/часами тестового процесса.
- DATE_ONLY в UTC и неопределённый локальный день даты США не получают точного
  времени; неподтверждённый CST сохраняется в evidence, неверный пояс запроса
  даёт 400. Today-фильтр сверяется с локальной датой именно ответа.
- Идентичный external ID в двух источниках не смешивается. После чтения
  неизменны round IDs/current/evidence/last-observed, schedule IDs/UTC/precision/
  kind/evidence, observations/payloads, статусы машин; ScanRun=0.
- Открытый SSE удерживается до штатного выхода и на этом дополнительном старте.

### Надёжность runner

Verified RED: при раннем выходе fake sidecar тестовый runner вернул ошибку,
но оставил предыдущие JSON с `status=ok`. Причина — отсутствовала очистка
success markers, не импорт/окружение. После первой правки отдельный RED
подтвердил тот же дефект при validation failure (binary отсутствует).
GREEN: перед проверкой убираются только два принадлежащих runner success JSON;
старый green не выдаётся за новый запуск. Исходные startup failure/JUnit и
stderr logs сохраняются, посторонние файлы report-dir не удаляются. Ошибка
validation остаётся явной argparse-ошибкой. Оба сценария проверены subprocess.

### Результат

- Полная backend-регрессия: **572 passed, 5 opt-in skipped**, 3 прежних
  SAWarning expression-index reflection. Новый compiled time тест — opt-in,
  как существующий navigation smoke; обычный pytest не запускает binary.
- Окончательный compiled runner **из ресурсов подписанной macOS `.app`**:
  **2 passed**, новая temporary DB, три navigation-рестарта + отдельный time
  процесс. Evidence: `work/msa07-ci-time/bundled-final/compiled-navigation.xml`,
  `compiled-navigation.json`, `compiled-auction-time.json`, `navigation-*.log`.
  Sidecar SHA-256:
  `d72cf156026699a42e9b19fef038c163b3e3325bcaf29b744bd00269e5ae8b6e`.
- macOS/Windows build workflows запускают оба сценария той же командой и
  always-upload сохраняет JSON/XML/logs 14 дней. YAML разобран, команды и пути
  evidence проверены для обоих workflows. Это **не** выполненный Windows CI.

Frontend/production backend не менялись; новая упаковка не нужна, использована
сборка `msa08-sse-exit.16xhFMoF`. Пользовательские данные/установленная копия
не трогались. Commit/push/remote workflow/release не выполнялись. Следующая
platform приёмка: Windows compiled + installed restart, реальная смена пояса
и сон/пробуждение WebView обеих ОС. MSA-01 prerequisites остаются открытыми.

# MSA-01-T: срез 1 — тестовый профиль, 12.10.2026

Ветка `codex/multisource-auctions`. Это изоляция экспериментального runtime,
не реализация чтения Copart/IAA и не production-разрешение MSA-01-P.

## Поведение

- Opt-in только окружением desktop backend: `SEARCHCAR_SOURCE_TEST_MODE=1`.
  Перед SQLite/секретами/logs/lock выбирается `<data-dir>/source-tests`.
  Маркер `.source-test-profile` обязателен для повторного использования;
  неизвестный существующий каталог и symlink отклоняются, данные не удаляются.
- Settings проверяют изоляцию при импорте; enqueue и worker дополнительно
  проверяют фактическую SQLite connection. Payload не включает режим в обычном
  runtime. Автоматические/неявные/CARS-команды запрещены в тестовом профиле;
  явный ручной PROJECTS использует существующую очередь и лицензирование.
- Scheduler не ставит jobs и не меняет next_run_at; worker отклоняет старую
  сохранённую automatic job до браузера. Каталог рабочих адаптеров не расширен.
- API desktop/runtime сообщает `source_test_mode`, updater button скрыт;
  реальный React shell показывает RU/UK пометку изолированных данных.

## RED/GREEN и регрессии

Baseline: 69 passed (desktop_runtime, scan_launch_plan, source_registry,
multisource_queue). Новый RED: 7 failed / 1 passed — opt-in игнорировался,
enqueue/scheduler пропускали automatic команды, worker доходил до браузера.
Ошибок импорта/неподготовленной БД не было. GREEN и расширенные проверки:
22 passed в `backend/tests/test_source_test_profile.py`.

Полная backend suite: **594 passed, 5 skipped**, существующие три предупреждения
SQLAlchemy reflection. Первый sandbox-запуск: 592 passed / 2 failed / 5 skipped;
`test_desktop_shutdown` и вложенный compiled runner упирались в
`PermissionError` при `listener.bind((127.0.0.1, 0))`. Причина проверена в XML
runner, оба теста прошли после разрешения localhost, без изменений ожиданий.

Команды с временным storage/SQLite, не рабочими каталогами:

```sh
task_test_dir=$(mktemp -d /private/tmp/searchcar-msa01t-check.XXXXXX)
PYTHONPATH="$PWD/backend" STORAGE_ROOT="$task_test_dir/storage" \
DATABASE_URL="sqlite+pysqlite:///$task_test_dir/tests.sqlite3" \
.desktop-build-venv/bin/python -m pytest backend/tests -q
```

Frontend: **92 passed**, включая реальный render тестовой/обычной оболочки
и RU/UK banner; `tsc --noEmit` и Vite desktop frontend build passed.
Node в shell PATH отсутствовал; использован обнаруженный bundled runtime
Codex, без установки/смены зависимостей. Осталось прежнее предупреждение Vite
о размере chunk >500 kB, не ошибка этой правки.

## Source-runtime CLI smoke и что проверить дальше

Проверено в трёх отдельных Python-процессах на временном каталоге:
два `check` с флагом `1` повторно использовали одну тестовую SQLite;
`check` с флагом `0` выбрал отдельную обычную SQLite в родительском профиле.
Все три: status ok, schema_version 11, foreign_keys 1, journal_mode wal.
Это исходный Python runtime macOS, не compiled/GUI/Windows evidence.

Локальная CLI-проверка (создаёт только отдельный профиль внутри заданного каталога):

```sh
task_profile_parent=$(mktemp -d /private/tmp/searchcar-source-profile.XXXXXX)
SEARCHCAR_SOURCE_TEST_MODE=1 PYTHONPATH="$PWD/backend" \
.desktop-build-venv/bin/python -m app.desktop_runtime check \
  --data-dir "$task_profile_parent" --port 8765
```

В JSON database должен заканчиваться `source-tests/data/searchcar.sqlite3`.
При нормальном запуске без флага старые пути/данные сохраняются. Установка
флага без штатной подготовки изолированного профиля должна завершаться
`SOURCE_TEST_ISOLATION_REQUIRED`, а не открывать рабочую БД.

Не выполнены: новый compiled sidecar/Tauri GUI, Windows, реальные Copart/IAA
URL и сетевой smoke этих площадок. Наличие banner/зелёной suite не означает
готовность поиска: следующим срезом идёт Copart parser/mapper и его ручной запуск.

# MSA-01-T срез 2 — тестовые адаптеры и ручной поиск (12.10.2026)

Ветка `codex/multisource-auctions`. Статус **код подключён, live/platform gate
не пройден**, задача остаётся IN_PROGRESS. Отчёт среза 1 выше исторический;
MSA-01-P production/публикация остаётся BLOCKED.

## Что реализовано

- Явные test factories Copart/IAA, только при opt-in и проверенной изоляции;
  выключение флага запрещает даже ранее кэшированный адаптер. Production
  registry остаётся Encar. Новые зависимости не добавлены.
- Отдельные site DOM selectors/mappers и общий list-only collection/persistence
  слой. Выдача Copart Classic, IAA public grid; детальные страницы не открываются.
  Фильтры исходной ссылки не пересобираются, encoded `+`/`/` сохраняются.
  RU prefix Copart нормализуется только в path, без смены рынка/параметров.
- Проверяется переход страницы и нового набора ID, неизменное total, дубли,
  окончание пагинации по unique/raw total и safety limit. FIRST_PAGE —
  целевой охват, не подтверждение отсутствия остальных машин.
- UI в test profile: создание проекта своего сайта с URL/охватом, адресное
  «Обновить данные» через общий launch/worker. Общая activity/cancel остаётся
  общей. Проекты/карточки Encar не заменены аукционной композицией.
- Создание source+external-ID, наблюдения раундов, LIST_ONLY snapshots и
  типизированные CURRENT_BID/BUY_NOW/ESTIMATED_RETAIL с USD cents и значением
  zero vs unknown. Нет KRW override, ставок/покупок, Telegram и планировщика.
- URL ID IAA не подменяется Stock #; не-US лоты не сохраняются как USA/USD,
  но входят в raw coverage. Новые лоты без даты не добавляются. Неизвестный
  пробег при новом чтении не стирает известный; неизвестная единица не угадывается.
- Copart явный CEST/CET/UTC/числовой offset разрешает только конкретное
  наблюдение, не создаёт yard timezone. IAA берёт год из видимого SalesList
  URL, сверяет month/day, Chicago preference с CDT/CST и pinned tzdata.
  Другая/неизвестная city или конфликт пояса остаётся UNKNOWN: нет выдуманного
  таймера/«Сегодня». Истёкшее время не превращает лот в LIVE/SOLD.
- Проверенная thumbnail-ссылка допускает только HTTPS + image host сайта;
  UI читает её напрямую, обычный browser image request возможен. Фото не
  скачиваются/не копируются в storage. Недоступная ссылка необязательна.
- Ошибка/отмена сохраняет проверенные partial rows, но не исключает прежние
  лоты. Только успешный полный поиск по неизменным условиям меняет relation
  на NOT_FOUND_IN_SEARCH; из активной выборки убирается, история/избранное и
  связи других проектов не удаляются. Новый успешный запуск не дублирует ID.
- HTTP отказ необходимых ресурсов и CAPTCHA — ошибка, не пустой результат.
  После отказа остающиеся проекты того же источника в партии не открывают
  браузер повторно; исходный код отказа сохраняется в failures.

## TDD / проверки

Первый RED: 12 failures — Copart/IAA не зарегистрированы; импорты/БД исправны.
UI RED: создание тестового проекта не отрисовывалось. Затем отдельные RED:
HTTP 403 обязательного скрипта после документа 200 не останавливал collection;
неизвестный новый одометр стирал известный; Encar initial_full_scan флаг попадал
в отчёт аукциона; отказ доступа повторно открывал остальные проекты сайта.
Причины проверены, продукт исправлен, проверки поведения сохранены.

Новый тест остановки партии после GREEN сначала читал неверный ключ
`failure_details`: существующий worker/API используют `payload.failures`.
Это ошибка нового теста, не дефект продукта; после просмотра publish/finalize
исправлено имя поля и усилено ожидание двумя failures с исходным кодом.

Targeted: **22 passed, 2 opt-in live skipped**
(`backend/tests/test_auction_list_adapters.py`). Полная backend:
**616 passed, 7 skipped**, три прежних SQLAlchemy reflection warnings.
Frontend: **93 passed**; TypeScript `--noEmit` и Vite desktop frontend build
passed; `git diff --check` passed. Прежний Vite warning chunk >500 kB остаётся.
Fixtures/mocked worker tests — не свидетельство доступа к реальному сайту.

Команды (временная SQLite/storage; full suite требует localhost bind для
существующих HTTP/shutdown/compiled-runner тестов):

```sh
task_test_dir=$(mktemp -d /private/tmp/searchcar-auction-regression.XXXXXX)
PYTHONPATH="$PWD/backend" STORAGE_ROOT="$task_test_dir/storage" \
DATABASE_URL="sqlite+pysqlite:///$task_test_dir/test.sqlite3" \
.desktop-build-venv/bin/python -m pytest backend/tests -q
```

## Реальные источники — отдельно от GREEN

Opt-in tests с `SEARCHCAR_AUCTION_LIVE_SMOKE=1`, bundled PW 1.54 Chromium
headless_shell-1181 на macOS, временный профиль: **2 failed**. Copart документ
200, own-host JS/CSS 403, unresolved Angular templates. IAA no ListingGrid,
`_Incapsula_Resource` iframe/hCaptcha request. Повторный диагностический запуск
подтвердил отказ; он не маскируется фикстурами или ослабленным live assertion.
Обход CAPTCHA, stealth/UA/proxy/cookie-transfer не использовался.

Отдельный публичный DOM Codex IAB доступен: Copart page 2 — 20 строк / total75,
3 даты; IAA 100/page — 75 строк, 10 датированных US. Проверены видимые selectors,
lazy image URL, SalesList date/city, различие URL ID / stock. Это выборочное
DOM evidence, не полный новый all-pages обход и не bundled/live успех.
Подробности в [отчёте доступа](MULTISOURCE_PUBLIC_ACCESS_REPORT.md).

## Как запускать исходный эксперимент / что ещё проверить

Из корня репозитория, после frontend build, отдельный временный профиль:

```sh
task_profile_parent=$(mktemp -d /private/tmp/searchcar-auction-profile.XXXXXX)
SEARCHCAR_SOURCE_TEST_MODE=1 PYTHONPATH="$PWD/backend" \
.desktop-build-venv/bin/python -m app.desktop_runtime serve \
  --data-dir "$task_profile_parent" --frontend-dir "$PWD/desktop/dist" \
  --browser-dir "$PWD/desktop/runtime/browsers" \
  --playwright-driver-dir "$PWD/desktop/runtime/playwright-driver" \
  --port 8765
```

Открыть `http://127.0.0.1:8765`; обычное onboarding/лицензирование сохраняется,
test profile не копирует рабочий ключ и не обходит entitlement. Перед ручным
поиском должна быть видна RU/UK пометка source-tests. Создать проект нужного
сайта, проверить его имя/исходную ссылку, запустить и смотреть общий activity
и историю: отказ обязан быть ошибкой, не успешными «0 машин». После завершения
или отмены новая команда должна ставиться отдельно. Остановить Ctrl-C.
Это инструкция source-runtime, не проверенная нативная `.app` с этими изменениями.

Открытые gates: устойчивый поддерживаемый public transport, реальные пагинация
и сохранение фильтров в чистой сессии, все необходимые IAA preferences/форматы
дат (не только Chicago), макOS native GUI/compiled + Windows. Сборка `.app`,
коммит, push и публикация этим срезом не выполнялись. MSA-01-T не DONE.

# MSA-01-T срез 3 — видимый браузер и ручная проверка (12.10.2026)

Пользователь выбрал варианты 1/2: видимый браузер и ручное прохождение
предложенной сайтом проверки. CSV/API не включать. Ветка
`codex/multisource-auctions`. Код реализован, полная live/platform приёмка открыта.

## Поведение

- Auction adapter принудительно открывает видимый браузер, независимо от
  старой headless-настройки Encar. Bundle содержит только headless_shell;
  он не используется для видимого окна. Берётся установленный channel:
  Chrome по умолчанию macOS, msedge Windows. Опциональный
  `SEARCHCAR_SITE_BROWSER_CHANNEL=chrome|msedge` выбирает установленный браузер.
  Не устанавливаем/обновляем его и не добавляем stealth/UA/proxy.
- Профиль: `source-tests/runtime/source-browsers/<source>/<channel>`;
  обычные пользовательские профили не читаются. Cookies/preferences остаются
  только в принадлежащем SearchCar профиле; разные сайты/каналы отделены.
  Symlink отвергается, на macOS каталог mode 0700; Windows ACL отдельно не
  проверены. Runtime не входит в portable backup; секреты браузера не экспортируем.
- Нужен установленный Chrome/Edge; невозможность launch — явная ошибка
  SOURCE_VISIBLE_BROWSER_UNAVAILABLE, не fallback на shell или другой профиль.
- Ручное ожидание только в собственном видимом page: первичная проверка
  сайта или cookies до 180 секунд. Пользователь сам работает с окном, приложение
  не нажимает CAPTCHA/consent и не перезагружает страницу. Отрисовавшаяся выдача
  с тем же search scope продолжает поиск; повторный отказ останавливает его.
- В progress сохраняются WAITING_FOR_USER/reason/expires_at. UI проекта,
  истории и общей activity показывает RU/UK инструкцию; отмена остаётся доступна.
  Закрытое окно, timeout, смена фильтров — ошибки, не пустая успешная выдача.
- Неизвестная/неполная страница не исключает старые лоты. Body/context могут
  исчезнуть при собственной навигации сайта; узко обработанный navigation
  error считается loading, остальные DOM ошибки не скрыты. Готовый header
  без строк тоже loading, подтверждённое zero остаётся отдельным случаем.
- List-only checkpoint/progress используют существующий SCAN_COMMAND_LOCK
  и короткий commit, не держат flushed heartbeat write lock при ожидании.
  Общий Encar collection path сохраняет прежнее поведение.

## RED/GREEN и результаты

Baseline ближайших suites: 59 passed / 2 skipped. RED интерактивного режима:
6 failures — ephemeral/headless launch, нет ожидания, CAPTCHA вместо timeout.
Дополнительные подтверждённые RED: shell нельзя сделать обычным окном;
проверка могла сменить фильтры; cookie overlay принимался за готовую выдачу;
оба DOM scripts падали при body=null; navigation context error прерывал
loading; header раньше rows давал Unconfirmed empty result.

Конкурентный cancellation test сначала прошёл, повторный прогон дал
OperationalError database locked. Причина — heartbeat autoflush удерживал
write transaction между polling ticks, cancel API не мог записать команду.
Исправлен именно list-only checkpoint/progress. После правки отдельный
connection отменяет ожидание, браузер закрывается, новый job проходит; targeted
suite и два дополнительных повторения cancellation test зелёные.

Итог на финальном коде: **37 passed / 2 live skipped** auction suite;
**631 passed / 7 skipped** full backend, три прежних reflection warnings;
**96 passed** frontend, TypeScript passed, desktop frontend Vite build passed;
diff --check passed. Мок/изолированный DOM не доказывает реальную доступность.

## Реальная сеть / ручная проверка

Live macOS Chrome: Copart принял первые 20 / total75 без 403. Начальная
all-pages попытка остановилась на Next: cookie overlay intercepted pointer.
Добавлено ручное ожидание; следующая попытка дождалась 180 секунд и завершилась
SOURCE_VERIFICATION_TIMEOUT, поскольку окно не закрыли. Первый набор/транспорт
подтверждены, весь поиск/пагинация и завершённая ручная проверка — нет.

IAA Chrome: исходная диагностика скрывала ошибку при body=null; исправлена
без ослабления live assertion. Следующий run подтвердил destroyed execution
context во время навигации, затем Unconfirmed empty result при header до rows.
Добавлены и пройдены отдельные RED/GREEN. После финальной readiness-правки
новый live-run не выполнен. Прохождение CAPTCHA человеком не проверено.

Computer Use native-window observation ждало разрешений Accessibility /
Screen Recording; разрешения не меняли, screenshot/визуальной приёмки нет.
Chrome был реально запущен продуктовым API, но это не native SearchCar `.app`
и не Windows smoke. Не сделаны native build, commit/push/release.

Запуск исходного профиля — команда среза 2 выше; для Chrome можно добавить
`SEARCHCAR_SITE_BROWSER_CHANNEL=chrome`. В отдельном окне закрыть cookie dialog
желаемым вариантом, вручную пройти предложенную проверку, не менять фильтры.
Проверить полный обход, даты/фото/дубли, отмену из приложения, новый запуск
после отмены и повторный старт с тем же SearchCar profile. CSV/API не нужны.
При timeout окно закрывается; новую попытку запускать явно. MSA-01-T не DONE.

# MSA-01-T срез 4 — проход, ожидания и диагностика (13.10.2026)

Ветка `codex/multisource-auctions`. Существующие незакоммиченные изменения
лицензии/Windows сохранены. Адаптеры остаются экспериментальными, list-only;
нет включения production, планировщика, Telegram, аккаунтов или API/CSV.

## Реализовано

- Независимые ожидания cookies и доступа: по одному ограниченному этапу каждой
  категории, в том же принадлежащем приложению окне, с отменой и проверкой scope.
  Переход consent → challenge или обратно обновляет инструкцию пользователю.
- HTTP429 — `SOURCE_RATE_LIMITED`, прекращение оставшихся проектов этого сайта
  в текущем batch. Не повторяем запросы автоматически и не предлагаем CAPTCHA.
- XHR/fetch401/403 не отменяет подтверждённые новые DOM-строки. Если новый набор
  не приходит, фиксируется отказ доступа; document/script/stylesheet по-прежнему
  обрабатываются консервативно. Диагностика ограничена20 записями host/path/status/
  type, query/fragment и тела запросов не сохраняются. Ошибка и progress содержат
  эту диагностику, worker переносит её в failure payload.
- `SEARCHCAR_SOURCE_TEST_MAX_PAGES=50` по умолчанию, диапазон1–1000. Explicit
  max_pages тоже проверяется. Лимит, changed total, повтор ID, ошибка/отмена —
  неполнота, не успешные zero/sold/исключение старых лотов. Полученное сохраняется.
- Progress содержит read_summary (с датой/точным временем/без назначенной даты/
  непроверенное расписание/чужой рынок), максимум5 примеров schedule_issues и
  stop_reason. Проект и история объясняют результат RU/UK. FIRST_PAGE — выбранный
  успешный охват, не провал ALL_PAGES.
- Copart CSS-hidden consent overlay больше не считается видимым диалогом.
- По пользовательскому screenshot выявлен неверный размер видимого браузера:
  viewport1440×1200 мог быть больше реального окна, кнопки cookies уходили вниз,
  пользователь не мог сделать выбор. Убрана эмуляция размера, no_viewport=True
  использует настоящее окно Chrome/Edge. Это не модификация CSS сайта/consent.
- IAA Timed определяется по реально наблюдённому timedauctionCountdown DOM.
  Не заявляем его action временем START; event_kind UNKNOWN до отдельного
  подтверждения семантики/года. Различие action/Sale List — DATE_CONFLICT,
  без придуманного UTC или добавления нового бездатного лота.

## RED/GREEN и ограничения проверок

Baseline source/time81 passed /2live skipped. Первые новые проверки дали7
ожидаемых RED (этапы ожидания, необязательный fetch, diagnostics,429, page budget)
и3 ошибки нового fixture: взяты все IAA-строки вместо двух. Fixture исправлен
без ослабления условий. Для duplicate-case новая страница должна отличаться
от stale previous signature, но содержать повтор ID; обновлена синтетическая
выборка, ожидание SEARCH_RESULTS_CHANGED и сохранение прежних лотов не изменены.

Отдельные RED/GREEN: отсутствие RU/UK пояснения полноты/FIRST_PAGE; скрытый
overlay ошибочно требовал consent; IAA Timed выдавался за START; launch задавал
виртуальный размер вместо реального окна. Отмена, повторный отказ, identity,
история при partial и отдельный batch-stop429 покрыты регрессиями.

Полный backend сначала дал2 environment failures из-за запрета localhost
socket в sandbox. Повтор вне sandbox прошёл без правки этих тестов. Итог после
исправления viewport: **654 passed /7skipped**,3 прежних reflection warnings;
frontend **100 passed /1 PowerShell skipped**; tsc passed; Vite desktop frontend
build passed (прежнее предупреждение о размере JS chunk). Это не native bundle.

## Live macOS, не Windows и не установленная .app

- IAA без смены размера: reported76, прочитано76 на одном экране100/page,
  9 точных US-расписаний/image URL,63 без даты,2 Timed с разными action/Sale List
  датами,2 Canada. Неопознанные даты исследованы только в строках выдачи.
- IAA с размером25 через штатное меню, до изменения viewport: **4 страницы,
  76 уникальных результатов / reported76**, без403. Next и конец подтверждены.
- Copart с прежним viewport: реальная таблица была доступна, но consent не
  завершался. После screenshot пользователя добавлено исправление размера.
  **Финальный Copart: 4 страницы, 72 уникальных / reported72,16 точных дат,
  56 без даты,16 image URL, нет сетевых отказов.** Пользователь сделал выбор
  cookies в новом окне, после чего продолжилась обычная пагинация.
- Повтор IAA с настоящим окном остановился в live-only helper выбора25/page:
  список открыт, но размер остался100. Это не доказательство403 или провала
  основного collect path; ожидания полноты не ослаблены. Сначала исследуется
  готовность штатного click binding и повтор на финальном коде.
- Устаревший тестовый Copart с прежним viewport остановлен SIGINT по проверенному
  PID, закрытие принадлежало его finally. Пользовательские Chrome profiles не
  затрагивались. Старый запуск не учитывается как успешный/финальный результат.

Новые нативные сборки, Windows/Edge, GUI после установки, повтор со сохранённым
профилем, реальное сохранение через установленный лицензированный worker и
production MSA-01-P остаются отдельными gates. Коммит/push/release не выполнялись.

## Завершение проверки IAA после прерывания (13.10.2026)

Уточнение прежней диагностики: причина не в доказанном отсутствии click binding.
IAA успевал отдать промежуточный server-rendered DOM:73 строки при reported76,
ещё без часовой настройки и готового пагинатора. Старое ready принимало любую
непустую часть как готовую страницу и могло ошибочно считать отсутствие Next
концом. Гипотеза ожидания глобального Knockout binding не подтвердилась и не
вошла в продуктовый код; ошибочные live-прогоны не пересчитаны в успешные.

Новый ready ожидает полный набор строк по total/page/pageSize, active и Next,
выбранную city. Настоящее подтверждённое zero остаётся отдельным исключением.
DOM regression сначала дал RED на преждевременной готовности, затем GREEN.
Это не увеличение timeout и не ослабление контроля полноты.

Финальные source-runtime проверки на macOS Chrome с no_viewport:

- default100/page:76/76, complete=true,9 exact US dates, без отказов;
- **25/page:4 страницы,76/76 уникальных, complete=true**; тот же результат
  повторён с передачей реальных строк в существующие storage/read API;
- **9 датированных US-лотов сохранены/прочитаны в одноразовой тестовой БД**;
  source identity, UTC и точность сопоставляются с результатом нормализации.
  Новые63 бездаты,2 Timed DATE_CONFLICT и2 Canada не добавлены вместо угадывания.
  Это не проверка установленного лицензированного worker: владелец/база — fixture.
- Backend **654 passed /7skipped**, frontend **101 passed /1skipped**, tsc
  passed. Существующие3 reflection warnings не менялись.

Не заявляем вечную доступность площадок или завершённый MSA-01-T: повтор
профиля после закрытия, другие поисковые ссылки, нативные macOS/Windows и
production условия остаются отдельными gates. Рабочие профили/ключи не читались,
Telegram не использовался. Copart в этом продолжении не перезапускался:
его финальный проход72/72 из среза4 уже подтверждён, адаптер не менялся.

## MSA-01-T срез 5 — фоновое чтение, ручной fallback и паузы (13.10.2026)

Уточнение пользователя заменяет always-visible транспорт среза3/4. Для Copart
и IAA обычный запуск — headless установленного Chrome/Edge, не пользовательского
браузерного профиля. Проверка/выбор cookies выполняются только человеком;
видимое окно открывается при подтверждённом запросе, не при любой ошибке DOM/403.
При 429 остаётся остановка batch источника без reload или нового окна.

Транспорт поддерживается штатными Playwright каналами и persistent context:
[браузерные каналы](https://playwright.dev/python/docs/browsers#google-chrome--microsoft-edge),
[lifecycle persistent context](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).
Малый владеющий session handle сохраняет cleanup worker при смене контекста;
сначала закрывается фоновой контекст, затем видимый открывает тот же собственный
профиль. Сетевые listeners переносятся. Видимое окно остаётся на остаток текущего
поиска, следующий проект вновь стартует в фоне. Рабочая база/лицензия не меняются.

Минимальный интервал 3 секунды между поисковыми навигациями/Next/Classic внутри
сессии; cancel checkpoint проверяется во время паузы (до 250 мс). Это ограничение
частоты, не имитация человека: нет random mouse/jitter, смены UA, прокси, скрытых
API или обхода CAPTCHA. Подресурсы страницы загружаются браузером штатно;
нельзя обещать, что сайт перестанет распознавать автоматизацию.

Если ручное подтверждение появляется после принятых страниц, свежий видимый
экран нельзя считать продолжением AJAX pagination. Частичные строки сохраняются,
complete=false, UI RU/UK просит повторить поиск; прежние лоты не исключаются.
Отмена, закрытие окна, неудачный launch, timeout, повторные запросы и переход
CAPTCHA→cookies остаются ограниченными и проходят регрессии.

### RED/GREEN и живые проверки

- Baseline адаптеров:49 passed /2 opt-in skipped. В первом новом прогоне
  обнаружен отсутствующий импорт fixture (не RED); исправлен. Затем8 RED
  подтвердили always-visible поведение, отсутствие пауз и manual fallback.
- GREEN проверяет отсутствие окна при нормальном чтении и parse/403/429,
  один fallback в тот же профиль, закрытие обоих contexts, сетевые события после
  relaunch, cancel во время паузы и background на следующий проект.
- Первый live Copart нашёл публичную выдачу, но cookie-баннер пришёл между DOM
  check и Classic click. Воспроизведён отдельным RED, затем исправлен без
  принудительного клика/выбора cookies; FC и OneTrust проверяются на видимость.
- Новый live Copart: reported68, обнаруженный cookies → видимое окно; ручной
  выбор не завершился за180сек, SOURCE_VERIFICATION_TIMEOUT, не empty success.
  Полного обхода нового транспорта не заявляем, старый72/72 не переименован.
- Новый live IAA на macOS, включая повтор после изменения фонового viewport:
  headless показал проверку, видимое окно затем получило публичную выдачу;
  **4 страницы,76/76 уникальных,9 exact US-расписаний**, те же9 сохранены и
  прочитаны через существующий storage/read API в одноразовой базе. Ручное
  автоматическое нажатие проверки не выполнялось. Это fallback, не полностью
  невидимое получение данных и не тест Windows/установленного worker.

Нативные сборки, Windows/Edge, повтор сохранённого профиля и завершение нового
Copart после выбора cookies не подтверждены. Модули по-прежнему source-test-only;
MSA-01-T остаётся IN_PROGRESS, production gates не изменены. Коммит/push/release
не выполнялись.

Финальная регрессия среза5: backend **668 passed /7 skipped**,3 существующих
reflection warnings; frontend **105 passed /1 PowerShell skipped**;
TypeScript и Vite desktop frontend build passed. Предупреждение большого JS
chunk прежнее. Это сборка frontend, не новая нативная macOS/Windows `.app`.

## MSA-01-T: возврат после релиза0.4.1 — 15.10.2026

Ветка `codex/multisource-auctions`, исходный HEAD3aa457b и существующий WIP
сохранены. До правок создан локальный snapshot tracked/staged patches и tar
untracked-исходников в `work/auction-resume-20261015.5N8IEr/`.

### Перенос хотфикса без перезаписи аукционных изменений

Recovery-клиент и миграция0007 уже совпадали с выпущенным main. Серверные
дополнения source policy и отдельные тесты сохранены; сервис не разворачивался.
Из main перенесены Windows `IShellLinkW` smoke reader и диагностическая workflow
(сравнены побайтно с main), каноническая версия обновлена до0.4.1 и синхронизирована
штатным скриптом. Это перенос содержимого, не git merge/commit/push.

Проверки на итоговом коде:

- pytest лицензирования, list adapters, background/manual browser, time/rounds,
  auction API/migration и test profile: **242 passed /2 opt-in network skipped**;
  одно существующее SQLAlchemy reflection warning.
- Production web build → Node suite: **105 passed /1 PowerShell skipped**.
- TypeScript, desktop frontend build, `cargo check --offline --locked`: passed.
- Windows installer не запускался на macOS. Опубликованный0.4.1 не изменён.

### Один живой Copart, без автоматического выбора согласия

Команда: существующий opt-in pytest
`test_real_public_lists_in_background_with_manual_fallback[copart]`, с
`SEARCHCAR_AUCTION_LIVE_SMOKE=1` и disposable basetemp
`work/auction-resume-20261015.5N8IEr/live-copart/pytest`. Лимит10 страниц,
исходная поисковая ссылка Audi A5; подробные страницы не открывались.

Наблюдение: headless Chrome обнаружил `COOKIE_CONSENT`, открыл свой отдельный
visible context и ждал180сек. В конечном DOM `consent_required=true`,
`blocked=false`, `can_prepare=true`, `ready=false`; размер окна1280×657.
Текст нового вида показал66 Search results. Никаких проверенных Classic pages
не принято:0 страниц,0 сохранённых лотов, `SOURCE_VERIFICATION_TIMEOUT`.
Это неполученное ручное подтверждение, не успешный empty/полный результат и
не доказанный дефект парсера. Единственный зафиксированный requestfailed —
рекламный pagead ping `ERR_ABORTED`, не отказ нужного ресурса Copart.

Новый собственный persistent profile сохранён в этой disposable папке;
профили/секреты рабочего приложения не использовались. Никаких consent clicks,
proxy, UA spoofing, CAPTCHA bypass, Telegram или production jobs не было.
Исходный failing live test не ослаблен и не объявлен GREEN. Повторять запросы
без согласованного момента ручного выбора не стали. Следующий шаг — ручной
выбор cookies, полный проход и повтор с сохранённым профилем. MSA-01-T не DONE.

Отдельная нативная macOS-сборка от14.10 уже существует:
`work/macos-artifact/auction-background-20261014.nNDS0dBc/BUILD.md`. Она0.4.0 и
предшествует переносу хотфикса; packaged/compiled smoke не равен живому GUI
поиску. Новую нативную0.4.1 в этой задаче не собирали, Windows/Edge не проверяли.

## MSA-01-T: автоматический отказ от необязательных cookies — 15.10.2026

Ветка `codex/multisource-auctions`. Разрешение пользователя: автоматически
выбирать только необходимые cookies; неизвестные варианты и CAPTCHA — вручную.
Новые зависимости, UI-настройки, миграции и перенос чужого профиля не нужны.

Обработчик общий для Copart/IAA, вызывается перед ручным fallback в начале
и при пагинации/подготовке Classic. Разрешены только распознанные provider
контейнеры и явные кнопки отказа с проверкой подписи/видимости/единственности.
Одна попытка на selector за поиск, нормальная actionability, короткое отменяемое
ожидание исчезновения баннера. Никаких accept-all, force-click, скрытых API,
доступа к cookies рабочего браузера или обхода ограничений. Сайт сохраняет
выбор в существующем отдельном persistent-профиле. IAA теперь тоже сообщает
видимые FC/OneTrust баннеры, даже если таблица уже загружена.

RED подтверждён по отсутствию reject-click/ошибочному ручному relaunch; отдельно
воспроизведены повторный CMP (`COOKIE_CONSENT` вместо продолжения/общего timeout)
и auto-reject после CAPTCHA посреди обхода (`SEARCH_PAGE_NOT_READY` вместо
`SEARCH_VERIFICATION_RESTART_REQUIRED`). Исправлены причины, не ожидания.
Повторный CMP использует исходные expires_at/deadline и уже открытое окно;
повторный ACCESS по-прежнему не запускает новый цикл. Partial/restart не обходится
даже при автоматическом закрытии cookies после ручной проверки.

Проверки:

- Целевые adapters/background/cookie tests:75 passed; live/browser flags отдельно.
- Итоговая полная backend-регрессия с `SEARCHCAR_COOKIE_BROWSER_SMOKE=1`:
  **694 passed /7 skipped**,3 прежних reflection warnings (40.89сек).
  Два исходных сбоя вызваны sandbox запретом bind127.0.0.1 в shutdown/compiled
  runner tests; повтор с разрешёнными локальными портами прошёл без изменения
  тех тестов. Итоговый единый прогон включает все14 Chrome browser-cases,
  включая отказ от автоматического `Manage options / Confirm choices`.
- Node DOM checks:7 passed. Отдельный реальный Chrome smoke:**14 passed**.
  Routed fixtures используют
  действительные DOM/scripts, обычные клики и закрытие/повтор того же isolated
  source-профиля. Они проверяют OneTrust banner/preferences, Google direct reject,
  оба источника, отсутствие кликов по accept/outside/unknown/duplicate/hidden,
  CAPTCHA, retained banner и многошаговый `Manage options` fallback.

Живое наблюдение: первоначальный opt-in full Copart smoke этой задачи завершился
ошибкой на повторном CMP,0 принятых страниц. Отдельная DOM-проверка собственного
профиля обнаружила OneTrust `Reject All` → reload → Google FC с `Consent / Manage
options`. В preferences есть unchecked consent и checked legitimate-interest
checkboxes, включая vendors. Поэтому простое `Confirm choices` не является
безопасным отказом и не автоматизировано. Этот вариант требует человека;
штатные Google direct-reject варианты поддержаны только когда реально есть
явная кнопка. После исправления повторного ожидания полный live не повторялся.
Нет доказательства полного Copart/IAA прохода в этой задаче; старый IAA76/76
остаётся наблюдением13.10. Артефакты текущей проверки:
`work/cookie-choice-20261015.TyAaUP/`. Native macOS/Windows не собирали/не проверяли,
production gate не меняли, коммит/push/release не выполняли.

## Временный all-sites для действующих лицензий — 15.10.2026

Решение пользователя: сервер лицензий пока не обновлять; локально разрешить
все подключённые сайты для любой действующей лицензии с общим `search=true`.
Ветка `codex/multisource-auctions`, без изменения версии, деплоя, коммита или
правок реальных лицензионных файлов. Выбор сайтов уже подготовлен в owner UI;
его rollout и возврат применения signed subset/empty отложены вместе.

Причина: тестовый профиль был активирован, но сохранённый подписанный lease
содержал только `sources=[encar]`. UI дополнительно блокировал создание проекта
через `can_search`. Общая entitlement-граница теперь берёт все подключённые
адаптеры реестра, без привязки к трём именам. Создание/сохранение аукционного
проекта не зависит от прав поиска, сами запуски по-прежнему защищены.

RED:9 тестов TRIAL/SUBSCRIPTION/PERPETUAL × subset/другой subset/empty показали
старую фильтрацию и отсутствие полного доступа; rendered UI test показал
disabled на кнопке создания. GREEN проверяет также четвёртый fake источник,
неизменённые подписанные bytes и expiry, повтор через offline/refresh и очередь.
Старые ожидания применения subset обновлены по новому пользовательскому
контракту; очередь по-прежнему проверяет общую лицензию, отдельные проверки
revocation механизмов с injected decisions сохранены. Непоставленные адаптеры,
искажённая подпись/структура, другое устройство, откат часов, истёкший lease/
подписка и общий `search=false` не получают разрешения.

Проверки итогового кода:

- Backend:689 passed /21 opt-in skipped,3 прежних reflection warnings.
- Frontend:106 passed /1 PowerShell skipped; TypeScript passed.
- Desktop frontend production build passed; прежнее предупреждение о размере chunk.
- Read-only проверка новой source-логики на существующей тестовой лицензии:
  active, can_search=true, источники copart/encar/iaa; expiry остаётся15.11.2026.
  `update_trusted_time=False`, без перезаписи binding/lease/trusted-time и без
  запросов к серверу. Это проверка source-runtime, не новой установленной .app.

Для уже собранной/запущенной .app необходима новая клиентская сборка. Серверный
rollout сам по себе не вернёт запреты в старый temporary-all-sites клиент:
потребуется его обновление. MSA-01-P и изоляция experimental source-tests не сняты.

## Copart: фото и фильтры пагинации — 15.10.2026

Ветка `codex/multisource-auctions`. По запросу исправлены два дефекта; правило
«новый лот без назначенной даты не сохраняем» оставлено без изменений.

Фото уже было сохранено в `Car.details.main_image_source_url`, но оба API
аукционного просмотра заменяли его на `None`, если локального MAIN не было.
Теперь локальный MAIN имеет приоритет, затем используется прошедшая source-domain
проверку ссылка. Изменены список проекта и отдельная машина, без скачивания
фото, открытия карточек или разрешения произвольных URL.

Живое воспроизведение Copart: первая страница Audi A4 —42 результата, запрос
Next теряет `filter[MAKE]`, `filter[MODL]`, `filter[VEHT]` при неизменном URL.
Вторая страница уже содержит KIA и сообщает1000 результатов. Поэтому прежний
`SEARCH_RESULTS_CHANGED` не был ложной тревогой. Перенос исходных выражений в
MISC тоже не подходит: UI добавляет иные ограничения, результат42→39.

Исправление в Copart adapter сохраняет копию фактических параметров принятого
запроса перед Next. Через существующее событие DataTables
[`preXhr`](https://datatables.net/reference/event/preXhr) передаётся та же область
поиска, меняются только штатные `draw/start/length/page/size`. Источник данных —
[`ajax.params()`](https://datatables.net/reference/api/ajax.params%28%29),
не скрытый отдельный запрос и не перевод выражений. Переключение остаётся обычным
кликом по Next; IAA использует прежний путь. Если таблица не позволяет сохранить
параметры, обход останавливается как partial. Паузы, отмена, CAPTCHA/429,
сверка total/дубликатов/полноты и защита прежних машин от исключения сохранены.

TDD: две photo-проверки упали на `None` до правки; две проверки пагинации
воспроизвели потерю фильтров и отсутствие безопасной остановки. Node выполняет
реальный adapter-script на fixture параметров, включая третью страницу, копию
вложенных фильтров, сохранение exclusions и удаление добавленных UI ограничений.

- Целевые Python:101 passed /16 opt-in skipped; DOM JavaScript:8 passed.
- Полный backend:695 passed /21 opt-in skipped,3 прежних reflection warnings.
- Полный frontend:107 passed /1 PowerShell skipped.
- Живой дефект воспроизведён, но итоговый полный проход после исправления пока
  **не подтверждён**: Copart показал CAPTCHA, диагностика остановилась без обхода
  защиты и без записи в проекты. Артефакты: `work/auction-photo-pages-20261015.y0ZdbF/`.

История запусков: принятый8.10 макет `auction-app-integration.html#runs` есть,
но production UI пока минимальный тестовый. В этой задаче его не переделывали.
Native app/Windows, загрузка remote-фото в установленной .app и реальный полный
обход после правки требуют проверки. Сборка, коммит/push, релиз и деплой не
выполнялись; production gates не сняты.

## Фото, создание и карточки аукционных проектов — 16.10.2026

Ветка `codex/multisource-auctions`, без изменения дизайна Encar, правила дат,
живых проектов/лицензий, публикации или новой native-сборки.

- Общий просмотр фото Copart/IAA открывается поверх приложения. Используется
  уже предоставленное изображение, без угадывания high-resolution URL. Native
  dialog удерживает фокус; Escape, кнопка и фон закрывают просмотр, фокус явно
  возвращается на миниатюру. Ошибка загрузки видима и не блокирует закрытие.
- После создания разрешённого проекта UI один раз отправляет только новый ID
  через существующую общую очередь. Это пользовательский запуск, не планировщик.
  При недоступном поиске проект сохраняется без запуска с объяснением. Ошибка
  enqueue не становится ошибкой создания и не предлагает создать дубликат.
- Название и действие карточки разделены CSS-классами: прежний общий selector
  увеличивал также кнопку «Обновить данные». Компактные метаданные содержат
  число машин, создание и последнее успешное завершённое обновление. Переносы
  и отступы проверены с длинным именем на1280/800/360; inline-форма больше не
  наследует модальную тень. Encar composition не менялась.
- `last_refreshed_at` вычисляется из завершённых ScanRun с успешной порцией
  ProjectScanRun. Редактирование проекта, более новый failed/running и другой
  проект не подменяют дату. В многопроектном запуске дата появится после
  завершения общего ScanRun; отдельного per-project finished_at в схеме пока нет.
  У нового проекта без такого наблюдения — «Ещё не обновлялся», не дата создания.

IAA screenshot использует `/Vehiclelisting/Cars/Hyundai`, а поставленный adapter
поддерживает `/Search?url=…`. Read-only проверка каталога вернулаHTTP200 с явной
защитной страницей,0 строк; обход не выполнялся. Поддержка непроверенного формата
не включена и фильтры не угадываются по названию проекта. Формат правильной
поисковой ссылки приходит из metadata источника; форма и безопасный код422
поясняют отличие каталога/объявления от поиска. Backend-границы URL сохранены.
Успешное создание и enqueue корректной IAA Search-ссылки проверены на fixtures,
не объявляются подтверждением live-доступа.

RED: два API-теста выявили отсутствующий last_refreshed_at; rendered tests —
некликабельное фото и отсутствие метаданных. Browser baseline воспроизвёл0
команд поиска после создания, общую ошибку IAA и отсутствие preview. Отдельно
проверка нашла потерю фокуса после Escape; добавлен явный focus return.
Первый повтор теста дат остановился из-за регистра React SSR `dateTime`, не
данных: assertion нормализован по регистру HTML-атрибута. Единственная import
ошибка pytest была запуском из root без backend path; исправлен cwd, не тесты.

Проверки: backend697 passed /21 opt-in skipped,3 прежних reflection warnings;
Node110 passed /1 PowerShell skipped; TypeScript и desktop frontend build passed.
Сохранён повторяемый browser smoke `scripts/auction_workspace_ui_smoke.py`:
все API/медиа — fixtures, без пользовательских данных, сообщений или live-сайтов.
Итоговый browser smoke:9 passed, включая UA IAA и unavailable image.
Артефакты RED/GREEN/скриншоты: `work/auction-workspace-20261016.UyE4gX4t/`.
Нативная .app и полный живой поиск IAA остаются отдельной проверкой.

## Выбор фильтров и автосохранение черновика — 16.10.2026

Первый срез в `codex/multisource-auctions`, только source-test профиль.
Форма аукциона использует кнопку выбора вместо обязательного URL. Окно работает
в собственном профиле сайта; cookies/токены не копируются в конфигурацию.
После подтверждения или закрытия сохраняется последний успешно захваченный
набор. Проект/поиск создаются только отдельной кнопкой, не событием закрытия.
В незавершённой правке остаётся старая сводка с предупреждением; создание
блокируется. При повторном открытии старый snapshot не удаляется до нового успеха.
Чужой/устаревший selection_id и несовпавшие условия отвергаются сервером.

RED/GREEN: отсутствовавшие методы захвата, состояния завершения/закрытия и API;
browser RED показал отсутствие нового выбора в форме. Дополнительные проверки
нашли утрату предыдущего snapshot при повторном открытии, SQLite write-lock
между попытками открыть занятый профиль, ошибочную обработку Copart vehicleFinder
как неисправного поиска и fullscreen min-height встроенной формы на360 px.
Причины исправлены в owning boundaries, проверки не ослаблялись. Поздний старый
ответ сохраняет новый snapshot, но отменяет готовность: его callback мог
перерисовать DOM старой выдачей, поэтому нужна новая проверка.

Итог: backend724 passed /28 opt-in skipped,3 прежних reflection warnings;
Node111 passed /1 PowerShell skipped; TypeScript и desktop frontend build passed
(прежнее предупреждение о крупном JS chunk). Bundled Chromium fixture suite:
7 passed. Browser UI:10 passed; повторно проверены RU/UK,1280/800/360, фото,
отдельное подтверждение, create→queue ровно одного проекта и сбои enqueue.
В финальном узком снимке нет пустого полноэкранного блока inline-формы.
Артефакты RED/GREEN: `work/source-selection-20261016.9uapOC7J/`, финальный UI
результат/скриншоты — `ui-verified-2/`.

**Не готово:** IAA restore, миграция Project/search_config и frozen scope на
повторном обходе. IAA creation по пресету заблокирован явно; общей ссылкой не
подменяется. У Copart пока URL/Classic-путь. Ни mocked UI, ни семь browser fixture
тестов не доказывают live-поиск, native macOS или Windows. Новая .app, коммит,
push и публикация не выполнялись; MSA-01-T остаётся IN_PROGRESS.

## Панель выбора поверх сайта — 16.10.2026

Ветка `codex/multisource-auctions`, исходный код/source-test профиль.
В управляемом окне Copart/IAA есть сворачиваемая панель SearchCar со сводкой,
состоянием и постоянно видимой кнопкой подтверждения. Доступность кнопки следует
проверенному черновику. Нажатие не создаёт проект и не ставит job в очередь;
действия панели не помечают условия как изменённые. Закрытие сайта сохраняет
последний подтверждённый выбор, незавершённая правка не принимается.

Пользовательский IAA-черновик перед изменением имел SELECTING, без snapshot и
без issue. Этого недостаточно, чтобы установить точную причину live-сбоя.
Воспроизведённый тестами дефект: выбор зависел от scan-readiness, включая город
аукциона и пагинацию. Для захвата IAA теперь отдельный selection-readiness:
обычный успешный POST с валидными публичными условиями плюс отображённый
результат. Сам сборщик по-прежнему требует прежнее доказательство готовности.
Дополнительно поясняются отсутствие запроса, ожидание выдачи, cookies,
неизвестный формат, сетевой отказ, CAPTCHA и обычная блокировка доступа.

RED: browser-проверка упала на отсутствии панели; отдельная DOM-проверка — на
отсутствии selection-readiness; unit-проверки — на отсутствии причин ожидания.
Позднее iframe-тест показал принятие условий не из основной страницы: захват
теперь ограничен main frame, подтверждение/панель — собственным HTTPS-сайтом.
Не сохраняются дополнительные headers/cookies/токены, обход защиты не добавлен.

Причины промежуточных падений исследованы до исправлений: тест ошибочно требовал
пагинацию для положительно подтверждённой пустой выдачи (старое поведение уже
принимает её); Node DOM-fixture не реализовывала настоящий getAttribute;
два backend-теста не могли bind localhost в sandbox. Исправлены ожидание/fixture
и разрешён локальный запуск, продуктовые проверки не ослаблялись.

Финал: backend729 passed /39 opt-in skipped,3 прежних reflection warnings;
Node112 passed /1 PowerShell skipped; отдельно bundled Chromium18 passed;
frontend browser11 passed, RU/UK,1280/800/360; TypeScript и desktop frontend
build passed (прежние предупреждения node_modules/chunk size). Панель на360 px
проверена визуально: сводка прокручивается, кнопка помещается, фокус виден.
Артефакты: `work/source-selection-panel-20261016.dryrpy/`.

**Не проверено:** текущая живая выдача IAA/Copart и native macOS/Windows с этой
панелью. Отдельный вопрос о готовности пользователя к live-проверке отправлен,
ответа к завершению работы нет. Новая .app не собиралась, старая не заменена.
IAA restore/create-gate и оставшиеся задачи search_config не закрыты; это не
заявление о готовом повторном поиске IAA. Commit/push/публикация не выполнялись.

## Активация подтверждения после применения галочки — 16.10.2026

Ветка `codex/multisource-auctions`. Обработчик отмечает новое значение checkbox/
radio в capture-фазе, до site handler. Последующие click/input/change одного
элемента с тем же значением не сбрасывают уже захваченный поисковый запрос.
Новый элемент или новое значение считаются новым изменением. Для select
учитываются selected values, для текстовых полей — value. Это наблюдение за
обычным действием, не отправка скрытых запросов и не изменение controls сайта.
Полный источник условий остаётся прежним: валидный POST IAA / URL Copart,
успешный ответ и готовая выдача. Неприменённое изменение, failed/старый ответ
не могут разрешить сохранение. Encar, API и формат черновика не менялись.

RED: первый тест обычного click прошёл и отмечен как baseline (порядок событий
зависел от тайминга), а полный baseline дал8 pass/1 fail: повторные same-value
input/change после применённого запроса отключали подтверждение. После owning
fix —27 pass; затем добавлен сценарий двух разных checkbox с одинаковым true.
Он сначала проверял панель раньше следующего settle: диагностика показала
completed=True при SELECTING, а не потерянный запрос. Ожидание привязано к
обработанному состоянию с конечным сроком, проверка обоих условий сохранена.

Итог:28 browser fixtures passed (в том числе10 новых checkbox cases), backend729
passed /49 opt-in skipped и3 прежних reflection warnings; Node112 passed /
1 PowerShell skipped; TypeScript и locked offline Cargo check passed.
Исходный диагностический пример отдельно повторён:6 вариантов событий теперь
сохраняют snapshot и can_use. Артефакты RED/baseline/диагностики/GREEN:
`work/checkbox-selection-fix-20261016.F8REv3BD/`.

Это не доказательство текущего live IAA. Создание/повторный поиск по IAA preset
по-прежнему требуют restore; принудительного включения кнопки без условий нет.

Для пользовательской пробы собрана отдельная macOS arm64 preview0.4.1:
`work/macos-artifact/checkbox-apply-20261016.LRjW0qkU/`. Nuitka/Tauri, deep/strict
локальная подпись, packaged resources/SQLite/Chromium/bootstrap/shutdown,
compiled navigation/timezone (2 passed) и чтение черновика/CSRF/IAA gate прошли.
Это compiled HTTP/resource evidence, не живой/native GUI прогон. Работающая
предыдущая .app и пользовательские данные не изменялись. Запускать новой
командой только после полного выхода из предыдущей версии; commit/push нет.

## IAA: сохранённые условия → проект → повторный поиск — 17.10.2026

Ветка `codex/multisource-auctions`, только изолированный source-test режим.
Предыдущий безусловный IAA create-gate снят **для выбранного поиска с native
RequestUri**. Общая `/Search` вместо условий не принимается. Старому черновику
без native ссылки нужно повторно открыть выбор, применить условие и дождаться
выдачи; ручное копирование JSON пользователем не требуется.

Реализовано:

- Захват штатной ссылки после успешного поиска/готовой выдачи; сохранение
  публичных criteria, summary, revision и стабильного времени захвата.
  Подтверждение панели и обычное закрытие окна сохраняют одинаковый preset.
- Server-owned `Project.search_config`, SQLite12/Alembic0021 и новая
  owner/source/search_identity уникальность. Старые URL-проекты сохраняют
  URL identity; IAA criteria identity исключает курсор/размер страницы и
  analytics metadata, но не фильтры, сортировку, ZIP/статусы/рекомендации.
- Независимая копия config в ручной/плановой очереди; legacy jobs совместимы.
  Worker передаёт frozen config модулю. Более новая конфигурация проекта
  не даёт старому поиску исключить ранее сохранённые лоты.
- IAA native URI → первая страница → native sort/page-size controls → проверка
  фактически отправленных условий перед каждой принятой страницей. Только
  доказанный full-readiness разрешает чтение; restore-readiness его не заменяет.
  Положительно подтверждённый total0 с успешным совпавшим запросом допустим
  даже без кнопки первой страницы. Out-of-range при total>0 не считается пустотой.
- Неизвестные поля, потерянные фильтры, игнорируемый page size, сбой/отмена,
  блокировка/429 не разрешают general fallback/ложный complete/исключение старых
  лотов. Guards освобождают listeners; CAPTCHA автоматически не решается.
  Encar UI и URL-путь, Copart URL/пагинация и общая очередь сохранены.

### TDD и расследованные промежуточные сбои

Функциональный RED подтвердил отсутствие finalize/config/scope/migration.
Отдельные RED: конфликт двух разных presets с одним native URL; зависание на
server-rendered grid без готового pager; URI с курсором3; незамеченный отказ
сайта применить page size; исключение лотов при новой конфигурации проекта;
невозможность подтвердить настоящий пустой поиск без page-one control.
После исправлений GREEN, проверки условий и сохранности данных не ослаблялись.

Первый запуск pytest без PYTHONPATH был ошибкой окружения, не RED.
`scan_id` в новом тесте оказался неверным ожиданием: API возвращает `id`.
Полная регрессия выявила устаревшие ожидания schema_version11 и выбор всей
нынешней ORM-модели из исторической схемы до миграции; проверки переведены на
соответствующую версию/старые колонки, прежние значения/rollback продолжают
сравниваться. Дублирующий UI error key исправлен после TypeScript диагностики.

### Доказательства на финальном коде

- Backend: **740 passed /63 opt-in skipped**,4 известных SAWarning reflection
  expression-based индекса converter (включая новый converter-тест).
- Targeted selection/restore: **48 passed**, из них42 bundled-browser fixtures
  и6 проверок данных/API/очереди. Все обращения к сайтам в fixtures перехвачены.
- Migration/backup: отдельно5 passed — v11→12, ID/relations/FK/index/trigger,
  проверенный pre-migration backup, rollback схемы/ledger, Alembic-equivalent,
  preset backup/restore и read-only conversion.
- Node: **112 passed /1 PowerShell skipped**; TypeScript и desktop frontend
  build passed. Прежние предупреждения node_modules sync/chunk size сохранены.
- Rendered frontend: **12 passed**, включая native IAA preset → create → одна
  команда очереди, no-link gate, Copart, ошибки, preview, RU/UK и1280/800/360px.
- Отдельно live: в собственных тестовых профилях доказаны native URI перенос,
  A/закрытие/B/A и сохранение фактических условий при нескольких страницах.
  Продуктовый collector прошёл **4 страницы /82 лота (25+25+25+7), complete=true**.
  Ни рабочая БД, ни Telegram не использовались; это не82 сохранённых машин.

Артефакты: `work/iaa-restore-20261016.2ocSyXEg/`: XML RED/GREEN/regression,
`live-collector.json`, `uri-roundtrip.json`, `ui/results.json` и screenshots.

**Не проверено/не включено:** установленное приложение macOS/Windows с этой
правкой, новая .app, реальный PostgreSQL-сервер, все возможные комбинации IAA,
production access и Telegram. Multi-sort/geo или иной потерянный URI параметр
без подтверждённого native UI-пути останавливают поиск. Редактирование пресета
у существующего проекта — следующий отдельный UI/API срез, не простая URL-правка.
Нет commit/push/релиза; рабочие пользовательские данные и приложения не заменялись.

## Copart URL-only и скрытое чтение после подтверждения — 17.10.2026

Пользователь уточнил контракт: Copart сохраняется по публичному URL результатов,
IAA — по полным параметрам обычного поиска и native URI. Copart capture больше
не ожидает готовность Classic-таблицы и не привязывает состояние URL к input/change
или необязательным XHR. Сохраняется прошедшая строгую проверку ссылка с criteria;
tracking-параметры по-прежнему исключаются. Отсутствующие в ссылке UI-изменения не
сохраняются и не выдаются за сохранённые: RU/UK панель и help объясняют границу.
Ошибочный document, чужой/невалидный URL, CAPTCHA и видимое consent не разрешают
подтверждение. Закрытие окна сохраняет последний успешно захваченный URL.

Оба collector по-прежнему стартуют скрыто независимо от общего headless setting.
Теперь ручное подтверждение до начала чтения заканчивается закрытием видимого
окна и повторным открытием той же ссылки в headless на том же собственном профиле.
Никакие данные из видимого окна не подставляются вместо проверенного результата.
IAA request listeners переживают замену контекста: native rewind/sort/page size
и сравнение полного scope выполняются заново. После CAPTCHA→автоматического
отказа от необязательных cookies возврат в фон также обязателен. Deadline consent,
отмена, rate limit и запрет sold/exclusion при partial не ослаблены.

### RED и диагностика промежуточных проверок

- Новый routed-browser тест:5 функциональных падений — валидная Copart search
  ссылка без Classic grid не становилась доступной;4 небезопасных случая уже
  корректно блокировались. Новые unit проверки visibility:4 падения — после
  ручного согласия обе страницы читались в видимом контексте.
- Отдельный RED CAPTCHA→cookie reject подтвердил пропущенный ранний return,
  сохранявший видимое чтение. Исправлен через общий resume_background.
- Старые visibility fixtures предполагали всего два запуска контекста;
  после проверки причины дополнены третьим headless контекстом. Тест второго
  CMP моделирует повтор после возврата в фон и сохраняет единый deadline.
- Новая проверка текста сначала читала innerText Shadow DOM host, а не section;
  исправлен локатор, не продукт. Старый Copart unfinished-input fixture зависал,
  поскольку ожидал блокировку, отменённую новым URL-only контрактом. Остановлен
  только собственный pytest SIGINT, сохранён XML; fixture теперь проверяет,
  что typed value вне URL не меняет сохранённую ссылку. IAA unfinished проверки
  не менялись и продолжают запрещать подтверждение до успешного запроса.

Артефакты: `work/copart-url-background-20261017.NvtyIlGj/`: RED XML,
`background-final.xml`, `background-browser.xml`, `selection-green.xml`,
`backend-final.xml`, `browser-final.xml`. Полная frontend Node regression,
TypeScript и Vite build проходят; существующие node_modules/chunk warnings
не скрываются. Браузерные проверки выполняются на перехваченных локальных
фикстурах, не являются живой проверкой Copart/IAA или установленного GUI.

Финальные результаты: backend **749 passed /74 opt-in skipped**,4 прежних
reflection SAWarning; targeted selection/restore **59 passed** (53 реальных
Chromium fixtures и6 unit/API/queue проверок); Node **112 passed /1 PowerShell
skipped**, TypeScript и desktop frontend build passed. Финальная backend suite
включает35 visibility/pacing/cancellation checks, в том числе вложенный cookie
reject после challenge. Отдельный actual Chromium переход с IAA guard проверен
на совпадающих и потерянных условиях; видимость в тесте записывается по launch
options, физически все тестовые окна запускаются headless.

Не выполнены новая native сборка, live приёмка, Windows, commit/push/deploy/release.
Рабочие приложения, базы, ключи и профили браузера пользователя не изменялись.

## IAA: новый выбор и повторная проверка сайта — 17.10.2026

Причина на границе браузера: отдельный draft_id не сбрасывал SearchState IAA
в собственном persistent profile. Новый выбор ждёт успешный initial request и
готовый grid, один раз нажимает штатный Clear All Filters и инвалидирует старый
candidate до нажатия. Сброс не удаляет cookies, лицензию, логин или чужие
preferences. Ошибка сброса не подтверждает старый запрос; закрытие окна в этот
момент также не делает его пригодным для create. Reopen того же snapshot не
сбрасывает условия проекта. После initial reset последующие изменения пользователя
обрабатываются прежним request/response/DOM механизмом.

Второй дефект: после ручной проверки collector закрывал verified context и
заново открывал URL headless. Новый challenge сразу попадал в уже использованный
ACCESS budget и завершал поиск CAPTCHA с нулём страниц. Теперь после проверки
та же страница сворачивается штатным Browser.setWindowBounds; состояние окна
проверяется через Browser.getWindowBounds. Нет дополнительного GET, переноса
challenge cookies или подмены fingerprint. Native IAA guard продолжает проверять
фактический запрос перед принятием строк. Обычный поиск без challenge по-прежнему
стартует headless. Повторное consent восстанавливает то же минимизированное окно.
Ошибка сворачивания явно останавливает обход, а не включает видимое чтение.
Mid-pagination partial/restart, cancellation,429, listeners/lease cleanup и общий
consent deadline сохранены.

RED: новый routed fixture с сохранённым старым поиском дал3 функциональных
падения (reset отсутствовал); resume был baseline pass. Первая попытка fixture
не включала isolated source-test descriptor и падала по InvalidSourceUrlError;
это исправление fixture, не функциональный RED. Unit проверки сохранения окна
падали из-за отсутствующего hide_verified. Старые visibility tests затем
адаптированы к новому контракту:2 context launch вместо3, CDP minimize вместо
повторного GET, реальные deny/429 после minimize вместо искусственного reload.
Проверка парсинга запрещает принимать строки до сворачивания manual window.

Проверки: полная backend suite с RUN_SOURCE_SELECTION_BROWSER=1 — **821 passed /
21 opt-in skipped**,4 прежних reflection warnings. Отдельные реальные routed
Chromium tests (capture/checkbox/fresh selection/guard) — **34 passed**. Node —
**112 passed /1 PowerShell skipped**, TypeScript passed. Финальный unit прогон
добавляет случаи ignored minimize и скрытой кнопки reset; XML в
`work/iaa-reset-session-20261017.Tqd2RFjf/`.

Браузерные тесты используют локальные ответы. Native window CDP выполняется в
тестовом Chrome с headless=true; это не доказательство установленного macOS GUI,
живой доступности IAA или Windows. Пользовательские БД/профили не изменялись.
Новая .app, commit/push/deploy/release в этом срезе не выполнялись.

## Повторная приёмка IAA: headed macOS, 17.10.2026

Предыдущая проверка не доказала работу окна на macOS. Скриншот пользователя
SOURCE_BACKGROUND_BROWSER_UNAVAILABLE подтвердил запуск нового кода, а не старой
сборки. Прямой probe настоящего Chrome(headless=false) на локальной странице
воспроизвёл: setWindowBounds(minimized) принят, после20 polling bounds=normal.
Попытка переместить окно за экран ограничена macOS и не скрыла его полностью;
AppKit hide/unhide собственного CDP browser PID вернул false. Эти варианты не
включались в продукт; личные Chrome windows не управлялись.

Пользователь явно согласовал fallback: если автоматическое скрытие недоступно,
продолжать проверенную сессию с предупреждением и ручным сворачиванием. Collector
подавляет только SOURCE_BACKGROUND_BROWSER_UNAVAILABLE в этой точке. Сохраняет
browser_notice=MANUAL_MINIMIZE в progress/history, UI RU/UK показывает notice.
Отказ доступа, повторный challenge,429, close/cancel и guard errors остаются
ошибками. Нет reload/смены режима/переноса challenge cookies ради продолжения.

Первый reset также ошибочно требовал _completed initial POST. Реальный SearchState
может прийти server-rendered; без POST prepare_fresh никогда не выполнялся.
Теперь reset ждёт готовый DOM/отсутствие in-flight source requests, а не наличие
начального POST. Dirty выставляется до штатного Clear All Filters, snapshot
подтверждается только успешным новым запросом/выдачей. Server-rendered fixture
без initial POST дал RED (zero captured requests), после исправления GREEN.

RED также проверен для unsupported minimize collector и RU/UK warning. После
исправления7 тестов в настоящем headed Chrome проверяют reset/server-rendered,
reopen/error/manual wait и criteria guard. Ещё1 headed Chrome integration читает
2 страницы с2 различными авто после simulated verification и реального отказа
minimize, затем сохраняет2 ProjectCar в отдельной тестовой БД. Это более сильное
native-window доказательство, но ответы сайта всё ещё локальные.

XML и диагностические probes: `work/iaa-native-window-20261017.WTRkPG65/`.
Node113 passed /1 skipped, TypeScript passed. Пользовательские app/БД/профили не
изменялись. Live IAA/Copart и Windows не проверены.

Дополнительная live-проверка этого среза: новый выбор IAA в отдельном ранее
созданном агентом public-browser-profile сначала вернул82 авто. После native
Clear All Filters product observe получил366986 авто и публичные условия
Searches=[], ZipCode="",miles=0,can_use=true. Значит, reset проверен не только
на фикстуре. `live-reset.log` не содержит cookies/headers/данных доступа.
Отдельный ограниченный headless collector smoke остановлен при запросе ручного
подтверждения до чтения; защита не обходилась, live full-search не объявлен
успешным. Фактический результат и причина — `live-search.log`.

Полная backend suite после исправлений:825 passed /21 opt-in skipped,4 прежних
reflection warnings. Headed native tests:7 reset/guard +1 two-page/storage.

После отдельного явного согласия пользователя выполнен live manual fallback
того же product collector: исходный headless сообщил CAPTCHA, открыл собственное
видимое окно, затем продолжил ту же проверенную страницу с MANUAL_MINIMIZE.
Результат: **4 страницы /82 уникальных авто из82, complete=true**. Native guard
проверял сохранённые условия; окно закрыто только по завершении полного обхода.
Отчёт: `live-search-manual.log`. Никаких CAPTCHA/cookie controls автоматикой
не нажимали, данные доступа не переносили, пользовательские проекты/БД не писали,
Telegram не использовали. Это live smoke одной выбранной конфигурации, не
гарантия всех фильтров/профилей IAA и не установленный .app smoke.

## Отдельный черновик проекта и native поле Copart — 17.10.2026

Диагностика: у Project уже был собственный search_config, но SearchSelections
хранил один черновик на owner/source; UI query key тоже не различал новые формы.
Из-за этого создание следующего проекта показывало прошлые условия. Новая форма
теперь получает random draft_id (32 lowercase hex), который проходит через query
key, GET/POST selection, путь файла, in-process session и search_draft_id в create.
Owner/source isolation сохраняется. Неподходящий draft_id/selection_id не может
подтвердить или создать проект; параметры копируются в Project, а не остаются
ссылкой на изменяемый черновик. БД менять не требуется, legacy API/файлы без
draft_id продолжают работать. Пользовательские старые черновики не удалялись.

Повторный Copart отказ: подтверждён путь последней запущенной preview, затем
read-only извлечены только публичные lotSearchResults criteria из session records
собственного SearchCar профиля, не личного Chrome. Никакие cookies/headers/auth
данные не читались и не переносились. Наблюдённые Nissan Frontier ссылки содержат
`vanityKeyword:false` (bool); строгая CopartCriteria выдавала extra_forbidden.
Добавлен явный bool field с сохранением через canonical URL/envelope. Новый
параметр не выбрасывается; unknown fields остаются запрещены. При неизвестной
схеме координатор теперь показывает SEARCH_FILTERS_UNSUPPORTED вместо общей
LAST_CHANGE_UNCONFIRMED во время выбора. Ничего на живой странице не нажимали.

RED:7 новых scope-тестов подтвердили отсутствие draft-aware API/session/files;
ещё2 теста native Frontier URL падали именно на vanityKeyword extra_forbidden.
После реализации scope checks11 passed, включая точное URL-кодирование ссылки
пользователя (служебный qId заменён fixture), HTTP owner/source/draft routing,
пустую вторую форму, несовпадающий selection и неизменность первого Project.
Настоящий frontend с API fixtures:13 passed, включая создание первого проекта
и открытие второй формы с пустыми условиями/другим draft_id. Скриншот
`ui/new-project-empty-filters.png` просмотрен; ширины1280/800/360 и RU/UK сохранены.
Node112 passed /1 PowerShell skipped, TypeScript и Vite build passed.

Полная backend-регрессия на финальном продуктовом коде:759 passed /76 opt-in
skipped,4 прежних SAWarning reflection. Targeted browser/restore61 passed
(55 routed Chromium fixtures и6 unit/API проверок). После получения полной
пользовательской ссылки отдельно подтверждено: vanityKeyword:false и весь
MAKE/MODL/YEAR сохраняются, qId исключается; дополнительных live-запросов нет.

Артефакты RED/GREEN/JUnit и UI: `work/project-selection-scope-20261017.1dSnqEli/`.
Не выполнены новая native сборка, live/native GUI приёмка, Windows, commit/push
или публикация. Рабочие данные, ключи, запущенное приложение и профили не менялись.

## Фоновое чтение и повторное использование браузера — 18.10.2026

Ветка `codex/multisource-auctions`. Обнаружены три причины лишнего окна:
немедленная эскалация transient challenge, уничтожение runtime после каждой
job и повторный headless relaunch проверенного headful контекста на следующем
проекте. Сам IAA guard/штатное восстановление URI/POST могут работать скрыто.
План реализации — дополнение18.10 в MULTISOURCE_IMPLEMENTATION_BACKLOG.md.

Реализовано без новых зависимостей:

- Один пассивный бюджет8сек для обнаруженной промежуточной проверки в скрытой
  странице. Не выполняются reload, API replay, CAPTCHA clicks, spoof/stealth,
  перенос cookies между контекстами. Голый403 не игнорируется,429 и отмена
  немедленно останавливают поиск. Критерии/полнота/partial сохранены.
- Worker-thread-owned BrowserRuntimePool: owner/source/options, до2 idle
  runtime/10мин, без передачи sync Playwright между потоками. Между jobs
  переход на about:blank останавливает таймеры/опрос старой страницы; следующая
  job всегда применяет собственный frozen URL/config/guard. Idle-профиль
  уступается выбору фильтров; active scan не закрывается другим потоком.
  Selection ждёт до30сек с отменой, запрос имеет короткий приоритет на lease.
- Закрытый browser/смена owner/options/TTL/shutdown освобождают runtime. Перед
  Encar/непереиспользуемым source idle-кэш закрывается, прежний бюджет detail
  browsers не расширяется. Очередь остаётся последовательной.
- После проверки тот же контекст сворачивается без relaunch. macOS fallback
  MANUAL_MINIMIZE сохранён; окно не объявляется скрытым при отказе ОС.
  Восстановление свёрнутого окна без relaunch не сбрасывает native page cursor;
  реальный relaunch после прочитанных страниц по-прежнему оставляет partial.

Доказательства:

- RED: transient IAA/Copart открывали2 browser вместо1; worker открывал2
  контекста на2 последовательные jobs; idle runtime не останавливал страницу.
  После реализации GREEN. Дополнительный RED при проверке безопасности
  подтвердил, что readable DOM не должен отменять bare403; gate исправлен.
- Targeted backend:154 passed /2 live opt-in skipped. Включены отмена/429,
  transient между страницами, repeated jobs, profile handoff, TTL/лимит,
  owner/options/closed/shutdown, cursor после повторной ручной проверки.
- Настоящий установленный headless Chrome с routed страницами: два проекта
  с разными ID, по2 страницы, один browser launch(headless=true), без interaction
  и внешних запросов. Park=about:blank проверен. Вместе с native guard/reset
  fixtures —8 passed. Это не доказательство live-доступа сайта.
- Настоящий headed macOS Chrome, routed IAA:2 страницы/2 авто после тестового prompt,
  затем повторный поиск в той же сессии, без третьего запуска, и storage2
  ProjectCar в disposable БД —1 passed. Это не установленный GUI/live smoke.
- Live IAA cold headless smoke (до4 страниц/50сек) после пассивного ожидания
  снова потребовал CAPTCHA. Probe остановлен до открытия окна, защиты не
  нажимались;0 страниц/0 авто, НЕ успешный full-search. Повторный live-поиск
  после реальной ручной CAPTCHA этим срезом пока не подтверждён.

Первый полный sandbox-прогон:778 passed /83 skipped, два localhost smoke
упёрлись в запрет локального порта. После разрешённого localhost —780 passed
/83 skipped. Расширенный RUN_SOURCE_SELECTION_BROWSER=1 выявил регрессию
bare403 в fake-page path (844 passed /22 skipped /1 failed); исправлено без
изменения этого теста. Финальный результат полного прогона записан ниже.

Финальная полная backend suite с RUN_SOURCE_SELECTION_BROWSER=1:
**846 passed /22 opt-in skipped**,4 прежних SAWarning reflection, без падений.
JUnit: `/tmp/searchcar-background-acceptance.ueroLs7L/backend.xml`.
Повтор native macOS fixture на финальном коде:1 passed,
JUnit: `/tmp/searchcar-native-background-acceptance.DQm8Vpw8/native.xml`.
`git diff --check` и compileall затронутых backend modules — passed.

Новая .app не собиралась, приложение пользователя не заменено. Нет новых
версий, commit/push/release/server изменений. Windows, установленный GUI и
полный live Copart не проверены; холодный IAA может требовать ручную CAPTCHA,
а macOS может потребовать ручного сворачивания. Это известные ограничения,
а не обещание полностью безоконного доступа к защищённому сайту.

### macOS preview этого среза — 18.10.2026

По отдельному запросу пользователя собрана0.4.1 arm64 из текущего worktree
ветки `codex/multisource-auctions`, без version bump/публикации/server изменений.
Артефакт: `work/macos-artifact/background-search-20261018.xMCWEcRC/`.
Старая копия/данные не заменялись; рядом с .app есть отдельный запускатель
Open SearchCar Auctions.command, включающий существующий source-tests профиль.
Fingerprint нового backend:`356942ed0f2a9260`; compiled browser_runtime_pool
подтверждён отчётом Nuitka. Node113 passed /1 PowerShell skipped, tsc/Vite и
Rust3 tests passed. Nuitka/Tauri сборки прошли. Подписанная ad-hoc .app прошла
deep/strict codesign, SQLite/FK/WAL, bundled Chromium, защищённый bootstrap,
штатный shutdown и compiled navigation/time2 passed на disposable данных.
Packaged backend SHA256:`793f0887f7cf1625def5687ff5e99e8ffc95757f2001b6fb1c49550c0b5caad4`.
Сборка не notarized/updater-signed. Installed GUI и Windows не проверены;
live CAPTCHA/manual-minimize ограничения остаются. ZIP не содержит smoke БД,
cookies, логов или браузерных профилей.

## Закрытие окна IAA после задания — 18.10.2026

Отдельный узкий запрос пользователя после диагностики about:blank.
Изменены только policy хранения оконного runtime IAA и её применение общим
pool: `IAASource.reuse_windowed_browser_session=False`,
`AuctionBrowserSession.has_window` учитывает interactive и minimized,
release немедленно закрывает такой runtime до park. Обычный headless IAA
остаётся в кэше, Copart сохраняет прежнюю политику. Worker/search flow,
CAPTCHA detector, выбор фильтров, лицензии и данные не менялись.

RED: три проверки оставляли оконный IAA в кэше вместо немедленного закрытия;
две baseline проверки headless/default policy уже проходили. После правки
targeted regression171 passed /16 opt-in skipped, включая очередь, partial,
отмену, завершение, профиль и восстановление условий. JUnit:
`/tmp/searchcar-iaa-close-regression.qYE1D0Bv/regression.xml`.

Отдельно2 actual Chrome fixture tests прошли: настоящий headed macOS Chrome
закрыл страницу и отключил свой browser после release, после успешных двух
обходов и записи2 авто в disposable БД; другой тест подтвердил сохранение
одного headless контекста для двух разных проектов. Никаких live источников,
личного браузера или пользовательской БД эти тесты не использовали. JUnit:
`/tmp/searchcar-iaa-close-native.fDXBfk4V/native.xml`.

`git diff --check` passed. Новая .app/публикация/commit/push не выполнялись;
запущенное приложение пользователя не заменялось. Появление окна во время
IAA поиска остаётся прежним и не объявляется исправленным этим изменением.

## Распознавание ручной проверки IAA — 18.10.2026

Следующий ограниченный срез MSA-01-T по запросу продолжить план. Исправлен
доказанный ложный сигнал в IAASource.dom_script: скрытый служебный
`_Incapsula_Resource` без таблицы раньше выставлял manual_required/blocked и
запускал headful fallback. Проверяются видимость iframe/предков, размер
содержимого без рамки и положение; явная инструкция проверки отличается от
простой надписи reCAPTCHA. Видимый challenge распознаётся также поверх старой
таблицы. Отказы Access Denied/Pardon Our Interruption/Request unsuccessful и
Security check без ручного контрола не превращаются в успешный поиск.

RED на установленном headless Chrome с полностью перехваченными страницами:
11 failed /14 baseline passed для DOM-классификации;2 collector-сценария
пытались открыть headful Chrome вместо фонового ожидания/ошибки таймаута.
Дополнительный RED подтвердил случай1px iframe со стандартной рамкой Chrome:
внешняя рамка больше1px, но клиентская область1px; проверка исправлена на
content size. Список условий/проектный guard, очередь и политика закрытия окна
не менялись. Нет новых зависимостей, API replay, CAPTCHA clicks, stealth,
переноса cookies или обхода отказа/429.

GREEN на окончательном коде:252 passed /2 live opt-in skipped, включая31 новую
Chrome DOM/collector проверку, выбор/сброс/подтверждение фильтров, IAA guard,
пагинацию/partial, отмену/429, pool/release, очередь и shutdown. Два новых
collector-сценария: служебный iframe дожидается выдачи с одним headless launch
без interaction; незавершившаяся загрузка даёт SEARCH_PAGE_NOT_READY, а не
пустой complete. JUnit:
`/tmp/searchcar-iaa-classifier-final.s1iJV5bs/regression.xml`.
Отдельный `SEARCHCAR_COOKIE_BROWSER_SMOKE=1`:14 passed — штатные reject-кнопки
обоих источников, сохранение выбора, отсутствие кликов по accept/unknown/
скрытым/дублированным контролам и при настоящем prompt. JUnit:
`/tmp/searchcar-iaa-cookie-final.d6QtVp7g/cookies.xml`.
compileall и git diff --check прошли. Пользовательские данные не использовались.

Это fixture-доказательство устранения конкретного ложного сигнала, не
подтверждение, что именно он сработал в последнем пользовательском scan, и не
гарантия безоконного доступа к live IAA. После исправления необходимы повторный
live IAA/Copart и приёмка в новой native preview/Windows. MSA-01-T остаётся
IN_PROGRESS, production gate не снят. Новая .app, commit/push/release/server
изменения не выполнялись; пользовательские приложение/данные не заменялись.

## Повторные поиски сохранённых проектов — 18.10.2026

Ветка `codex/multisource-auctions`, продолжение MSA-01-T. Добавлен
`backend/tests/test_auction_project_repeat_browser.py`, не изменён продуктовый
код. Для IAA и Copart проверяется один и тот же полный путь на disposable БД:
два независимых закрытых selection draft → create API function → сохранённый
Project.search_config → frozen ScanRun plan → настоящий worker/collector →
две отрисованные страницы → сохранённые Car/ProjectCar. Выполняются A→B→A в
одной browser session и повторA после полного закрытия pool/Playwright.

В каждом поиске26 уникальных датированных авто, у двух проектов52 независимых
ID. Состояние предыдущего поиска реально остаётся в localStorage/owned profile,
но не заменяет config проекта. Проверены одинаковый профиль после restart,
один headless launch для первых3 jobs, второй после restart, отсутствие
видимых окон/дублей/перезаписи config и окончательное закрытие всех contexts.
IAA guard проверяет native POST каждой страницы; Copart fixture моделирует
известную потерю критериев DataTables Next, которую действующий preXhr handler
предотвращает. Реальные сайты/права лицензии этим fixture не проверяются:
все ответы перехвачены локально, entitlement mock действует только в тесте.
Паузы в fixture обнулены, отдельные pacing-тесты не заменяются.

Первый baseline: Copart passed; IAA не сохранил авто, поскольку новый fixture
не содержал Sale List URL, необходимого существующему parser для года торгов.
Это дефект fixture, НЕ RED продуктового поведения. Добавлена исходная ссылка,
не ослаблено ожидание26 сохранённых ID. Отдельный повтор:2 passed,
`/tmp/searchcar-project-repeat-check.eXzhLA8y/repeat.xml`.

Финальная регрессия:68 passed с включёнными Chrome fixtures — новые повторы,
draft isolation, IAA restore, runtime pool, общая очередь и search_config
миграция/backup/converter. Дополнительно проверены FOUND/tracking_enabled
отношения и AVAILABLE авто обоих проектов: запускB не исключает результатыA
и прошедшая дата сама по себе не создаёт SOLD. Один прежний SAWarning
converter reflection expression index. JUnit:
`/tmp/searchcar-project-repeat-final.7atBWD1k/regression.xml`.
compileall нового теста и git diff --check прошли.

Ограниченные живые smoke в отдельных одноразовых профилях:

- IAA: публичные условия тестовых проектов BMW X2 / Ducati. Первый A остановлен
  перед ручным fallback: iframe `/_Incapsula_Resource`,1280×800, visible,
  manual_required/blocked=true. Это не скрытый1px iframe, исправленный ранее.
  Проверенных страниц/авто0; остальные раунды не запущены.
- Copart: публичные условия Chevrolet Tahoe / Audi S5. Первый A остановлен
  на Google CMP «COPART asks for your consent…», consent_required=true,
  manual_required/blocked=false. Проверенных страниц/авто0; следующие раунды
  не запущены. Наблюдаемый заголовок без фильтров до завершения CMP не принят
  за завершённую выдачу или доказательство потери условий.

Исходная source-tests БД открыта mode=ro, скопированы только публичные
URL/search_config в одноразовое хранилище. Ни рабочие проекты, ни исходные
profiles/cookies не менялись/не переносились. Probe прерывается до открытия
видимого окна без согласованного участия пользователя; CAPTCHA не нажимается,
важные условия не принимаются, отказ не обходится повторными запросами.
Отчёты: `work/auction-repeat-20261018.Nrj98EL3/iaa-background/report.json` и
`copart-background/report.json` там же. Это source-runtime smoke, не GUI/worker
live-приёмка; complete=false в обоих отчётах.

Для следующего live шага запрошена готовность пользователя пройти проверку
в отдельном окне. До её завершения нельзя объявить live A→B→A/restart принятым.
Новая .app/Windows, production gate, commit/push/release/deploy этим срезом
не выполнены. Установленное приложение и пользовательские данные не заменялись.

### Живой A→B→A и повтор после закрытия браузера — 19.10.2026

Пользователь явно согласился пройти ручную проверку. Существующий
`work/auction-repeat-20261018.Nrj98EL3/repeat_probe.py` запущен с `--manual`
последовательно для Copart и IAA. Это ограниченная source-runtime приёмка
текущего кода ветки `codex/multisource-auctions`, не installed GUI/worker smoke.
Исходная source-tests БД открыта `mode=ro`; только публичные URL/config проектов
скопированы в отдельные временные SQLite и собственные browser profiles.
Пользовательские данные/браузерные профили/Telegram не изменялись; продуктовый
код, лицензирование и production-доступ не менялись.

| Источник / условия | A | B | Возврат A | A после закрытия browser/driver |
| --- | --- | --- | --- | --- |
| Copart: Chevrolet Tahoe 2025–2027 / Audi S5 2021–2027 | 49/49, 3 стр. | 29/29, 2 стр. | 49/49, 3 стр. | 49/49, 3 стр. |
| IAA: BMW X2 2022–2027 / Ducati | 22/22, 1 стр. | 105/105, 2 стр. | 22/22, 1 стр. | 22/22, 1 стр. |

Все восемь результатов `SUCCEEDED`, оба отчёта `complete=true`. Каждый проход
сверяет число уникальных прочитанных ID с объявленным total, неизменность
исходного config; A/B используют разные hashes и непересекающиеся ID. Отдельно
проверено, что множества ID первого, повторного и послеперезапускового A совпали
у обоих источников. Это число **прочитанных**, не записанных в рабочие проекты
лотов; данный probe не проверяет worker storage и отбор датированных машин.

Copart на первом A потребовал cookies; после их завершения B/A прошли в той же
проверенной сессии без нового interaction. На macOS сохранился прежний warning
`MANUAL_MINIMIZE`: окно переиспользовалось/парковалось до закрытия pool, а не
объявлялось закрытым после каждого release. После полного закрытия browser/driver
повтор A прошёл headless без нового окна/interaction (7.57 s). Это подтверждает
повтор именно этих условий, не гарантирует отсутствие будущих challenges.

IAA в **каждом** из четырёх раундов обнаружил CAPTCHA и перешёл в ручное окно;
после завершения проверки прочитал весь результат. Все четыре release
подтвердили `session.context is None`; окно закрывалось после каждого задания.
`MANUAL_MINIMIZE` не останавливал проверенное чтение, но полностью фоновый IAA
этой приёмкой **не подтверждён**. Сохранённого browser profile оказалось
недостаточно, чтобы новый headless context не получил повторную проверку.
Защита не обходилась, автоматические нажатия CAPTCHA не выполнялись.

После завершения обоих процессов отдельный `ps` не обнаружил Chrome/driver
процессов с путями двух test profiles. Исходные отчёты с ID/config hashes и
interaction каждого раунда:
`work/auction-repeat-20261018.Nrj98EL3/copart-manual/report.json`,
`work/auction-repeat-20261018.Nrj98EL3/iaa-manual/report.json`.
Остаются native GUI/Windows gates, live worker/storage и ограничение повторных
ручных окон IAA. Новая .app, commit/push/release/deploy не выполнялись.

### IAA: повторный поиск в проверенном browser без открытого окна — 19.10.2026

Причина подтверждена сравнением с предыдущей живой приёмкой: освобождение
оконного IAA уничтожало browser/context и создавало новый headless runtime
на каждом поиске. Возврат старой policy оставил бы открытое пустое окно.
Выбран не этот возврат, а скрытая страница в **том же** owned browser/context.

Локальный эксперимент `work/iaa-hidden-session-20261019.7an6BFea/probe.py`
проверил Chrome155.0.8059.80: скрытая страница имеет type `other`, без opt-in
Playwright её не подключает. С `PW_CHROMIUM_ATTACH_TO_OTHER=1` она читает DOM,
browser остаётся подключён после закрытия видимой страницы,
`Browser.getWindowForTarget` сообщает `Browser window not found`, обычное окно
снова открывается в той же сессии. Probe использовал только локальный HTML.
Источники API: [CDP Target.createTarget](https://chromedevtools.github.io/devtools-protocol/tot/Target/#method-createTarget),
[upstream Playwright opt-in](https://github.com/microsoft/playwright/issues/14876).
Параметр experimental: поддержка проверяется фактическим созданием, не только
версией. AppKit probe вернул hide=false/unhide=false; этот путь не включён.

Изменения:

- Driver environment helper включает attach-other только при source-test opt-in;
  глобальный os.environ не меняется. Используется закреплённый driver, без
  патчей его JavaScript, spoof/stealth, переноса challenge cookies или hidden API.
- IAA source отдельно разрешает скрытый reuse, но по-прежнему запрещает idle
  UI-окно. На release создаётся скрытая about:blank page, закрываются прежние
  страницы, retained CDP session удерживает скрытый target. Следующий job
  получает тот же handle и свой frozen URL/config; `park` убирает опрос сайта.
- При unsupported target/timeout/ошибке/оставшейся UI-странице runtime закрывается
  полностью. Старое закрытие сохранено как fail-closed fallback. Повторная
  ручная проверка открывает обычную страницу в том же browser без relaunch;
  это новая page, поэтому challenge после partial сохраняет restart-required.
- Lease/selection handoff, отмена,429, ограничения доступа, FIFO, TTL/лимит,
  owner/options и shutdown остаются прежними. Copart/normal Encar не выбирают
  новую policy. Никаких новых таблиц, миграций, копирования рабочих profiles.

TDD: новые pool/driver tests сначала упали — pool не пытался сохранить скрытый
runtime и driver не подключал target. GREEN после реализации. Дополнительный
RED на ложном «успешном скрытии», при котором has_window остаётся true,
закрыт явной проверкой и полным закрытием runtime. Есть unsupported/error
fallback и проверка отсутствия глобального driver env изменения.

Настоящий native macOS Chrome с локальными routed ответами проверяет:
первый видимый поиск → скрытая страница без native window → другой проект
в том же browser/context → повторный ручной prompt → видимая страница без
browser relaunch → повторное скрытие → окончательный shutdown. Проверены
сохранённый localStorage, отдельные ID, 1 page без окна и ровно 1 browser launch.
Никакая реальная CAPTCHA в fixture не решалась автоматически.

Живой IAA source-runtime smoke с участием пользователя:

| Раунд | Прочитано / reported | Страниц | Запрошено ручное окно |
| --- | --- | --- | --- |
| A: BMW X2 | 22/22 | 1 | Да, CAPTCHA |
| B: Ducati | 105/105 | 2 | Нет |
| Возврат A | 22/22 | 1 | Нет |
| A после полного закрытия browser/driver | 22/22 | 1 | Да, CAPTCHA |

Все раунды SUCCEEDED, config не менялся, A/B ID не пересекались.
Отчёт: `work/iaa-hidden-session-20261019.7an6BFea/live/iaa-manual/report.json`.
Это проверка live collector, не worker storage/installed GUI. Рабочая БД открыта
только mode=ro для публичных условий; данные/профили пользователя и Telegram
не менялись. Полностью безоконный **холодный** IAA не обещается. Во время
первого подтверждённого чтения на macOS возможен прежний MANUAL_MINIMIZE;
новый путь убирает UI при release, а не скрывает запрос проверки от человека.

Новая .app/sidecar не собраны; installed macOS и Windows/Edge требуют проверки.
Production/MSA-01-P остаётся закрыт. Коммит/публикация/деплой не выполнялись.

Регрессия: первый targeted native/browser прогон150 passed; pool/driver/native
повтор42 passed. Расширенный полный прогон с browser/native opt-in:898 passed,
22 skipped и1 failure — старый `test_actual_native_verification_fallback_reads_two_pages_and_two_cars`
после закрытия видимой вкладки требовал `session.context is None`. Причина
отдельно воспроизведена: новая policy намеренно сохраняет context без окна.
Тест не потерял проверку закрытия видимой page и2 страниц/2 сохранённых авто:
добавлены отсутствие native window у retained target, connected browser до
выхода и disconnected browser после pool shutdown. На финальном коде оба
native lifecycle сценария passed (2 passed /1 headless opt-in skipped);
`native-complete.xml`. Старый failed отчёт сохранён как evidence, не объявлен
зелёным. Node:129 passed /1 PowerShell skipped в прогоне без ранее устаревшего
source-text packaging-теста. Compileall/diff-check passed. Все XML находятся
в `work/iaa-hidden-session-20261019.7an6BFea/`.

### Живой worker → storage → повтор проекта — 19.10.2026

Ветка `codex/multisource-auctions`. Следующий срез расширяет предыдущую
collector-only приёмку: штатные create_project/scan_projects, замороженный plan,
атомарный claim_next_job и настоящий worker.process_job с живым collector.
Каждый источник использует отдельную одноразовую SQLite и browser profile;
исходные публичные условия импортированы read-only из предыдущих snapshots.
Два независимых CLOSED draft проходят обычную валидацию создания проекта.
Параллельных внешних поисков нет, лимит10 страниц/240 s на job.

Enforcement required: существующий тестовый signer создаёт временный ключ
в памяти и подписанную lease на2 часа с обоими источниками. Публичный ключ
доверен только этому subprocess через отдельный test config; entitlement
не замокан и не отключён. Production keys/лицензия/license-server не использованы.
Прямой вызов API-функций не доказывает HTTP-auth или установленный GUI.
Scheduler/Telegram не запускались; рабочие БД/профили не менялись.

Оба source preflight прошли подпись/права → draft → create → frozen plan →
durable claim без открытия браузера. Живой IAA завершил весь сценарий:

| Раунд | Прочитано / reported | Сохранено в проекте | Новых | Ручная проверка |
| --- | --- | --- | --- | --- |
| A: BMW X2 | 22/22, 1 стр. | 2 | 2 | CAPTCHA |
| B: Ducati | 105/105, 2 стр. | 19 | 19 | Нет |
| Возврат A | 22/22, 1 стр. | 2 | 0 | Нет |
| A после закрытия browser/driver | 22/22, 1 стр. | 2 | 0 | CAPTCHA |

Все4 jobs SUCCEEDED, pagination.complete=true. Перед каждым claim повторный
scan_projects вернул тот же run_id/created=false:8 команд дали4 jobs.
После каждого job проверены сохранённые IDs, FOUND/tracking_enabled, source,
AVAILABLE, отсутствие изменений соседнего проекта и обоих search_config.
A одинаков до/после restart, ID A/B не пересекаются. Количество сохранённых
меньше прочитанных по действующей политике: BMW20 новых без даты; Ducati85
без даты и1 вне USA. Поле probe `excluded_without_assigned_date` исторически
содержит все не принятые лоты (для Ducati86), не только отсутствие даты;
точное разбиение берётся из pagination.read_summary. Политика не менялась.

Новый read-only connection к завершённой IAA SQLite подтвердил21 уникальное
source+ID авто,21 связь в2 проектах,4 MANUAL jobs, совпадение собственных frozen
plan и Project.search_config,0 FK violations/активных jobs. На повторах new=0,
changed=0. Это уже live worker/storage proof, но не compiled/installed smoke.

Copart первыйA запросил COOKIE_CONSENT, затем CAPTCHA. После190.42 s штатный
manual timeout завершил worker как FAILED/SOURCE_VERIFICATION_TIMEOUT:
0 страниц, complete=false,0 авто. B/возвратA/restart не запускались. Это
**не** подтверждённый live Copart worker/storage gate и не пустой успешный
поиск. Свежий read-only connection подтвердил сохранённый FAILED,2 тестовых
проекта/0 cars/0 project_cars,0 FK violations/активных jobs. Автоповторов и
обхода защиты не было; продолжение требует нового ручного участия.

После завершения отдельный ps не обнаружил процессов Chrome/driver с путями
обоих live test profiles. Скрипты и отчёты:
`work/auction-worker-live-20261019.aMi7FpLH/acceptance.py`, `readback.py`,
`iaa-live/report.json`, `iaa-live/storage-readback.json`,
`copart-live/report.json`; preflight отчёты лежат отдельно в `*-preflight`.
Compileall скриптов и git diff --check прошли. Продуктовый код не менялся;
новые полные regression suites не запускались — предыдущие результаты на
том же продуктовом коде приведены выше. Следующий открытый gate — Copart
worker/storage, затем native macOS/Windows. MSA-01-T остаётся IN_PROGRESS;
production, новая .app, commit/push/release/deploy этим срезом не выполнены.

### macOS preview IAA hidden session и отдельное редактирование фильтров — 19.10.2026

По запросу «собери билд и приступи к следующей задаче» сначала зафиксированы
входы отдельной macOS preview0.4.1 (ветка `codex/multisource-auctions`), затем
начата новая функция. Пакет включает новый список/удаление проектов и hidden
reuse IAA, **не включает последующее редактирование фильтров**. Nuitka начал
с fingerprint `1b9e148c2c729722`; после генерации C новые source-only правки
не попадали в payload. Оболочка/Vite собраны с прежним стабильным UI; готовый
новый sidecar помещён в отдельный staged bundle, затем весь bundle подписан
ad-hoc. Рабочая запущенная копия и данные не заменялись/не останавливались.

Артефакт:
`work/macos-artifact/iaa-hidden-20261019.DvcBcNbZ/SearchCar-macOS-0.4.1-iaa-hidden.zip`
(192 MiB), SHA256 `90c46de33e2a5d5aba51a85a694efd51e6071fe7ae94039c46a40efb26750a6d`.
Именно его подписанные resources прошли SQLite12/FK/WAL, bundled Chromium PNG,
protected bootstrap и graceful shutdown (17 s), compiled navigation/time/profile
3 passed (32 s), deep/strict codesign и ZIP integrity. До новых feature-правок:
backend803 passed /120 skipped, Node129 passed /1 PowerShell skipped при
исключении прежнего packaging text assert, TypeScript/Vite/Rust/Tauri passed.
Два первых sandbox loopback failures прошли при разрешённом localhost без
изменения кода. Не notarized/updater-signed; installed GUI/live/Windows не приняты.
BUILD.md/SHA256SUMS и отчёты рядом с ZIP; внутри только app, не test profiles.

Следующая согласованная задача реализована отдельно в исходниках:
карандаш в списке/плитке Copart/IAA и «Изменить фильтры» на странице проекта;
native dialog со сводкой действующих условий, новым project-bound draft и
выбором на сайте. Новый проект по-прежнему открывает чистый выбор; редактирование
открывает собственную native ссылку/preset без Clear All Filters. Отмена
останавливает только свой selection, не меняя проект и не требуя продлённой
лицензии поиска. Pending OPEN не позволяет закрыть форму до появления session ID.

POST `/projects/{id}/search-selection` проверяет owner/CSRF/origin, source-test
изоляцию/entitlement, CLOSED/can_use snapshot и project-bound draft. Revision
и очередь повторно проверяются под SCAN_COMMAND_LOCK; активный проект,
конфликт revision/уникальности и неподтверждённый выбор оставляют старые условия.
Повышается существующая revision; история/избранное/исключения и соседний
проект сохраняются. Миграций нет. «Сохранить и обновить» после успеха отправляет
одну существующую команду поиска только этого проекта; неподтверждённый enqueue
не называется ошибкой сохранения. Новый frozen plan содержит новые условия.

RED: отсутствие save-route (404), project_id в OPEN (422), edit-action в
AuctionProjects. GREEN:19 новых API-сценариев, в том числе настоящие auth-cookie/
CSRF/origin, отказ прав и отмена при истёкшем entitlement. Новые тесты сначала
ошибочно ожидали исходную revision0, тогда как существующий default1;
исправлена fixture-предпосылка, не продуктовое правило. Финальный backend:
822 passed /122 skipped,4 прежних reflection warnings. Отдельный routed IAA
browser:7 passed, включая legacy URL и saved preset при редактировании.
Node130 passed /1 PowerShell skipped без известного packaging source-text
теста; TypeScript/Vite/ESLint (0 errors/warnings), compileall/diff-check passed.

UI:23/23 финальных сценария на собранном интерфейсе и локальных API/media
fixtures passed. Покрыты сохранение/отмена/Escape/focus, медленный OPEN,
повторное нажатие/pending, optimistic conflict/retry, одно enqueue,
сохранение при queue error, обе площадки/RU/UK/360 px и прежние действия.
Ранние падения сохранились в `work/project-search-edit-20261019-ui*`:
проверки прежнего прямого toolbar button/его меняющегося текста уточнены без
потери проверки disabled/right alignment; fixture GET9 перестал перехватывать
DELETE9. Новый pending OPEN случай привёл к синхронному callback/ref до запроса,
чтобы отмена не оставляла потерянное окно; повторный сценарий passed.
Финальный отчёт/проверенные screenshots:
`work/project-search-edit-20261019-ui-verified/results.json`.
API/browser/XML/Node логи новой функции помечены `edit-*` рядом с артефактом,
но не доказывают её наличие в этом ZIP. Live редактирование/installed .app/
Windows остаются отдельной приёмкой. Production/Telegram, версия, commit/push/
release/deploy не менялись.

### Отмена IAA: скрытая сессия → очередь → следующий проект — 19.10.2026

По запросу следующей задачи выполнен локальный failure-path срез MSA-01-T,
не новый внешний Copart retry. Добавлен
`backend/tests/test_auction_cancel_native_browser.py`:2 параметризованных
opt-in native Chrome сценария, временная SQLite и полностью routed site
responses. Предварительный успешный A создаёт сохранённый лот и скрытый
припаркованный context. Новый A отменяется либо во время повторного ручного
prompt после открытия обычного окна, либо после чтения первой страницы из двух.

Отмена проходит через штатный cancel_scan из другого потока/DB connection.
Тест дожидается завершения команды перед следующим collector checkpoint
вне worker command lock, чтобы выбор момента отмены не зависел от гонки
потоков. Проверяется реальный checkpoint, а не подставленный ScanCancelled.
Ожидающий B не может быть claimed пока A активен; после CANCELLED получает
claim и SUCCEEDED. Ещё один полный A также SUCCEEDED. На каждом сценарии:

- Прежний A-лот остаётся FOUND при отмене. Проверенная partial-страница
  сохраняется только в pagination-сценарии; полный поиск не объявляется.
- Другой проект не получает чужие лоты; обе исходные ссылки не меняются.
- После отмены остаётся1 скрытая page в исходном context; native CDP
  Browser.getWindowForTarget возвращает window not found. Browser не запущен
  повторно:4 jobs/1 launch; pool shutdown отключает его полностью.
- Только последующий успешный полный A исключает отсутствующий старый лот
  как NOT_FOUND_IN_SEARCH, не SOLD. Следующий B и повторA сохраняются независимо,
  в конце QUEUED/RUNNING/CANCEL_REQUESTED jobs нет.

Первый запуск команды без STORAGE_ROOT не смог импортировать app.main из-за
попытки создать /storage; это ошибка окружения, не RED продукта. В корректном
одноразовом окружении оба новых теста дошли до последнего assertion:
ошибочно ожидался NOT_FOUND, тогда как принятый auction_storage контракт —
NOT_FOUND_IN_SEARCH. Причина проверена в owning storage; изменён только новый
тест. Продуктовый код не менялся: это characterization/приёмка уже реализованного
поведения, не объявленное исправление нового бага.

Baseline существующих cancel checks3 passed. Финальный native набор
(cancel + native pagination + hidden lifecycle):4 passed /1 headless opt-in
skipped. Queue/pool/launch-plan/adapter regression:127 passed /2 browser skipped.
Compileall нового теста и git diff --check passed. XML:
`work/auction-cancel-native-20261019.lAfLouYD/{baseline,native-first,native-final,regression}.xml`;
первое падение сохранено. Источник выдачи локальный, проверка лицензии в fixture
замокана; это не live/site-access, HTTP-auth, installed .app или Windows proof.
Сборка, commit/push/release/deploy, production gate и Telegram не выполнялись.

Финальная обычная полная backend suite: **803 passed /118 opt-in skipped**,
4 прежних SAWarning converter reflection (`backend-unit-final.xml`). В этом
прогоне browser/native cases честно skipped; их evidence — отдельные150/42
проверки и финальный native-complete, а не подмена обычным pytest. Процессы
завершённого live temporary profile не остались; найден только сам read-only ps.

### macOS preview закрытия окна и detector IAA — 18.10.2026

По отдельному запросу собрана локальная0.4.1 arm64 из текущего worktree ветки
`codex/multisource-auctions`. Артефакт:
`work/macos-artifact/iaa-close-repeat-20261018.gOQTjj2G/`.
Папка release содержит .app, BUILD-INFO и Open SearchCar Auctions.command,
который включает существующий source-tests профиль. Перед запуском новой
копии пользователь должен штатно выйти из работающей старой; сборка её не
останавливала/не заменяла и не меняла пользовательские данные.

Сборка включает закрытие headful/minimized runtime IAA после job и уточнение
классификации ручной проверки. Тест A/B/A не менял продуктовый код.
Первый Node-прогон выявил устаревшее ожидание: bare Security check/CAPTCHA
считались explicit manual prompt IAA. Node-тест согласован с уже проверенным
Chrome контрактом: явная инструкция по-прежнему true; bare security heading
blocked без manual, attribution/loading не CAPTCHA. Copart не менялся.

Финальные проверки: backend898 passed /8 opt-in skipped,4 прежних reflection
warnings; Node113 passed /1 PowerShell skipped; version check0.4.1, tsc/Vite и
Rust3 passed. Nuitka/Tauri успешно. Payload fingerprint:`2d430ebfb22e358b`,
новый отдельный cache при той же видимой версии. Packaged backend SHA256:
`6e990d2e7f6775f9aa0de98047ac3facda7c9beca0a03fc0fb2383969f3db912`.
Ad-hoc deep/strict codesign прошёл. На ресурсах именно упакованной .app прошли
SQLite/FK/WAL, bundled Chromium screenshot, protected bootstrap, graceful
shutdown; compiled navigation/auction-time2 passed на disposable данных.
JUnit/JSON/logs находятся рядом с артефактом, не включены в пользовательский ZIP.

ZIP:`SearchCar-0.4.1-macOS-arm64-iaa-close.zip`,200276792 bytes,
SHA256:`880cc056e051e6027acfc9a5987d01da417bf017ce6a15893adac5a20be706df`.
Проверена целостность, нет source-tests DB/cookies/logs/browser profiles.
Не notarized/updater-signed; installed GUI/live IAA/Copart и Windows остаются
непринятыми. Сервер/версия/commit/push/публикация не менялись.

### Рабочий журнал аукционных запусков — 19.10.2026

Ветка `codex/multisource-auctions`, следующий функциональный срез по запросу
пользователя. AuctionRuns подключён только вместо временного auction scans
экрана; история Encar, source adapters и управление самим браузером не менялись.

Проверенные правила: фильтры проекта/исхода отдельно для сайта, компактные
строки с локальной датой, явным trigger/unknown, выбранным охватом и числом
прочитанных лотов; неизвестный размер не даёт fake percent. FIRST_PAGE —
1 из1, не частичный обход9 страниц. Состояния проектов и смешанных запусков
не смешиваются между сайтами. Ожидание проверки активно, не ошибочный итог.
Замороженные имена удалённых проектов и source-projected legacy/CARS остаются
доступны; незнакомая дата/trigger/coverage явно неизвестны.

Раскрытие показывает отдельные проекты, сведения существующего pagination
и только сохранённые changes. Дедуплицированный report с project_ids виден
по каждому записанному контексту. Текущий car endpoint не используется для
изготовления исторической карточки; schedule change не становится SOLD.
Telegram явно выключен в source-test, доставки/retry нет. Отмена требует
подтверждения и отправляет source_keys только текущего сайта в точном scan id,
включая проекты, скрытые фильтром; другой сайт продолжает работать. Escape
не отправляет команду, ошибка остаётся доступной, фокус возвращается.
Фильтры/раскрытие/вкладки хранятся только в session QueryClient по source;
он уже очищается оболочкой при logout. Нет новой записи в workspace DB.

RED/GREEN: отсутствующие фильтры/охват, неверная трактовка waiting,
потерянные grouped report и legacy/CARS. Node141 passed /1 PowerShell skipped;
прежний packaging source-text check намеренно не входил в этот прогон.
Существующий `test_multisource_acceptance.py`:38 passed на disposable SQLite,
проверяет source projection/owner/worker/отмену, не live source access.
TSC/Vite и ESLint новых компонентов passed; предупреждение Vite о bundle
>500kB и прежние два ref warnings в ActivityDialog не исправлялись этим срезом.

Браузерный regression26/26: создание/выбор/редактирование/удаление/очередь/
countdown и новый журнал, RU/UK ×1280/800/360 без horizontal overflow. Фильтры,
Telegram-off, сохранённые schedule old→new, escaping, смена источника и контекст,
cancel/failure/Escape/focus, read-error/retry/empty проверены интерактивно.
Скриншоты/JSON: `work/auction-history-20261019-final/`; новый журнал после
legacy-совместимости — `work/auction-history-20261019-final-history/`.
Ранние неуспешные UI-прогоны сохранены; причины — selector по доступному
aria-label, неверное ожидание трёх IAA строк и race немедленного assertion
до cache-render. Исправлены fixtures/waits, не ослаблена приёмка продукта.

Новая .app не собиралась. Installed macOS/Windows, live Copart/IAA, полноценные
snapshot cards и Telegram не заявлены принятыми. Версия/коммит/push/release,
production entitlement и настройки пользователя не менялись.

### Карточки по снимкам аукционного отчёта — 19.10.2026

Следующий функциональный срез, ветка `codex/multisource-auctions`. Добавлен
owner-authenticated read-only GET `/api/scans/{id}/auction-report` с source_key,
обязательным текущим IANA computer_timezone и1–20 car_ids. Используется точный
after_snapshot_id отчёта, проверяются integrity/kind/car/source/listing/scan
binding. Нет fallback к последнему Car/CarSnapshot/ценам/расписанию. История
удалённого проекта сохраняется по прежним правилам, исключённая tracking-связь
не обходится. Batch-обогащение ограничено запрошенными машинами. Без миграции БД.

Время контекста — checked_at сохранённого наблюдения, при отсутствии поля —
created_at снимка; не сегодняшние backend часы. Для ставки читается round_id
строго observation_key этого scan/car, поэтому новый текущий раунд не подменяет
старую привязку. При отсутствии/несовпадении раунда сумма ставки не показывается,
причина подписана; BUY_NOW/ESTIMATED_RETAIL остаются отдельными смыслами.
Недостающий/невалидный снимок возвращает явный SNAPSHOT_UNAVAILABLE на строке.

AuctionHistoryReport lazy читает только раскрытую вкладку изменений. Карточки
порциями20, keyboard load-more, фильтр начинает с20 и сохраняет фокус. Повтор
ошибки явный; missing/error/loading различаются, сохранённые changes остаются.
Защита клиента сверяет scan/source/car/pointer/recorded timestamp ответа.
Переиспользуется обычная AuctionCard с исторической датой/привязкой раунда:
нет живого countdown, относительного «Сегодня», удаления, текущей карточки или
footer. Старый обычный режим не меняется. Фото загружается по сохранённому
проверенному URL с подписью «не архивируется и может измениться на сайте»;
текущий MAIN не подставляется. Архив байтов фотографий не добавлялся.

RED API404, отсутствующие исторический режим/zone; browser RED потери фокуса
при filter-key remount. После исправления input сохраняется, первая порция
сбрасывается без лишнего запроса старых страниц. GREEN:22 новых API-сценария,
backend86 passed (auction API + mixed worker/history regression), Node147 passed
/1 PowerShell skipped без прежнего packaging source-text check. TSC, Vite,
ESLint новых компонентов, py_compile и diff-check passed. Общий Vite bundle
>500kB остаётся предупреждением, не переписывался этим срезом. Первый backend
прогон ошибочно ожидал422 для CST; действующий общий контракт400 сохранён.

Браузерный fixture regression28/28 passed. Новые проверки: нет snapshot GET
для свёрнутого запуска; один scoped запрос41/42, pending; сохранённые поля,
ставка/время, фото modal/Escape/focus; read failure/retry/чужой source и missing
без актуальной подмены; IAA/UK45 карточек с запросами20/20/5, load-more с
клавиатуры, фильтр/возврат к20 без нового запроса. RU1280/800/360 и UK360 без
horizontal overflow. После финального mobile/focus/reset уточнения два новых
сценария повторно passed; снимки просмотрены, обычные карточки не переписаны.
Отчёты/снимки: `work/auction-snapshot-ui-verified/`,
`work/auction-snapshot-ui-final-verified/`; исходный focus RED сохранён отдельно.

Все записи и media — disposable fixtures, backend не обращается к сайтам.
В рабочем UI браузер может загружать сохранённое фото с разрешённого CDN.
Установленная .app, live источники и Windows не проверялись; сборка, commit,
push, версия, публикация, source entitlement, scheduler и Telegram не менялись.
