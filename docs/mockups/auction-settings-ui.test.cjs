const {test}=require('node:test'),assert=require('node:assert/strict');
const {botOutcome,checkChannel,connectionFor,sourceView,settingsHTML,sourceNoticeHTML}=require('./auction-settings-ui.js');
const initial={bot:'offline',checks:{}};
const {effectiveInterval,newProjectDraft,participation,scheduledProjects}=require('./auction-settings-ui.js');
test('site interval overrides the app default without changing another site',()=>{
  const state={interval:180,siteIntervals:{Encar:null,Copart:60,IAA:null}};
  assert.deepEqual(effectiveInterval(state,'Copart'),{minutes:60,owner:'site'});
  assert.deepEqual(effectiveInterval({...state,interval:360},'IAA'),{minutes:360,owner:'app'});
  assert.deepEqual(effectiveInterval({...state,interval:360},'Copart'),{minutes:60,owner:'site'});
  assert.equal(state.interval,180);
});
test('new project defaults are copied only at creation; auctions have no Encar price or detail mode',()=>{
  const defaults={scan_mode:'FAST',scope:'all'},old=newProjectDraft('Encar',defaults);
  defaults.scan_mode='ACCURATE';defaults.scope='first';
  assert.equal(old.scan_mode,'FAST');assert.equal(old.scope,'all');
  assert.equal(newProjectDraft('Encar',defaults).scan_mode,'ACCURATE');
  for(const site of ['Copart','IAA']){const d=newProjectDraft(site,defaults);assert.equal(d.scope,'all');assert.equal(d.scan_mode,undefined);assert.equal(d.priceMode,undefined);assert.equal(d.publish,false);}
});
test('one project participation value drives the scheduler and source IDs remain isolated',()=>{
  const old={id:'p1',name:'A',auto:true},projects={Encar:[old],Copart:[{...old,auto:false}],IAA:[]};
  assert.deepEqual(scheduledProjects(projects).map(p=>[p.source,p.id]),[['Encar','p1']]);
  projects.Encar[0]=participation(old,false);projects.Copart[0]=participation(projects.Copart[0],true);
  assert.deepEqual(scheduledProjects(projects).map(p=>[p.source,p.id]),[['Copart','p1']]);
  assert.equal(old.auto,true);
});
test('connected bot alone never proves channel rights',()=>{
  const state=botOutcome(initial,'ready');
  assert.equal(state.bot,'ready');
  assert.equal(connectionFor(state,'Copart:p1','@auto_auctions_demo'),'unverified');
  assert.equal(connectionFor(initial,'Copart:p1','@auto_auctions_demo'),'offline');
});
test('rights belong to the checked channel and project, not another site or edited channel',()=>{
  const state=checkChannel({bot:'ready',checks:{}},'Copart:p1','https://t.me/auto_auctions_demo','ready');
  assert.equal(connectionFor(state,'Copart:p1','@auto_auctions_demo'),'ready');
  assert.equal(connectionFor(state,'IAA:p1','@auto_auctions_demo'),'unverified');
  assert.equal(connectionFor(state,'Copart:p1','@another_channel'),'unverified');
  assert.equal(connectionFor(checkChannel(state,'Copart:p1','@auto_auctions_demo','rights'),'Copart:p1','@auto_auctions_demo'),'rights');
  assert.equal(connectionFor(checkChannel(state,'Copart:p1','@auto_auctions_demo','error'),'Copart:p1','@auto_auctions_demo'),'error');
});
test('bot replacement/disconnection invalidates rights without mutating previous state',()=>{
  const old=checkChannel({bot:'ready',checks:{}},'Copart:p1','@auto_auctions_demo','ready');
  for(const outcome of ['offline','invalid','network','checking','ready']){
    const changed=botOutcome(old,outcome);
    assert.deepEqual(changed.checks,{});
    assert.equal(connectionFor(changed,'Copart:p1','@auto_auctions_demo'),outcome==='ready'?'unverified':'offline');
  }
  assert.equal(connectionFor(old,'Copart:p1','@auto_auctions_demo'),'ready');
});
test('invalid channels and an offline bot cannot be granted demo publication rights',()=>{
  assert.deepEqual(checkChannel(initial,'Copart:p1','@auto_auctions_demo','ready'),initial);
  const connected={bot:'ready',checks:{}};
  assert.deepEqual(checkChannel(connected,'Copart:p1','https://t.me/+invite','ready'),connected);
  assert.equal(connectionFor(checkChannel(connected,'Copart:p1','@auto_auctions_demo','unknown'),'Copart:p1','@auto_auctions_demo'),'unverified');
});
test('source failure prevents a new search; unknown states fail closed',()=>{
  assert.equal(sourceView('ready').canSearch,true);
  for(const state of ['unavailable','blocked','network','disabled','license','unverified','unknown'])assert.equal(sourceView(state).canSearch,false,state);
});
test('settings render RU/UK source states without confusing bot access and publication preference',()=>{
  const projects={Copart:[{id:'p1',name:'<img src=x>',channel:'@auto_auctions_demo',publish:true}],IAA:[]};
  for(const lang of ['ru','uk']){
    const html=settingsHTML({global:true,source:'Copart',lang,bot:{bot:'ready',checks:{}},availability:{Copart:'ready'},projects,tab:'telegram'});
    assert.ok(html.includes('&lt;img src=x&gt;'));
    assert.ok(html.includes(lang==='ru'?'Публикация приостановлена':'Публікацію призупинено'));
    assert.ok(!html.includes(lang==='ru'?'Готов к публикации · демо':'Готовий до публікації · демо'));
    for(const state of ['network','blocked','disabled','unverified','license','unavailable','unknown']){
      assert.ok(sourceNoticeHTML('Copart',state,lang).includes('role="status"'));
      assert.ok(settingsHTML({global:false,source:'Copart',lang,bot:initial,availability:{Copart:state},projects}).includes('settings-source-review'));
    }
    assert.equal(sourceNoticeHTML('Copart','ready',lang),'');
  }
});
