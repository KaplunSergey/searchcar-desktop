import { readCalendarPage, REMINDER_SHORTCUT_NAME } from "../app/auction-calendar-link";
import "./styles.css";

const root = document.getElementById("event")!;
let rendered = "";

function node<K extends keyof HTMLElementTagNameMap>(tag: K, text: string, className = "") {
  const element = document.createElement(tag); element.textContent = text; element.className = className; return element;
}

function render() {
  const now = Date.now(), event = readCalendarPage(location.hash, now);
  const late = event ? Date.parse(event.calendar.start_utc) - now < 900000 : false;
  const key = `${location.hash}:${Boolean(event)}:${late}:${Boolean(event?.reminder.available)}`;
  if (key === rendered) return;
  rendered = key;
  root.replaceChildren();
  const locale = event?.locale || (navigator.language.startsWith("uk") ? "uk" : "ru");
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  document.documentElement.lang = locale;
  document.title = l("SearchCar · Календарь аукциона", "SearchCar · Календар аукціону");
  document.querySelector("header small")!.textContent = l("Календарь аукциона", "Календар аукціону");
  document.querySelector("footer")!.textContent = l("SearchCar не связан с Telegram, Google, Apple или Microsoft.", "SearchCar не пов’язаний із Telegram, Google, Apple або Microsoft.");
  if (!event) {
    const error = node("section", "", "notice"); error.setAttribute("role", "alert");
    error.append(node("h1", l("Событие недоступно", "Подія недоступна")), node("p", l(
      "Ссылка неполная или повреждена, либо время начала уже прошло. Запросите актуальное сообщение с подтверждённым временем начала аукциона.",
      "Посилання неповне або пошкоджене, або час початку вже минув. Запросіть актуальне повідомлення з підтвердженим часом початку аукціону.")));
    root.append(error); return;
  }
  const { calendar, listing } = event;
  root.append(node("p", l("Добавить в календарь", "Додати до календаря"), "eyebrow"), node("h1", calendar.title));
  const time = node("time", new Intl.DateTimeFormat(locale === "uk" ? "uk-UA" : "ru-RU", {
    dateStyle: "long", timeStyle: "short",
  }).format(new Date(calendar.start_utc)), "event-time");
  time.dateTime = calendar.start_utc;
  root.append(time, node("p", l("Часовой пояс устройства", "Часовий пояс пристрою") + ": " + Intl.DateTimeFormat().resolvedOptions().timeZone, "muted"));
  const lot = node("a", l("Открыть лот на аукционе ↗", "Відкрити лот на аукціоні ↗"));
  lot.href = listing; lot.target = "_blank"; lot.rel = "noopener noreferrer"; root.append(lot);
  root.append(node("p", l("Выберите календарь или напоминание. Сохранение — только после вашего подтверждения.", "Виберіть календар або нагадування. Збереження — лише після вашого підтвердження."), "intro"));
  const actions = node("div", "", "calendar-options");
  const google = node("a", "Google Calendar", "calendar-option primary");
  google.href = calendar.google_url; google.target = "_blank"; google.rel = "noopener noreferrer";
  google.append(node("small", l("Открыть заполненную форму", "Відкрити заповнену форму")));
  const outlook = node("a", "Outlook · Windows", "calendar-option");
  outlook.href = calendar.outlook_url; outlook.target = "_blank"; outlook.rel = "noopener noreferrer";
  outlook.append(node("small", l("Форма в браузере · личный Microsoft Account", "Форма в браузері · особистий Microsoft Account")));
  const apple = node(event.reminder.available ? "a" : "div", l("Apple · Напоминание", "Apple · Нагадування"), "calendar-option");
  if (apple instanceof HTMLAnchorElement && event.reminder.available) {
    apple.href = event.reminder.shortcut_url; apple.rel = "noreferrer";
    apple.addEventListener("click", click => {
      if (!readCalendarPage(location.hash, Date.now())?.reminder.available) { click.preventDefault(); render(); }
    });
    apple.append(node("small", l("За 15 минут · через команду SearchCar Reminder", "За 15 хвилин · через команду SearchCar Reminder")));
  } else {
    apple.setAttribute("aria-disabled", "true");
    apple.append(node("small", l("Время напоминания уже прошло", "Час нагадування вже минув")));
  }
  const ics = node("a", "Apple Calendar / .ics", "calendar-option");
  const file = new URL("/event.ics", location.origin);
  file.searchParams.set("event", location.hash.slice(1));
  ics.href = file.href; ics.rel = "noreferrer";
  ics.append(node("small", l("Запасной вариант · импорт файла", "Запасний варіант · імпорт файлу")));
  actions.append(google, apple, outlook, ics); root.append(actions);
  const setup = node("details", "", "notice apple-reminder-setup");
  setup.append(node("summary", l("Первый раз с Apple? Добавьте команду один раз", "Уперше з Apple? Додайте команду один раз")), node("p", l(
    "Это тестовый вариант для iPhone и Mac. Добавьте команду, оставьте название SearchCar Reminder и выберите список iCloud — напоминания будут синхронизироваться между вашими устройствами.",
    "Це тестовий варіант для iPhone та Mac. Додайте команду, залиште назву SearchCar Reminder та виберіть список iCloud — нагадування синхронізуватимуться між вашими пристроями.")));
  const install = node("a", l("Добавить команду SearchCar Reminder", "Додати команду SearchCar Reminder"));
  install.href = `/${encodeURIComponent(REMINDER_SHORTCUT_NAME)}.shortcut`; install.rel = "noreferrer";
  setup.append(install, node("p", l(
    "Затем вернитесь на эту страницу и нажмите «Apple · Напоминание». Проверьте дату и ссылку в окне команды и подтвердите добавление. При первом запуске Apple может запросить доступ к Напоминаниям. Само открытие страницы ничего не создаёт.",
    "Потім поверніться на цю сторінку й натисніть «Apple · Нагадування». Перевірте дату й посилання у вікні команди та підтвердьте додавання. Під час першого запуску Apple може попросити доступ до Нагадувань. Саме відкриття сторінки нічого не створює.")));
  root.append(setup);
  root.append(node("p", l("Apple: напоминание за 15 минут. В Google и Outlook проверьте напоминание перед сохранением.",
    "Apple: нагадування за 15 хвилин. У Google та Outlook перевірте нагадування перед збереженням."), "muted"));
  const notice = node("details", "", "notice");
  notice.append(node("summary", l("Совместимость и ограничения", "Сумісність і обмеження")), node("p", l(
    "В Google и Outlook проверьте напоминание перед сохранением. Файл .ics содержит напоминание за 15 минут; его применение зависит от календаря и разрешений уведомлений. Для Apple-команды уведомление назначается на время за 15 минут до начала.",
    "У Google та Outlook перевірте нагадування перед збереженням. Файл .ics містить нагадування за 15 хвилин; його застосування залежить від календаря та дозволів сповіщень. Для Apple-команди сповіщення призначається на час за 15 хвилин до початку.")), node("p", l(
    "Outlook открывается в браузере, не обязательно в приложении Windows. Может потребоваться вход в личную учётную запись Microsoft. Окончание торгов неизвестно: Outlook может подставить своё время окончания — проверьте его в форме.",
    "Outlook відкривається в браузері, не обов’язково в застосунку Windows. Може знадобитися вхід в особистий обліковий запис Microsoft. Завершення торгів невідоме: Outlook може підставити власний час завершення — перевірте його у формі.")), node("p", l(
    "На iPhone откройте эту страницу в Safari, если встроенный браузер Telegram не показывает добавление в календарь. Событие нужно сохранить вручную; результат зависит от браузера и версии iOS.",
    "На iPhone відкрийте цю сторінку в Safari, якщо вбудований браузер Telegram не показує додавання до календаря. Подію потрібно зберегти вручну; результат залежить від браузера й версії iOS.")), node("p", l(
    "Только кнопка Apple Calendar / .ics запрашивает файл с сервера. Кнопка Apple · Напоминание передаёт данные установленной команде на вашем устройстве; в нашей базе они не сохраняются.",
    "Лише кнопка Apple Calendar / .ics запитує файл із сервера. Кнопка Apple · Нагадування передає дані встановленій команді на вашому пристрої; у нашій базі вони не зберігаються.")));
  if (late) { const warning = node("p", l("До начала меньше 15 минут: время напоминания уже прошло.", "До початку менше 15 хвилин: час нагадування вже минув."), "warning"); warning.setAttribute("role", "status"); root.append(warning); }
  root.append(notice, node("p", l(
    "Это снимок расписания из сообщения, не проверка актуальности на сайте. Время окончания неизвестно. Переносы аукциона автоматически не обновляются — проверьте лот перед торгами.",
    "Це знімок розкладу з повідомлення, не перевірка актуальності на сайті. Час завершення невідомий. Перенесення аукціону автоматично не оновлюються — перевірте лот перед торгами."), "muted"));
}

render();
window.addEventListener("hashchange", render);
window.addEventListener("focus", render);
window.addEventListener("pageshow", render);
document.addEventListener("visibilitychange", render);
window.setInterval(render, 1000);
