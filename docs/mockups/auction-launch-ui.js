// Prototype-only queue: no source requests, persistence, scheduler or Telegram.
import {ui,text,icon} from './auction-card-ui.js';
const active=r=>['running','waiting'].includes(r.state);
const same=(a,b)=>a.source===b.source&&a.project===b.project;
export function planLaunch(catalog, selected, queue) {
  const skipped=[],groups=[];
  for(const site of catalog.filter(s=>selected.includes(s.source))) {
    const ready=[];
    if(!site.projects.length)skipped.push({source:site.source,reason:'empty'});
    for(const project of site.projects) {
      const row={source:site.source,project:project.id,name:project.name,config:{...project}};
      const busy=queue.find(q=>active(q)&&same(q,row));
      if(site.state!=='ready')skipped.push({...row,reason:site.state});
      else if(busy)skipped.push({...row,reason:'busy',task:busy.id,batch:busy.batch});
      else ready.push(row);
    }
    groups.push(ready);
  }
  const ready=[];
  for(let i=0;groups.some(g=>i<g.length);i++)for(const group of groups)if(group[i])ready.push(group[i]);
  return {ready,skipped};
}
function promote(queue) {
  if(!queue.some(r=>r.state==='running')){const next=queue.find(r=>r.state==='waiting');if(next)next.state='running';}
  return queue;
}
export function enqueueDemo(queue, plan, batch) {
  const next=queue.map(r=>({...r}));
  for(const row of plan.ready)if(!next.some(q=>active(q)&&same(q,row)))next.push({...row,id:batch+'-'+row.source+'-'+row.project,batch,state:'waiting'});
  return promote(next);
}
export function advanceDemo(queue, blocked=false) {
  const next=queue.map(r=>({...r})),running=next.find(r=>r.state==='running');
  if(running){running.state=blocked?'failed':'succeeded';if(blocked)for(const row of next)if(row.source===running.source&&row.state==='waiting'){row.state='skipped';row.reason='blocked';}}
  return promote(next);
}
export function cancelDemo(queue, target) {
  return promote(queue.map(r=>({...r,state:active(r)&&Object.entries(target).every(([key,value])=>r[key]===value)?'canceled':r.state})));
}

export function createLaunchPreview({projects,context,modal,availability={},onAvailabilityChange=()=>{}}) {
  const $=id=>document.getElementById(id),e=ui.escape;
  let queue=[],batch=0,tab='choose',chosen=new Set(),selection={},confirmation=null,notice='';
  const availabilityState=site=>!availability[site]||availability[site]==='ready'?'ready':availability[site]==='license'?'license':'unavailable';
  const t=(ru,uk)=>text(ru,uk,context().lang);
  const siteEntries=()=>['Encar','Copart','IAA'].map(site=>[site,projects[site]]);
  const reason=key=>({busy:t('Уже работает или в очереди','Уже працює або в черзі'),empty:t('Нет выбранных проектов','Немає вибраних проєктів'),unavailable:t('Источник недоступен · демо','Джерело недоступне · демо'),license:t('Нет доступа по лицензии · демо','Немає доступу за ліцензією · демо')}[key]||key);
  const status=key=>({waiting:t('В очереди','У черзі'),running:t('Обновление','Оновлення'),succeeded:t('Завершено','Завершено'),canceled:t('Отменено','Скасовано'),failed:t('Блокировка источника','Блокування джерела'),skipped:t('Пропущено: источник заблокирован','Пропущено: джерело заблоковане')}[key]);
  const catalog=()=>siteEntries().map(([source,ps])=>({source,state:availabilityState(source),projects:ps.filter(p=>selection[source]?.has(p.id))}));
  const preview=()=>planLaunch(catalog(),[...chosen],queue);
  const affected=target=>queue.filter(r=>active(r)&&Object.entries(target).every(([key,value])=>r[key]===value));
  function controls() {
    if(!$('launch-controls'))return;
    $('launch-controls').innerHTML=`<button id="launch-sites" title="${e(t('Обновить один или несколько сайтов · макет','Оновити один або кілька сайтів · макет'))}">${e(t('Обновить данные','Оновити дані'))}</button>${queue.length?`<button id="launch-queue" aria-label="${e(t('Открыть демо-очередь','Відкрити демо-чергу'))}">${icon('clock')}${queue.filter(active).length}</button>`:''}`;
    $('launch-sites').onclick=()=>open();if($('launch-queue'))$('launch-queue').onclick=()=>open({queueOnly:true});
    const available=availabilityState(context().source)==='ready';
    document.querySelectorAll('[data-launch-site]').forEach(b=>{b.disabled=!available;b.onclick=()=>open({site:context().source,ids:context().selected.length?context().selected:null});});
    document.querySelectorAll('[data-launch-current]').forEach(b=>{b.disabled=!available;b.onclick=()=>open({site:context().source,ids:[context().project]});});
    document.querySelectorAll('[data-launch-all]').forEach(b=>{b.disabled=!available;b.onclick=()=>open({site:context().source});});
  }
  function open({site=context().source,ids=null,queueOnly=false}={}) {
    chosen=new Set([site]);selection=Object.fromEntries(siteEntries().map(([s,ps])=>[s,new Set(ps.filter(p=>s!==site||!ids||ids.includes(p.id)).map(p=>p.id))]));
    tab=queueOnly?'queue':'choose';confirmation=null;notice='';
    modal(t('Обновить данные','Оновити дані'),'');$('dialog').classList.add('launch-dialog');$('dialog-close').hidden=true;draw('launch-close');
  }
  $('dialog').addEventListener('close',()=>{if($('dialog').classList.contains('launch-dialog'))($('launch-queue')||$('launch-sites'))?.focus();});
  function draw(focus) {
    const plan=preview(),running=queue.find(r=>r.state==='running'),count=queue.filter(active).length;
    $('dialog-title').textContent=confirmation?t('Подтвердить отмену','Підтвердити скасування'):t('Обновить данные','Оновити дані');
    const sites=siteEntries().map(([site,ps])=>`${site==='Encar'?`<h3 class="launch-family">${e(t('Площадки','Майданчики'))}</h3>`:site==='Copart'?`<h3 class="launch-family">${e(t('Аукционы','Аукціони'))}</h3>`:''}<section class="launch-site"><div class="launch-site-head"><label><input type="checkbox" data-launch-source="${site}" ${chosen.has(site)?'checked':''}><b>${site}</b><small>${e(t('Проектов','Проєктів'))}: ${ps.length}</small></label><select data-launch-state="${site}" aria-label="${e(t('Демо-доступность','Демо-доступність')+' '+site)}">${[['ready',t('Готов · демо','Готовий · демо')],['unavailable',t('Недоступен · демо','Недоступний · демо')],['license',t('Нет лицензии · демо','Немає ліцензії · демо')]].map(([key,label])=>`<option value="${key}" ${availabilityState(site)===key?'selected':''}>${e(label)}</option>`).join('')}</select></div>${chosen.has(site)?`<div class="launch-projects">${ps.length?ps.map(p=>`<label><input type="checkbox" data-launch-project="${e(p.id)}" data-project-source="${site}" ${selection[site].has(p.id)?'checked':''}><span>${e(p.name)}</span>${queue.some(q=>active(q)&&q.source===site&&q.project===p.id)?`<small>${e(reason('busy'))}</small>`:''}</label>`).join(''):`<small>${e(t('На сайте пока нет проектов','На сайті ще немає проєктів'))}</small>`}</div>`:''}</section>`).join('');
    const selectionBody=`<div class="launch-select-all"><button class="editor-text-button" id="launch-select-all">${e(t('Все доступные · демо','Усі доступні · демо'))}</button><small>${e(t('Выбирает сайты, не запускает поиск','Вибирає сайти, не запускає пошук'))}</small></div>${sites}<section class="launch-summary" aria-live="polite"><b>${e(t('Будут добавлены в очередь','Буде додано до черги'))}: ${plan.ready.length}</b><p>${plan.ready.length?plan.ready.map(r=>e(r.source+' · '+r.name)).join('<br>'):e(t('Выберите доступный сайт и свободный проект. Пустой запуск не создаётся.','Виберіть доступний сайт і вільний проєкт. Порожній запуск не створюється.'))}</p>${plan.skipped.length?`<b>${e(t('Не добавляем','Не додаємо'))}: ${plan.skipped.length}</b><ul>${plan.skipped.map(r=>`<li>${e(r.source+(r.name?' · '+r.name:''))}: ${e(reason(r.reason))}${r.task?` <button class="editor-text-button" data-view-task="${e(r.task)}">${e(t('В очередь','До черги'))}${icon('external')}</button>`:''}</li>`).join('')}</ul>`:''}</section>`;
    const cancelButton=(target,label)=>`<button class="editor-text-button launch-danger" data-queue-cancel="${e(JSON.stringify(target))}">${e(label)}</button>`;
    const queueBody=queue.length?`<p class="launch-queue-summary">${e(t('В работе','У роботі'))}: ${running?1:0} · ${e(t('Ожидают','Очікують'))}: ${queue.filter(r=>r.state==='waiting').length} · ${e(t('Завершены','Завершені'))}: ${queue.filter(r=>r.state==='succeeded').length}</p>${[...new Set(queue.map(r=>r.batch))].map(b=>{const rows=queue.filter(r=>r.batch===b);return `<section class="launch-batch"><header><b>${e(t('Запуск','Запуск'))} ${e(b)}</b>${rows.some(active)?cancelButton({batch:b},t('Отменить запуск','Скасувати запуск')):''}</header><div class="launch-site-cancels">${[...new Set(rows.filter(active).map(r=>r.source))].map(s=>cancelButton({batch:b,source:s},t('Отменить','Скасувати')+' '+s)).join('')}</div>${rows.map(r=>`<div class="launch-task" tabindex="-1" id="task-${e(r.id)}"><div><small>${e(r.source)}</small><b>${e(r.name)}</b></div><span class="launch-task-state ${r.state}">${e(status(r.state))}</span>${active(r)?cancelButton({batch:b,source:r.source,project:r.project},t('Отменить проект','Скасувати проєкт')):''}</div>`).join('')}</section>`;}).join('')}`:`<div class="launch-empty"><b>${e(t('Очередь пуста','Черга порожня'))}</b><p>${e(t('Выберите сайты и подтвердите демо-запуск.','Виберіть сайти та підтвердьте демо-запуск.'))}</p></div>`;
    const confirmBody=confirmation?`<div class="launch-summary warning"><b>${e(t('Отменяем только','Скасовуємо лише'))}: ${affected(confirmation).length}</b><p>${affected(confirmation).map(r=>e(r.source+' · '+r.name+' · '+r.batch)).join('<br>')}</p><p>${e(t('Остальные задачи продолжатся. Уже завершённые результаты не удаляются.','Решта завдань продовжиться. Уже завершені результати не видаляються.'))}</p></div>`:'';
    $('dialog-body').innerHTML=`<div class="launch-shell"><button class="editor-close" id="launch-close" aria-label="${e(t('Закрыть выбор сайтов','Закрити вибір сайтів'))}">${icon('close')}</button><p class="launch-intro">${e(t('Только макет. Последовательная очередь — предложение; реальный поиск и Telegram не запускаются.','Лише макет. Послідовна черга — пропозиція; реальний пошук і Telegram не запускаються.'))}</p>${confirmation?'':`<nav class="launch-tabs"><button id="launch-choose-tab" aria-pressed="${tab==='choose'}">${e(t('Выбор сайтов','Вибір сайтів'))}</button><button id="launch-queue-tab" aria-pressed="${tab==='queue'}">${e(t('Очередь','Черга'))} · ${count}</button></nav>`}<div class="launch-scroll">${notice?`<p class="launch-notice" role="status">${e(notice)}</p>`:''}${confirmation?confirmBody:tab==='choose'?selectionBody:queueBody}${tab==='queue'&&!confirmation&&running?`<div class="launch-lab"><b>${e(t('Управление состояниями · только демо','Керування станами · лише демо'))}</b><button class="app-button" id="launch-step">${e(t('Завершить текущий проект · демо','Завершити поточний проєкт · демо'))}</button><button class="app-button" id="launch-block">${e(t('Блокировка текущего сайта · демо','Блокування поточного сайту · демо'))}</button></div>`:''}<p class="launch-footnote">${e(t('Сеанс очереди отдельный от примеров истории. Ничего не сохраняется на диск; перезагрузка сбросит очередь. Демо-доступность не проверяет сайт или лицензию.','Сеанс черги окремий від прикладів історії. Нічого не зберігається на диск; перезавантаження скине чергу. Демо-доступність не перевіряє сайт або ліцензію.'))}</p></div><footer class="launch-footer">${confirmation?`<button class="app-button" id="launch-back">${e(t('Назад','Назад'))}</button><button class="app-button primary" id="launch-confirm-cancel">${e(t('Отменить только эти · демо','Скасувати лише ці · демо'))}</button>`:`<button class="app-button" id="launch-dismiss">${e(t('Закрыть','Закрити'))}</button>${tab==='choose'?`<button class="app-button primary" id="launch-confirm" ${plan.ready.length?'':'disabled'}>${e(t('В очередь · демо','До черги · демо'))} (${plan.ready.length})</button>`:''}`}</footer></div>`;
    $('launch-close').onclick=()=>$('dialog').close();if($('launch-dismiss'))$('launch-dismiss').onclick=()=>$('dialog').close();
    if($('launch-choose-tab'))$('launch-choose-tab').onclick=()=>{tab='choose';notice='';draw('launch-choose-tab');};if($('launch-queue-tab'))$('launch-queue-tab').onclick=()=>{tab='queue';draw('launch-queue-tab');};
    document.querySelectorAll('[data-launch-source]').forEach(b=>b.onchange=()=>{b.checked?chosen.add(b.dataset.launchSource):chosen.delete(b.dataset.launchSource);draw('[data-launch-source="'+b.dataset.launchSource+'"]');});
    document.querySelectorAll('[data-launch-project]').forEach(b=>b.onchange=()=>{const set=selection[b.dataset.projectSource];b.checked?set.add(b.dataset.launchProject):set.delete(b.dataset.launchProject);draw('[data-launch-project="'+b.dataset.launchProject+'"][data-project-source="'+b.dataset.projectSource+'"]');});
    document.querySelectorAll('[data-launch-state]').forEach(b=>b.onchange=()=>{availability[b.dataset.launchState]=b.value;onAvailabilityChange();draw('[data-launch-state="'+b.dataset.launchState+'"]');});
    if($('launch-select-all'))$('launch-select-all').onclick=()=>{chosen=new Set(catalog().filter(s=>s.state==='ready').map(s=>s.source));draw('launch-select-all');};
    if($('launch-confirm'))$('launch-confirm').onclick=()=>{const fresh=preview();if(!fresh.ready.length)return;queue=enqueueDemo(queue,fresh,'D-'+String(++batch).padStart(3,'0'));tab='queue';notice=t('Добавлено только в демо-очередь. Один проект выполняется, остальные ожидают.','Додано лише до демо-черги. Один проєкт виконується, решта очікують.');controls();draw('launch-queue-tab');};
    document.querySelectorAll('[data-view-task]').forEach(b=>b.onclick=()=>{tab='queue';draw('task-'+b.dataset.viewTask);document.getElementById('task-'+b.dataset.viewTask)?.scrollIntoView({block:'nearest'});});
    document.querySelectorAll('[data-queue-cancel]').forEach(b=>b.onclick=()=>{confirmation=JSON.parse(b.dataset.queueCancel);draw('launch-back');});
    if($('launch-back'))$('launch-back').onclick=()=>{confirmation=null;draw('launch-queue-tab');};
    if($('launch-confirm-cancel'))$('launch-confirm-cancel').onclick=()=>{queue=cancelDemo(queue,confirmation);confirmation=null;notice=t('Отменены только выбранные демо-задачи.','Скасовано лише вибрані демо-завдання.');controls();draw('launch-queue-tab');};
    if($('launch-step'))$('launch-step').onclick=()=>{queue=advanceDemo(queue);notice=t('Демо-проект завершён; следующий получил слот.','Демо-проєкт завершено; наступний отримав слот.');controls();draw('launch-queue-tab');};
    if($('launch-block'))$('launch-block').onclick=()=>{availability[running.source]='blocked';queue=advanceDemo(queue,true);onAvailabilityChange();notice=t('Блокировка только '+running.source+'. Остальные сайты продолжаются.','Блокування лише '+running.source+'. Решта сайтів продовжується.');controls();draw('launch-queue-tab');};
    if(focus)(document.getElementById(focus)||document.querySelector(focus))?.focus();
  }
  return {bind:controls,open};
}
