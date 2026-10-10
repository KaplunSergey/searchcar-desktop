// Presentation-only mockup. No requests, persistence, adapter or delivery rules.
// One outline grid for UI glyphs; illustrations keep their own dimensions.
const iconPaths={
  refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 18 17"/>',
  settings:'<path d="M10 2.5h4l.6 3 1.8 1 2.9-1 2 3.5-2.3 2v2l2.3 2-2 3.5-2.9-1-1.8 1-.6 3h-4l-.6-3-1.8-1-2.9 1-2-3.5 2.3-2v-2l-2.3-2 2-3.5 2.9 1 1.8-1Z"/><circle cx="12" cy="12" r="3"/>',
  down:'<path d="m6 9 6 6 6-6"/>',up:'<path d="m6 15 6-6 6 6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check:'<path d="m5 12 4 4L19 6"/>',stop:'<rect x="6" y="6" width="12" height="12" rx="2"/>',
  alert:'<path d="m12 3 10 17H2Z"/><path d="M12 9v4m0 3h.01"/>',
  help:'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4.2 1.8c-1.2.7-1.7 1-1.7 2.2m0 3h.01"/>',
  external:'<path d="M14 3h7v7m0-7L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',close:'<path d="m6 6 12 12M6 18 18 6"/>',menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  list:'<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
  grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>'
};
export const icon=name=>`<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${iconPaths[name]||iconPaths.help}</svg>`;
const dict = {
  damage: {
    'All Over':['По всему кузову','По всьому кузову'], Biohazard:['Биологическое загрязнение','Біологічне забруднення'],
    Electrical:['Электрика','Електрика'], 'Engine Burn':['Пожар двигателя','Пожежа двигуна'],
    'Engine Damage':['Повреждение двигателя','Пошкодження двигуна'], Flood:['Затопление','Затоплення'],
    Frame:['Рама','Рама'], 'Fresh Water':['Пресная вода','Прісна вода'],
    'Front & Rear':['Передняя и задняя части','Передня та задня частини'], 'Front End':['Передняя часть','Передня частина'],
    Hail:['Град','Град'], 'Interior Burn':['Пожар салона','Пожежа салону'],
    'Left & Right Side':['Левая и правая стороны','Ліва та права сторони'], 'Left Front':['Слева спереди','Зліва спереду'],
    'Left Rear':['Слева сзади','Зліва ззаду'], 'Left Side':['Левая сторона','Ліва сторона'],
    Mechanical:['Механические','Механічні'], None:['None — по источнику','None — за джерелом'],
    'Normal Wear & Tear':['Обычный износ','Звичайний знос'], Rear:['Задняя часть','Задня частина'],
    Repossession:['Изъятие кредитором','Вилучення кредитором'], 'Right Front':['Справа спереди','Справа спереду'],
    'Right Rear':['Справа сзади','Справа ззаду'], 'Right Side':['Правая сторона','Права сторона'],
    Rollover:['Опрокидывание','Перекидання'], Roof:['Крыша','Дах'], 'Storm Damage':['Последствия шторма','Наслідки шторму'],
    Strip:['Разукомплектован','Розукомплектований'], Suspension:['Подвеска','Підвіска'], Theft:['Кража','Крадіжка'],
    'Total Burn':['Полное выгорание','Повне вигоряння'], 'Transmission Damage':['Повреждение трансмиссии','Пошкодження трансмісії'],
    Undercarriage:['Днище','Днище'], Unknown:['Неизвестно (Unknown)','Невідомо (Unknown)'], Vandalized:['Вандализм','Вандалізм'],
    Side:['Боковая часть','Бічна частина'], 'Water/Flood':['Вода / затопление','Вода / затоплення'],
    'Rear End':['Задняя часть','Задня частина'], Vandalism:['Вандализм','Вандалізм'], Stripped:['Разукомплектован','Розукомплектований'],
    Burn:['Пожар','Пожежа'], 'Normal Wear':['Обычный износ','Звичайний знос'], 'Top/Roof':['Верх / крыша','Верх / дах'],
    'Minor Dent/Scratches':['Вмятины / царапины','Вм’ятини / подряпини'], 'Burn - Interior':['Пожар салона','Пожежа салону'],
    'Burn - Engine':['Пожар двигателя','Пожежа двигуна'], 'Frame Damage':['Повреждение рамы','Пошкодження рами']
  },
  engine: {
    'Run & Drive':['Run & Drive','Run & Drive'], Starts:['Двигатель запускается','Двигун запускається'],
    Stationary:['Без движения (Stationary)','Без руху (Stationary)'], 'Engine Start':['Engine Start Program','Engine Start Program'],
    Enhanced:['Подготовка автомобиля','Підготовка автомобіля']
  },
  odometer: {
    Actual:['Фактический','Фактичний'], 'Not Actual':['Не фактический','Не фактичний'],
    'Exceeds Mechanical Limits':['Превышен предел одометра','Перевищено межу одометра'],
    'Inoperable Digital Dash':['Цифровая панель не работает','Цифрова панель не працює'],
    Exempt:['Освобождён от подтверждения','Звільнений від підтвердження']
  },
  keys: { true:['Ключи есть','Ключі є'], false:['Ключей нет','Ключів немає'] },
  airbags: { Deployed:['Сработали','Спрацювали'], Intact:['Целые (по источнику)','Цілі (за джерелом)'], Missing:['Отсутствуют','Відсутні'], None:['None — по источнику','None — за джерелом'] },
  title: { Clear:['Clear','Clear'], 'Clean Title':['Clean Title','Clean Title'], Salvage:['Salvage','Salvage'], 'Salvage Title':['Salvage Title','Salvage Title'], 'Non-Repairable':['Non-Repairable','Non-Repairable'], 'Title Not Yet Available':['Документ ещё не доступен','Документ ще не доступний'] },
  sale: { 'Pure Sale':['Без резервной цены','Без резервної ціни'], 'On Approval':['Одобрение продавца','Схвалення продавця'], 'Minimum Bid':['Минимальная цена','Мінімальна ціна'], 'On Reserve':['Резервная цена','Резервна ціна'], 'If Bids':['Условная ставка (If Bids)','Умовна ставка (If Bids)'] },
  loss: { Collision:['Столкновение','Зіткнення'], Other:['Другое','Інше'], Water:['Вода','Вода'], Theft:['Кража','Крадіжка'], Fire:['Пожар','Пожежа'] }
};
export const observed = {
  IAA: {
    damage: ['All Over','Biohazard','Electrical','Engine Burn','Engine Damage','Flood','Frame','Fresh Water','Front & Rear','Front End','Hail','Interior Burn','Left & Right Side','Left Front','Left Rear','Left Side','Mechanical','None','Normal Wear & Tear','Rear','Repossession','Right Front','Right Rear','Right Side','Rollover','Roof','Storm Damage','Strip','Suspension','Theft','Total Burn','Transmission Damage','Undercarriage','Unknown','Vandalized'],
    engine:['Run & Drive','Starts','Stationary'], odometer:['Not Actual','Inoperable Digital Dash','Exceeds Mechanical Limits'],
    keys:[true], airbags:['Deployed','Intact','Missing','None'], title:['Clear','Salvage','Non-Repairable','Title Not Yet Available'],
    loss:['Collision','Other','Water','Theft','Fire'],
    flags:['Public','Offsite','Lane TBD','Automobiles','Rental Vehicles','Fleet Vehicles'],
    actions:['Pre-Bid','Join Auction','Timed Auctions / BID NOW','Buy Now','Auction Not Assigned']
  },
  Copart: {
    damage:['Front End','Side','Water/Flood','Hail','Rear End','Undercarriage','Vandalism','Stripped','Rollover','Burn','All Over','Mechanical','Normal Wear','Top/Roof','Minor Dent/Scratches','Burn - Interior','Burn - Engine','Frame Damage'],
    engine:['Run & Drive','Engine Start','Enhanced'], odometer:['Actual','Not Actual','Exceeds Mechanical Limits'],
    keys:[true,false], airbags:[], title:['Clean Title','Salvage Title','Non-Repairable'], loss:[],
    flags:['Wholesale','Featured Vehicle','Offsite Sales','GO-Offsite','Flood Reconditioned','Green Light','Red Light'],
    actions:['Bid now','Buy it now','Join auction','LIVE NOW','Future','Upcoming lot','Auction in…']
  }
};
export function text(ru, uk, lang) { return lang === 'uk' ? uk : ru; }
function translate(group, raw, lang='ru') {
  if (raw === null || raw === undefined || raw === '') return text('Нет данных','Немає даних',lang);
  return dict[group]?.[String(raw)]?.[lang === 'uk' ? 1 : 0] ?? String(raw);
}
function escape(value) { return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }
function safeLink(raw, source) {
  try {
    const u=new URL(raw);
    const site=u.hostname==='www.copart.com'?'Copart':u.hostname==='www.iaai.com'?'IAA':null;
    const detail=site==='Copart'?/^\/(?:[a-z]{2}\/)?lot\/\d+(?:\/[^/]+)?\/?$/.test(u.pathname)
      :site==='IAA'&&/^\/VehicleDetail\/\d+~US\/?$/.test(u.pathname);
    return u.protocol==='https:' && detail && (!source || source===site) && !u.username && !u.password ? u.href : null;
  }
  catch { return null; }
}
const exactTime = event => event?.precision !== 'day' && typeof event?.at === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(event.at) && Number.isFinite(Date.parse(event.at));
function eligible(event) { return exactTime(event) || /^\d{4}-\d{2}-\d{2}$/.test(event?.date || ''); }
function eventView(event={}, now, lang='ru') {
  const t=(ru,uk)=>text(ru,uk,lang);
  const states = {
    live:[t('Аукцион идёт','Аукціон триває'),'good'], closed:[t('Торги завершены','Торги завершені'),'neutral'],
    cancelled:[t('Аукцион отменён','Аукціон скасовано'),'warn'], removed:[t('Исключён из проекта','Виключений із проєкту'),'neutral'],
    unassigned:[t('Аукцион не назначен','Аукціон не призначено'),'neutral'], future:['Future','neutral'], upcoming:['Upcoming lot','neutral']
  };
  const known=states[event.state];
  const view={text:known?.[0] || t('Расписание','Розклад'), tone:known?.[1] || 'blue', countdown:null, date:'', time:'', zone:'', hint:''};
  if (exactTime(event)) {
    const at=Date.parse(event.at), locale=lang==='uk'?'uk-UA':'ru-RU', zone=event.zone || 'Europe/Warsaw';
    // Explicit fixture zone stands in for computer timezone, never inferred from site country.
    const options={timeZone:zone};
    try {
      const fullDate=new Intl.DateTimeFormat(locale,{...options,day:'numeric',month:'long',year:'numeric'});
      const parts=fullDate.formatToParts(at);
      const part=type=>parts.find(item=>item.type===type).value;
      const currentYear=fullDate.formatToParts(now).find(item=>item.type==='year').value;
      view.fullDate=fullDate.format(at);
      view.date=`${part('day')} ${part('month')}${part('year')===currentYear?'':' '+part('year')}`;
      const day=new Intl.DateTimeFormat('en-CA',{...options,year:'numeric',month:'2-digit',day:'2-digit'});
      if(day.format(at)===day.format(now))view.date=t('Сегодня · ','Сьогодні · ')+view.date;
      view.time=new Intl.DateTimeFormat(locale,{...options,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(at);
    } catch {
      view.date=event.at;
      view.hint=t('Неизвестный часовой пояс · без отсчёта','Невідомий часовий пояс · без відліку')+': '+zone;
      return view;
    }
    view.zone=zone;
    if (!known && (event.kind==='start' || event.kind==='end')) {
      if (at > now) {
        const mins=Math.ceil((at-now)/60000), h=Math.floor(mins/60), m=mins%60;
        view.countdown=(event.kind==='end' ? t('До окончания','До завершення') : t('Через','Через'))+` ${h} ${t('ч','год')} ${m} ${t('мин','хв')}`;
        view.text=event.kind==='end'?t('Окончание торгов','Завершення торгів'):t('Начало аукциона','Початок аукціону');
      } else { view.text=t('Время по расписанию наступило','Час за розкладом настав'); view.tone='warn'; view.hint=t('Текущий этап не подтверждён','Поточний етап не підтверджено'); }
    } else if (!known) {
      view.text=t('Время события · Timed','Час події · Timed'); view.tone='warn';
      view.hint=t('Начало или окончание — не подтверждено','Початок чи завершення — не підтверджено');
    }
  } else if (event.date) {
    view.date=event.date;
    view.hint=t('Точное время и локальный день требуют проверки','Точний час і локальний день потребують перевірки');
  } else if (event.at) {
    view.date=event.at; view.tone='warn'; view.hint=t('Часовой пояс не подтверждён · без отсчёта','Часовий пояс не підтверджено · без відліку');
  } else if (!known) {
    view.text=event.raw || t('Нет расписания','Немає розкладу'); view.tone='neutral';
  }
  if (event.state==='rescheduled') {view.text=t('Аукцион перенесён','Аукціон перенесено');view.tone='warn';view.hint=t('Новая дата · прежнее время сохранено в истории','Нова дата · попередній час збережений в історії');}
  return view;
}
function odometerView(odo={}, lang='ru') {
  const present=Number.isFinite(odo.value) && odo.value>=0;
  return {
    value:present?new Intl.NumberFormat(lang==='uk'?'uk-UA':'ru-RU').format(odo.value):translate('odometer',null,lang),
    note:odo.status?translate('odometer',odo.status,lang):text('Достоверность не указана','Достовірність не зазначена',lang),
    km:present && odo.unit==='mi' && odo.status==='Actual'?Math.round(odo.value*1.609344):null,
    warn:Boolean(odo.status && odo.status!=='Actual'), unit:present?odo.unit || '': ''
  };
}
export const ui = { translate, eventView, odometerView, eligible, escape, safeLink };

export const scenarios = {
  scheduled:{ru:'Назначен · точное время',uk:'Призначений · точний час',event:{state:'scheduled',kind:'start',at:'2026-10-08T15:30:00+02:00'}},
  live:{ru:'Идёт · сигнал источника',uk:'Триває · сигнал джерела',event:{state:'live',kind:'start',at:'2026-10-08T12:30:00+02:00'}},
  elapsed:{ru:'Время прошло · этап неизвестен',uk:'Час минув · етап невідомий',event:{state:'scheduled',kind:'start',at:'2026-10-08T12:00:00+02:00'}},
  rescheduled:{ru:'Перенос · новая дата',uk:'Перенесення · нова дата',design:true,event:{state:'rescheduled',kind:'start',at:'2026-10-09T15:30:00+02:00'}},
  timed:{ru:'Timed · назначение времени неизвестно',uk:'Timed · призначення часу невідоме',event:{state:'timed',kind:'unknown',at:'2026-10-09T03:10:00+02:00'}},
  timedEnd:{ru:'Timed · подтверждённое окончание',uk:'Timed · підтверджене завершення',design:true,event:{state:'timed',kind:'end',at:'2026-10-08T18:00:00+02:00'}},
  dateOnly:{ru:'Дата без времени',uk:'Дата без часу',design:true,event:{state:'scheduled',date:'2026-10-08',precision:'day'}},
  zoneUnknown:{ru:'Часовой пояс неизвестен',uk:'Часовий пояс невідомий',design:true,event:{state:'scheduled',kind:'start',at:'2026-10-08T15:30:00'}},
  future:{ru:'Future · новый лот пропускаем',uk:'Future · новий лот пропускаємо',event:{state:'future'}},
  upcoming:{ru:'Upcoming lot · без даты',uk:'Upcoming lot · без дати',event:{state:'upcoming'}},
  unassigned:{ru:'Auction Not Assigned',uk:'Auction Not Assigned',event:{state:'unassigned'}},
  dateLost:{ru:'У сохранённого лота исчезла дата',uk:'У збереженого лота зникла дата',design:true,existing:true,event:{}},
  closed:{ru:'Завершён · источник подтвердил',uk:'Завершений · джерело підтвердило',design:true,event:{state:'closed'}},
  cancelled:{ru:'Отменён · источник подтвердил',uk:'Скасований · джерело підтвердило',design:true,event:{state:'cancelled'}},
  removed:{ru:'Исчез · полный поиск',uk:'Зник · повний пошук',design:true,event:{state:'removed'}},
  unknown:{ru:'Новое значение · raw fallback',uk:'Нове значення · raw fallback',design:true,event:{raw:'New auction status / vendor-specific value'}}
};

export function cardHTML(data, now, lang) {
  const t=(ru,uk)=>text(ru,uk,lang), e=escape, event=eventView(data.event,now,lang), odo=odometerView(data.odo,lang);
  const field=(label,group,raw)=>`<span class="small-field"><span>${e(label)}</span><strong>${e(translate(group,raw,lang))}</strong></span>`;
  const raw=(group,value)=>value && translate(group,value,lang)!==String(value)?`<span class="raw">${e(value)}</span>`:'';
  const damage=translate('damage',data.damage,lang), link=safeLink(data.url,data.source);
  return `<article class="card ${event.tone}" aria-label="${e(data.name)}">
    <div class="photo" role="img" aria-label="${e(t('Фото недоступно в макете','Фото недоступне в макеті'))}"><svg viewBox="0 0 100 60" aria-hidden="true"><path d="M14 38l8-18h49l13 18M8 38h82v15H8zM29 20l-5 18h47l-5-18M24 53v5m51-5v5"/></svg><span>${e(t('Фото недоступно','Фото недоступне'))}</span></div>
    <div class="info"><div class="eyebrow">${e(data.source)} · ${e(t('Демо-лот','Демо-лот'))} ${e(data.id)}</div><h2>${e(data.name)}</h2><p class="subtitle">${e(data.spec)}</p>
      <div class="facts"><div><span class="label">${e(t('Показание одометра','Показання одометра'))}</span><div class="mileage ${odo.unit?'':'missing'}">${e(odo.value)} <span class="unit">${e(odo.unit)}</span></div><div class="secondary ${odo.warn?'warning':''}">${odo.warn?icon('alert'):''}${e(odo.note)}</div>${odo.km!==null?`<div class="secondary">≈ ${e(new Intl.NumberFormat(lang==='uk'?'uk-UA':'ru-RU').format(odo.km))} ${e(t('км','км'))}</div>`:''}</div>
      <div><span class="label">${e(t('Повреждения','Пошкодження'))}</span><div class="damage">${e(damage)}</div>${raw('damage',data.damage)}${data.secondary?`<div class="secondary">${e(t('Доп.','Дод.'))}: ${e(translate('damage',data.secondary,lang))}</div>`:''}</div></div>
      <div class="condition"><span class="chip ${data.engine==='Run & Drive'?'good':'neutral'}">${e(translate('engine',data.engine,lang))}</span>${raw('engine',data.engine)}</div>
      <div class="small-fields">${field(t('Ключи','Ключі'),'keys',data.keys)}${field(t('Документ','Документ'),'title',data.title)}${data.source==='IAA'?field(t('Подушки','Подушки'),'airbags',data.airbags):''}${data.loss?field(t('Тип потери','Тип втрати'),'loss',data.loss):''}${data.sale?field(t('Условие продажи','Умова продажу'),'sale',data.sale):''}</div>
      ${data.flags?.length?`<div class="flags">${data.flags.map(flag=>`<span class="chip neutral">${e(flag)}</span>`).join('')}</div>`:''}
      ${data.note?`<p class="inline-note">${e(data.note)}</p>`:''}
    </div>
    <div class="auction"><span class="label">${e(t('Аукцион','Аукціон'))}</span><div class="event-title ${event.tone}">${e(event.text)}</div><div class="date" title="${e(event.fullDate || event.date)}">${e(event.date)}</div><div class="clock">${e(event.time || '—')}</div>${event.countdown?`<div class="countdown">${icon('clock')}${e(event.countdown)}</div>`:''}${event.zone?`<div class="zone">${e(t('Время компьютера','Час комп’ютера'))}<br>${e(event.zone)}</div>`:''}${event.hint?`<p class="event-hint">${e(event.hint)}</p>`:''}
      ${data.action?`<div class="source-action"><span class="label">${e(t('Метка источника','Мітка джерела'))}</span><span class="chip neutral">${e(data.action)}</span></div>`:''}
      ${data.price?`<div class="price"><span class="label">${e(data.price.kind==='buy'?t('Buy It Now','Buy It Now'):t('Текущая ставка · снимок','Поточна ставка · знімок'))}</span><strong>${e(new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(data.price.value))}</strong><small>${e(t('На момент проверки · 12:37','На момент перевірки · 12:37'))}</small></div>`:''}
      ${link?`<a class="source-link" href="${e(link)}" target="_blank" rel="noopener noreferrer">${e(t('Объявление','Оголошення'))}${icon('external')}</a>`:`<span class="link-unavailable">${e(t('Ссылка на лот недоступна','Посилання на лот недоступне'))}</span>`}
    </div>
  </article>`;
}
