# Состояния общей карточки IAA / Copart

Первичная проверка 07.10.2026: по 100 строк первой страницы и видимые фильтры.
По дополнительному запросу пользователя выполнен [проход всей доступной
пагинации](AUCTION_PAGINATION_AUDIT_2026-10-07.md): IAA — 10 страниц, 945 уникальных
лотов; Copart — 10 страниц каждого из двух видов, 1086 ID в объединении,
но полный охват совпадений ограничен счётчиком 1000+. Без аккаунтов и карточек.
Это не исчерпывающий каталог: неизвестное значение сохраняется как raw,
пустое поле не превращается в отрицательное состояние.

## Общая композиция

[Макет v4](mockups/auction-cards-v4.html): один renderCard для обоих
источников, отдельно от Encar. Убраны нижний footer с площадкой/ACV/ставкой/
избранным и раскрывающиеся детали. Это изменение отображения, не предложение
удалить поля данных. Основные блоки: фото, название, источник, одометр,
повреждения, запуск, ключи/документ, время и ссылка. В макете fixtures и ссылки
на поиски; реальный UI должен ссылаться на конкретный лот.
В v4 добавлены RU/UK, интерактивная лаборатория признаков/расписания,
каталог всех найденных значений и loading/empty/partial/error/cancelled/blocked.
[Покрытие и проверка](AUCTION_UI_STATE_COVERAGE.md). Это макет, не подключённый UI.

## Независимые признаки

| Признак | IAA: наблюдалось | Copart: наблюдалось / описано официально | Правило |
|---|---|---|---|
| Запуск | Run & Drive, Starts, Stationary, нет данных | CERT-D = Run & Drive; CERT-S = Engine Start; CERT-E = Enhanced, подтверждено одинаковыми ID в двух видах | Enhanced — отдельный признак подготовки, не исправность |
| Одометр | Not Actual, Inoperable Digital Dash, Exceeds Mechanical Limits, обычное показание | ACTUAL, NOT ACTUAL, EXCEEDS MECHANICAL LIMITS наблюдались; EXEMPT только в словаре | Недостоверное показание не называть фактическим пробегом |
| Повреждения | Primary / Secondary, отдельно Loss Type | Основные/дополнительные повреждения | Запуск не отменяет повреждений; None отличается от пустого поля |
| Ключи | Key Available или пусто | Keys available / Keys not available в New list | Пусто = нет данных, не «нет ключей» |
| Подушки | Deployed, Intact, Missing, None, пусто | Отдельное поле не наблюдалось | Не переносить сведения между источниками; None отличается от пусто |
| Документы | Clear, Salvage, Non-Repairable, Title Not Yet Available | Clean / Salvage / Non Repairable в фильтрах, конкретные документы в tooltip | Документ — не механическое состояние |
| Продажа | Pre-Bid, Join Auction, Timed Auctions + BID NOW, Buy Now, Auction Not Assigned | Current bid / Bid now, Buy It Now, Future, Upcoming lot, LIVE NOW / Join auction | Bid now не доказывает начало live-торгов |

Примеры IAA stock: 45917945 — Not Actual; 45627569 — Inoperable Digital Dash;
46067692 — Starts + Timed Auctions; 46220642 — Run & Drive.
Copart: 49928046 — 0 N / NOT ACTUAL; 51032626 — Future; 41587166 — Upcoming lot.
Последние два значения не дают точной даты: новые такие лоты пока не добавляем.

IAA наблюдаемые повреждения: Front End, Rear, Left/Right Front, Left/Right Rear,
Left/Right Side, Front & Rear, All Over, Flood, Storm Damage, Undercarriage,
Frame, Suspension. Copart фильтры: All Over, Burn, Front End, Hail, Mechanical,
Minor Dent/Scratches, Normal Wear, Rear End, Rollover, Side. Словарь также
описывает пожар двигателя/салона, раму, затопление, вандализм, крышу,
неполный ремонт и неизвестное повреждение. Это не шкала тяжести.
[Copart glossary](https://www.copart.com/content/us/en/support/faq-topics/common-terms-1).

Copart Run & Drive и Engine Start описывают проверку при поступлении;
Enhanced относится к подготовке автомобиля, а не доказательству запуска.
IAA Stationary оставляем собственной меткой, не заменяем «двигатель сломан».
[Copart terminology](https://www.copart.com/content/us/en/landing-page/copart-terminology).

Copart Pure Sale / On Approval / Minimum Bid (On Reserve) — условия продажи:
без резерва / одобрение продавца / минимальная цена. Это не этап торгов.
[Copart sale statuses](https://www.copart.com/content/us/en/member-news/sale-status).
IAA Bidding Closed и If Bids описаны в AuctionNow, но не наблюдались в этом
списке. Персональные статусы ставок без аккаунта не собираем.
[IAA AuctionNow](https://www.iaai.com/US/marketing/auctionnow).

## Даты и переходы

- Точная дата: локальное время компьютера и «Через …».
- Плановое время прошло: в v4 «Время по расписанию наступило» + «Текущий этап
  не подтверждён». Это предложенная безопасная подпись, не подтверждённое live.
- Live / Closed / Cancelled: только прямое подтверждение источника.
- Перенос: отдельное событие и новая дата, не новая машина.
- Future / Upcoming lot / неизвестная дата: новые лоты не добавляем.
- Исчезновение: убираем из активной выборки только после полного успешного
  поиска с прежними фильтрами; это не SOLD.

Copart уже отображает CEST, например 19:00 CEST: не конвертировать повторно
как время американской площадки. displayStr URL содержит 2025–2027, однако
searchCriteria и видимый фильтр — 2026–2027; учитывать действительный фильтр.

IAA US Timed Auctions: у stock 46067692 видны BID NOW, отсчёт и 07.10.2026
20:10 CDT → 08.10.2026 03:10 Europe/Warsaw. Это может быть время завершения,
не начала. [IAA Canada Timed Auctions](https://ca.iaai.com/marketing/timed-auctions)
описывает закрытие, но её правила не переносим автоматически на США.
Семантика времени US требует подтверждения до реализации уведомлений.
Нельзя выдавать такой отсчёт за «начнётся через …» без проверки.

Предлагаемые поля контракта: sale_kind (live/timed/unknown), event_kind
(start/end/unknown), precision, raw timezone, UTC. Пока только план.
Нужно решение пользователя: Timed Auctions включать в «Сегодня» по дате
окончания или показывать отдельной выборкой активных торгов?
