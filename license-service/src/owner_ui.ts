const OWNER_APP = String.raw`<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SearchCar — управление лицензиями</title>
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color:#17223b; background:#f4f7fb; }
    * { box-sizing:border-box; }
    body { margin:0; min-width:320px; }
    header { background:#17223b; color:#fff; padding:24px max(24px, calc((100vw - 1180px)/2)); display:flex; align-items:center; justify-content:space-between; gap:16px; }
    h1 { font-size:22px; margin:0; } h2 { font-size:18px; margin:0 0 16px; } h3 { font-size:15px; margin:0 0 8px; }
    main { max-width:1180px; margin:0 auto; padding:28px 24px 60px; }
    .hidden { display:none !important; } .card { background:#fff; border:1px solid #d7e0ee; border-radius:16px; padding:22px; box-shadow:0 6px 22px rgba(28,45,75,.06); }
    .narrow { max-width:470px; margin:40px auto; } .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; margin-bottom:18px; }
    .stack { display:grid; gap:12px; } label { display:grid; gap:6px; font-size:13px; font-weight:650; color:#4f6283; }
    input, select { width:100%; min-height:42px; border:1px solid #c9d5e7; border-radius:9px; background:#fff; padding:9px 11px; font:inherit; color:#17223b; }
    button { min-height:42px; padding:9px 15px; border:0; border-radius:9px; color:#fff; background:#3569d4; font:inherit; font-weight:700; cursor:pointer; }
    button.secondary { color:#29405f; background:#fff; border:1px solid #c9d5e7; } button.danger { background:#b42335; } button:disabled { opacity:.55; cursor:wait; }
    .row { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }.actions { display:flex; gap:10px; align-items:end; flex-wrap:wrap; }
    .message { margin:14px 0 0; padding:12px; border-radius:9px; background:#eef5ff; color:#29405f; }.message.error { background:#fff0f1; color:#a12d3d; }.muted { color:#7083a2; font-size:14px; line-height:1.55; }.source-options { display:grid; gap:8px; padding:10px; border:1px solid #c9d5e7; border-radius:9px; }.source-option { display:flex; align-items:center; gap:8px; color:#29405f; font-size:14px; font-weight:600; }.source-option input { width:auto; min-height:auto; }
    .transfer-summary { min-height:58px; padding:11px 12px; border:1px solid #d7e0ee; border-radius:9px; background:#f7f9fc; }.warning { margin:0; padding:10px 12px; border-radius:9px; background:#fff7ed; color:#935d14; font-size:12px; line-height:1.5; }
    table { width:100%; border-collapse:collapse; font-size:13px; } th,td { text-align:left; padding:10px 8px; border-bottom:1px solid #e4ebf5; vertical-align:top; } th { color:#657896; font-size:12px; text-transform:uppercase; } .table-wrap { overflow-x:auto; }
    code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; } .pill { padding:4px 8px; border-radius:99px; background:#edf4ff; color:#3569d4; font-size:12px; font-weight:700; }
    @media (max-width:760px) { .grid { grid-template-columns:1fr; } main { padding:18px 14px 40px; } header { padding:18px 14px; } }
  </style>
</head>
<body>
  <header><h1>SearchCar · лицензии</h1><button id="logout" class="secondary hidden">Выйти</button></header>
  <main>
    <section id="loading" class="card narrow"><p class="muted">Проверяем сессию владельца…</p></section>
    <section id="setup" class="card narrow hidden">
      <h2>Первичная настройка</h2>
      <p class="muted">Создайте единственную учётную запись владельца. Токен нужен только один раз, не сохраняется в браузере и не попадает в приложение.</p>
      <form id="setup-form" class="stack">
        <label>Токен первоначальной настройки<input name="token" type="password" autocomplete="off" required></label>
        <label>Логин владельца<input name="login" autocomplete="username" minlength="3" required></label>
        <label>Пароль (не менее 12 символов)<input name="password" type="password" autocomplete="new-password" minlength="12" required></label>
        <button>Создать учётную запись</button>
      </form><p id="setup-message" class="message hidden" role="alert"></p>
    </section>
    <section id="login" class="card narrow hidden">
      <h2>Вход владельца</h2>
      <form id="login-form" class="stack">
        <label>Логин<input name="login" autocomplete="username" required></label>
        <label>Пароль<input name="password" type="password" autocomplete="current-password" required></label>
        <button>Войти</button>
      </form><p id="login-message" class="message hidden" role="alert"></p>
    </section>
    <section id="dashboard" class="hidden">
      <div class="row" style="margin-bottom:18px"><div><h2>Управление лицензиями</h2><p id="owner-login" class="muted" style="margin:5px 0 0"></p></div><button id="refresh" class="secondary">Обновить</button></div>
      <p id="dashboard-message" class="message hidden" role="alert"></p>
      <div class="grid">
        <section class="card"><h3>Новый клиент</h3><form id="customer-form" class="stack"><label>Имя или компания<input name="display_name" maxlength="120" required></label><label>Email или контакт (необязательно)<input name="contact" maxlength="240"></label><button>Создать клиента</button></form></section>
        <section class="card"><h3>Новая лицензия</h3><form id="license-form" class="stack"><label>Клиент<select name="customer_id" id="customer-select" required></select></label><button>Создать подписку</button></form></section>
        <section class="card"><h3>Код продления</h3><form id="code-form" class="stack"><label>Лицензия<select name="license_id" id="license-select" required></select></label><label>Срок<select name="duration"><option value="P1M">1 месяц</option><option value="P3M">3 месяца</option><option value="P6M">6 месяцев</option><option value="P12M">12 месяцев</option><option value="PERPETUAL">Бессрочно</option></select></label><button>Сгенерировать код</button></form><hr style="border:0;border-top:1px solid #e4ebf5;margin:18px 0"><form id="code-diagnostic-form" class="stack"><label>Проверка выданного кода<input name="activation_code" placeholder="SC-XXXXX-XXXXX-XXXXX-XXXXX" autocomplete="off" required></label><button class="secondary">Проверить в D1</button></form></section>
        <section class="card"><h3>Перенос устройства</h3><form id="transfer-form" class="stack"><label>Запрос с нового компьютера<select name="transfer_code" id="transfer-select" required></select></label><label>Лицензия клиента<select name="license_id" id="transfer-license-select" required></select></label><div id="transfer-summary" class="transfer-summary muted">Выберите запрос и лицензию, чтобы проверить перенос.</div><p class="warning">Перенос отключит старое устройство от новых проверок лицензии. Проекты и история не переносятся через облако — для них нужна резервная копия. Если старый компьютер потерян, лицензию всё равно можно перенести.</p><button>Одобрить перенос</button></form><p class="muted">После одобрения пользователь должен нажать «Завершить перенос» на новом компьютере.</p></section>
        <section class="card"><h3>Доступ к источникам</h3><form id="source-form" class="stack"><label>Лицензия<select name="license_id" id="source-license-select" required></select></label><div id="source-options" class="source-options" aria-live="polite"></div><button>Сохранить доступ</button></form><p class="muted">Изменения применятся после нажатия пользователем «Проверить сейчас» в приложении, либо при следующей проверке лицензии.</p></section>
        <section class="card"><h3>Удаление лицензии</h3><form id="delete-license-form" class="stack"><label>Лицензия<select name="license_id" id="delete-license-select" required></select></label><button class="danger">Удалить лицензию</button></form><p class="muted">Не используйте это для отключения площадки. Удаление необратимо отзывает лицензию и все её коды. После проверки в приложении пользователю потребуется новая лицензия.</p></section>
      </div>
      <section class="card" style="margin-bottom:18px"><h3>Лицензии</h3><div id="licenses" class="table-wrap"></div></section>
      <section class="card" style="margin-bottom:18px"><h3>Коды активации</h3><div id="codes" class="table-wrap"></div></section>
      <section class="card" style="margin-bottom:18px"><h3>Запросы переноса</h3><div id="transfers" class="table-wrap"></div></section>
      <section class="card" style="margin-bottom:18px"><h3>История устройств</h3><div id="devices" class="table-wrap"></div></section>
      <section class="card"><h3>Журнал действий</h3><div id="audit-events" class="table-wrap"></div></section>
    </section>
  </main>
  <script>
  (() => {
    const $ = id => document.getElementById(id);
    const state = { data: null };
    const message = (id, text, error = false) => { const el=$(id); el.textContent=text; el.classList.toggle('error',error); el.classList.toggle('hidden',!text); };
    const errorText = (payload, fallback) => payload && payload.error && payload.error.code ? payload.error.code : fallback;
    const request = async (path, options = {}) => {
      const response = await fetch(path, { credentials:'same-origin', ...options });
      const body = await response.json().catch(() => null);
      if (!response.ok || !body || !body.ok) throw new Error(errorText(body, 'REQUEST_FAILED'));
      return body.data;
    };
    const mutation = (path, body) => request(path, { method:'POST', headers:{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID(),'X-Request-Id':crypto.randomUUID()}, body:JSON.stringify(body) });
    const option = (value, label) => { const node=document.createElement('option'); node.value=value; node.textContent=label; return node; };
    const fill = (id, values, label) => { const select=$(id); select.replaceChildren(option('', values.length ? 'Выберите…' : 'Нет доступных записей')); select.firstChild.disabled=true; select.firstChild.selected=true; values.forEach(v => select.append(option(v.id, label(v)))); };
    const cell = value => { const td=document.createElement('td'); td.textContent=value == null || value === '' ? '—' : String(value); return td; };
    const table = (target, headings, rows) => { const t=document.createElement('table'), h=document.createElement('thead'), r=document.createElement('tr'); headings.forEach(x=>{const th=document.createElement('th');th.textContent=x;r.append(th)});h.append(r);t.append(h);const b=document.createElement('tbody');rows.forEach(row=>{const tr=document.createElement('tr');row.forEach(value=>tr.append(cell(value)));b.append(tr)});t.append(b);$(target).replaceChildren(t); };
    const render = data => {
      state.data=data;
      fill('customer-select', data.customers, x => x.display_name + (x.contact ? ' · ' + x.contact : ''));
      const activeLicenses=data.licenses.filter(x=>!x.deleted_at && x.status==='ACTIVE');
      const transferableLicenses=activeLicenses.filter(x=>x.active_device_id);
      const existingLicenses=data.licenses.filter(x=>!x.deleted_at);
      fill('license-select', activeLicenses, x => x.customer_name + ' · ' + x.kind + ' · ' + x.id.slice(0,8));
      fill('transfer-license-select', transferableLicenses, x => x.customer_name + ' · ' + x.kind + ' · ' + (x.active_device_label || x.active_device_id) + ' · ' + x.id.slice(0,8));
      fill('transfer-select', data.transfers.filter(x=>x.status==='PENDING' && Date.parse(x.expires_at)>Date.now()), x => x.public_code_hint + ' · ' + (x.customer_name || 'лицензия не назначена'));
      fill('source-license-select', existingLicenses, x => x.customer_name + ' · ' + x.kind + ' · ' + x.id.slice(0,8));
      fill('delete-license-select', existingLicenses, x => x.customer_name + ' · ' + x.kind + ' · ' + x.id.slice(0,8));
      renderSourceOptions();
      renderTransferSummary();
      const sourceNames = new Map(data.sources.map(x => [x.source_key, x.display_name]));
      const licenseSources = new Map(data.licenses.map(x => [x.id, []]));
      data.license_sources.forEach(x => { if (licenseSources.has(x.license_id)) licenseSources.get(x.license_id).push(sourceNames.get(x.source_key) || x.source_key); });
      table('licenses',['Клиент','Тип','Статус','Источники','Действует до','Устройство'],data.licenses.map(x=>[x.customer_name,x.kind,x.deleted_at ? 'УДАЛЕНА' : x.status,(licenseSources.get(x.id) || []).join(', '),x.perpetual ? 'Бессрочно' : x.expires_at,x.active_device_label || x.active_device_id]));
      table('codes',['Клиент','Тип','Срок','Подсказка','Статус'],data.activation_codes.map(x=>[x.customer_name,x.license_kind,x.makes_perpetual ? 'Бессрочно' : x.duration_months+' мес.',x.code_hint,x.used_at ? 'Использован' : 'Активен']));
      table('transfers',['Клиент','Код','Устройство','Статус','Истекает'],data.transfers.map(x=>[x.customer_name,x.public_code_hint,x.requested_label,x.status,x.expires_at]));
      table('devices',['Клиент','Лицензия','Устройство','Состояние','Последняя связь'],data.devices.map(x=>[x.customer_name,x.license_kind,x.label,x.is_active ? 'Активно' : 'Отключено',x.last_seen_at]));
      table('audit-events',['Время','Действие','Объект','Результат'],data.audit_events.map(x=>[x.created_at,x.action,x.target_type+' · '+(x.target_id || '—'),x.outcome]));
    };
    const renderSourceOptions = () => {
      const root=$('source-options');
      const data=state.data;
      if (!data) return;
      const selected=$('source-license-select').value;
      const granted=new Set(data.license_sources.filter(x=>x.license_id===selected).map(x=>x.source_key));
      root.replaceChildren();
      const available=data.sources.filter(x=>x.is_active);
      if (!selected) { root.textContent='Сначала выберите лицензию.'; return; }
      if (!available.length) { root.textContent='Нет доступных источников.'; return; }
      available.forEach(source => { const label=document.createElement('label'), input=document.createElement('input'), text=document.createElement('span'); label.className='source-option'; input.type='checkbox'; input.name='source_key'; input.value=source.source_key; input.checked=granted.has(source.source_key); text.textContent=source.display_name+' ('+source.source_key+')'; label.append(input,text); root.append(label); });
    };
    const renderTransferSummary = () => {
      const root=$('transfer-summary'), data=state.data;
      if (!data) return;
      const transfer=data.transfers.find(x=>x.id===$('transfer-select').value);
      const license=data.licenses.find(x=>x.id===$('transfer-license-select').value);
      if (!transfer || !license) { root.textContent='Выберите запрос и лицензию, чтобы проверить перенос.'; return; }
      const oldDevice=license.active_device_label || license.active_device_id || 'неизвестное устройство';
      const newDevice=transfer.requested_label || 'новый компьютер';
      root.textContent='Клиент: '+license.customer_name+'. Лицензия '+license.id.slice(0,8)+' будет перенесена с «'+oldDevice+'» на «'+newDevice+'». Срок лицензии не изменится.';
    };
    const load = async () => { const data=await request('/v1/owner/dashboard'); render(data); };
    const showDashboard = async login => { $('loading').classList.add('hidden'); $('setup').classList.add('hidden'); $('login').classList.add('hidden'); $('dashboard').classList.remove('hidden'); $('logout').classList.remove('hidden'); $('owner-login').textContent='Владелец: '+login; try { await load(); } catch(e) { message('dashboard-message','Не удалось загрузить данные: '+e.message,true); } };
    const init = async () => {
      try { const session=await request('/v1/owner/session'); return showDashboard(session.login); }
      catch (_) { const status=await request('/v1/owner/status').catch(()=>({configured:true})); $('loading').classList.add('hidden'); $(status.configured ? 'login' : 'setup').classList.remove('hidden'); }
    };
    const form = (id, handler, messageId) => $(id).addEventListener('submit', async event => { event.preventDefault(); const target=event.currentTarget; const button=target.querySelector('button'); button.disabled=true; message(messageId,''); try { const result=await handler(new FormData(target)); target.reset(); await load(); message(messageId,result && result.notice ? result.notice : 'Готово.'); } catch(e) { message(messageId,'Ошибка: '+e.message,true); } finally { button.disabled=false; } });
    form('setup-form', async f => { const data=await request('/v1/owner/bootstrap',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+f.get('token')},body:JSON.stringify({login:f.get('login'),password:f.get('password')})}); await showDashboard(data.login); }, 'setup-message');
    form('login-form', async f => { const data=await request('/v1/owner/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login:f.get('login'),password:f.get('password')})}); await showDashboard(data.login); }, 'login-message');
    form('customer-form', async f => { const data=await mutation('/v1/owner/customers',{display_name:f.get('display_name'),contact:f.get('contact') || undefined}); return data.already_exists ? {notice:'Такой клиент уже существует. Открыты актуальные данные.'} : undefined; }, 'dashboard-message');
    form('license-form', f => mutation('/v1/owner/licenses',{customer_id:f.get('customer_id')}), 'dashboard-message');
    form('code-form', async f => { const data=await mutation('/v1/owner/activation-codes',{license_id:f.get('license_id'),duration:f.get('duration')}); return {notice:'Код создан. Скопируйте и передайте пользователю: '+data.activation_code}; }, 'dashboard-message');
    form('code-diagnostic-form', async f => { const data=await mutation('/v1/owner/activation-codes/diagnose',{activation_code:f.get('activation_code')}); const labels={STABLE_V2:'Код найден: текущий стабильный формат.',LEGACY_CURRENT_PEPPER:'Код найден: старый формат с текущим CODE_PEPPER.',NOT_FOUND:'Точный код не найден.'}; const states={AVAILABLE:' Код доступен для активации.',USED:' Код уже использован.',EXPIRED:' Срок кода истёк.',LICENSE_NOT_ACTIVE:' Лицензия кода неактивна.',NOT_FOUND:''}; const hint=data.hint_matches.length ? ' Записей с такой подсказкой в D1: '+data.hint_matches.length+'.' : ' Записей с такой подсказкой в D1 нет.'; return {notice:labels[data.matched_by]+states[data.availability]+hint}; }, 'dashboard-message');
    form('source-form', f => mutation('/v1/owner/license-sources',{license_id:f.get('license_id'),source_keys:f.getAll('source_key')}), 'dashboard-message');
    form('delete-license-form', f => { const id=String(f.get('license_id') || ''); const license=state.data.licenses.find(x=>x.id===id); const label=license ? license.customer_name+' · '+id.slice(0,8) : id; if (!window.confirm('Удалить лицензию '+label+'? Это действие нельзя отменить.')) return {notice:'Удаление отменено.'}; return mutation('/v1/owner/licenses/delete',{license_id:id,confirmation:'DELETE'}).then(()=>({notice:'Лицензия удалена. После следующей проверки приложение очистит локальную лицензию.'})); }, 'dashboard-message');
    form('transfer-form', async f => {
      const transfer=state.data.transfers.find(x=>x.id===f.get('transfer_code'));
      const license=state.data.licenses.find(x=>x.id===f.get('license_id'));
      if (!transfer || !license) throw new Error('TRANSFER_SELECTION_REQUIRED');
      const oldDevice=license.active_device_label || license.active_device_id || 'неизвестное устройство';
      const newDevice=transfer.requested_label || 'новый компьютер';
      const confirmed=window.confirm('Подтвердить перенос лицензии?\n\nКлиент: '+license.customer_name+'\nСтарое устройство: '+oldDevice+'\nНовое устройство: '+newDevice+'\n\nСрок лицензии не изменится. Локальные проекты и история не переносятся. Старое устройство потеряет новые проверки лицензии и сможет работать только до окончания уже сохранённого автономного допуска.');
      if (!confirmed) return {notice:'Перенос отменён.'};
      await mutation('/v1/owner/transfers/approve',{transfer_id:transfer.id,license_id:license.id});
      return {notice:'Перенос одобрен. Попросите пользователя нажать «Завершить перенос» на новом компьютере.'};
    }, 'dashboard-message');
    $('source-license-select').addEventListener('change', renderSourceOptions);
    $('transfer-select').addEventListener('change', renderTransferSummary);
    $('transfer-license-select').addEventListener('change', renderTransferSummary);
    $('refresh').addEventListener('click', () => load().catch(e=>message('dashboard-message','Ошибка: '+e.message,true)));
    $('logout').addEventListener('click', async () => { try { await request('/v1/owner/logout',{method:'POST'}); location.reload(); } catch(e) { message('dashboard-message','Ошибка выхода: '+e.message,true); } });
    init().catch(e => { $('loading').textContent='Сервис недоступен: '+e.message; });
  })();
  </script>
</body></html>`;

export function ownerPage(): Response {
  return new Response(OWNER_APP, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
