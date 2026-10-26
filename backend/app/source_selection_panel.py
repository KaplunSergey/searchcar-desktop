"""SearchCar-only controls inside the owned selection window, not the scan browser."""


def panel_content(draft, locale='ru'):
    uk = locale == 'uk'
    link_only = bool(draft.snapshot and draft.snapshot.get('source_key') == 'copart')
    messages = {
        'NO_SEARCH_CAPTURED': ('Условия ещё не получены. Примените фильтр на сайте, чтобы обновить результаты.',
                               'Умови ще не отримано. Застосуйте фільтр на сайті, щоб оновити результати.'),
        'SEARCH_RESULTS_NOT_READY': ('Фильтры получены. Ожидаем загрузки списка автомобилей.',
                                    'Фільтри отримано. Очікуємо завантаження списку автомобілів.'),
        'SITE_VERIFICATION_REQUIRED': ('Пройдите проверку сайта вручную. Затем дождитесь результатов.',
                                       'Пройдіть перевірку сайту вручну. Потім дочекайтеся результатів.'),
        'SITE_ACCESS_BLOCKED': ('Сайт ограничил доступ. Пока результаты недоступны, сохранить выбор нельзя.',
                                'Сайт обмежив доступ. Поки результати недоступні, зберегти вибір не можна.'),
        'SITE_CONSENT_REQUIRED': ('Выберите настройки cookies в окне сайта.', 'Виберіть налаштування cookies у вікні сайту.'),
        'SEARCH_FILTERS_UNSUPPORTED': ('Формат фильтров не распознан. Не сохраняем неполные условия.',
                                       'Формат фільтрів не розпізнано. Не зберігаємо неповні умови.'),
        'SEARCH_REQUEST_FAILED': ('Сайт не загрузил результаты. Повторно примените фильтры на сайте.',
                                  'Сайт не завантажив результати. Повторно застосуйте фільтри на сайті.'),
    }
    waiting = ('Ожидаем применения фильтров. Дождитесь обновления результатов.',
               'Очікуємо застосування фільтрів. Дочекайтеся оновлення результатів.')
    message = ('Фильтры получены. Можно сохранить выбор.', 'Фільтри отримано. Можна зберегти вибір.') if draft.can_use else messages.get(draft.issue, waiting)
    if link_only and draft.can_use:
        message = ('Ссылка поиска получена. Можно сохранить выбор.', 'Посилання пошуку отримано. Можна зберегти вибір.')
    return dict(ready=draft.can_use, message=message[uk],
                summary=draft.snapshot['summary'] if draft.snapshot else [],
                summary_label=(('Условия из ссылки' if not uk else 'Умови з посилання') if link_only else
                               ('Сохранённые условия' if not uk else 'Збережені умови')),
                button=('Использовать выбранные фильтры' if not uk else 'Використати вибрані фільтри'),
                collapse=('Свернуть панель' if not uk else 'Згорнути панель'),
                expand=('Развернуть панель' if not uk else 'Розгорнути панель'),
                hint=(('Сохраняем поиск из адресной строки Copart. Фильтры, которые не отражены в ссылке, не сохраняются.' if not uk else
                       'Зберігаємо пошук з адресного рядка Copart. Фільтри, які не відображені в посиланні, не зберігаються.') if link_only else
                      ('Вернёмся в SearchCar. Проект и поиск пока не запускаются.' if not uk else
                       'Повернемося в SearchCar. Проєкт і пошук поки не запускаються.')))


PANEL_SCRIPT = r"""(value) => {
  let host = document.getElementById('searchcar-selection-panel');
  if (!host || !host.shadowRoot) {
    if (host) host.remove();
    host = document.createElement('div');
    host.id = 'searchcar-selection-panel';
    host.style.cssText = 'all:initial!important;position:fixed!important;right:12px!important;bottom:12px!important;width:min(420px,calc(100vw - 24px))!important;z-index:2147483647!important;color-scheme:light!important;';
    const root = host.attachShadow({mode:'open'});
    root.innerHTML = `<style>
      :host {font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#17233b}
      * {box-sizing:border-box} section {font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#17233b;background:#fff;border:1px solid #cddbf2;border-radius:14px;padding:16px;box-shadow:0 8px 32px #13213b33;max-height:calc(100dvh - 24px);overflow:auto}
      header {display:flex;align-items:center;justify-content:space-between;gap:12px}
      strong {font-size:15px;color:#2458d3} p {margin:12px 0;overflow-wrap:anywhere}
      ul {padding-left:20px;margin:6px 0 12px;max-height:110px;overflow:auto;overflow-wrap:anywhere}
      .hint {font-size:12px;color:#53627b;margin:10px 0 0}
      button {font:inherit;cursor:pointer;border-radius:8px;border:1px solid #cddbf2;padding:8px 12px;background:#fff;color:#334563}
      button:focus-visible {outline:3px solid #86aaff;outline-offset:2px}
      .confirm {width:100%;background:#2862e9;color:#fff;border-color:#2862e9;font-weight:600;padding:10px 12px;white-space:normal}
      .confirm:disabled {background:#eef2f8;color:#596b86;border-color:#d8e1ef;cursor:not-allowed}
      .toggle {padding:3px 10px;font-size:20px;line-height:24px} [hidden] {display:none!important}
    </style><section role="region" aria-label="SearchCar"><header><strong>SearchCar</strong><button type="button" class="toggle" aria-expanded="true">−</button></header>
      <div class="body"><p class="status" role="status" aria-live="polite"></p><div class="summary" hidden><b></b><ul></ul></div>
      <button type="button" class="confirm" disabled></button><p class="hint"></p></div></section>`;
    // Keep SearchCar clicks out of the site's own filter handlers.
    for (const event of ['click','input','change','keydown']) host.addEventListener(event,e=>e.stopPropagation());
    root.querySelector('.toggle').addEventListener('click', () => {
      host.dataset.collapsed = host.dataset.collapsed === 'true' ? 'false' : 'true';
      root.querySelector('.body').hidden = host.dataset.collapsed === 'true';
      root.querySelector('.toggle').setAttribute('aria-expanded', String(host.dataset.collapsed !== 'true'));
      root.querySelector('.toggle').textContent = host.dataset.collapsed === 'true' ? '+' : '−';
      root.querySelector('.toggle').setAttribute('aria-label', host.dataset.collapsed === 'true' ? host.dataset.expand : host.dataset.collapse);
    });
    root.querySelector('.confirm').addEventListener('click', async e => {
      if (!e.isTrusted || e.currentTarget.disabled) return;
      e.currentTarget.disabled = true;
      try { await window.__searchcarSelectionConfirm(); } catch (_) { /* Polling will restore the current state. */ }
    });
    document.documentElement.append(host);
  }
  const root = host.shadowRoot;
  host.dataset.expand = value.expand; host.dataset.collapse = value.collapse;
  root.querySelector('.toggle').setAttribute('aria-label', host.dataset.collapsed === 'true' ? value.expand : value.collapse);
  const status = root.querySelector('.status');
  if (status.textContent !== value.message) status.textContent = value.message;
  const button = root.querySelector('.confirm');
  button.textContent = value.button; button.disabled = !value.ready;
  root.querySelector('.hint').textContent = value.hint;
  const summary = root.querySelector('.summary');
  summary.hidden = !value.summary.length;
  summary.querySelector('b').textContent = value.summary_label;
  const items = JSON.stringify(value.summary);
  if (summary.dataset.items !== items) {
    summary.dataset.items = items;
    summary.querySelector('ul').replaceChildren(...value.summary.map(text=>{
      const item = document.createElement('li'); item.textContent = text; return item;
    }));
  }
}"""
