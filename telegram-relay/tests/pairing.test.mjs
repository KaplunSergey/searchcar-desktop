import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, beforeEach, test } from 'node:test';
import { Miniflare } from 'miniflare';
import { webcrypto } from 'node:crypto';

const enc=new TextEncoder();
const licenseId='10000000-0000-4000-8000-000000000001';
const deviceId='20000000-0000-4000-8000-000000000001';
const webhookSecret='fixture-webhook-secret-not-a-real-credential';
let mf,db,licenses,device;
let outbound=[], deliveryReply={ok:true,result:{message_id:701,chat:{id:555,type:'private'}}};
let deliveryAllowed=false;const unexpectedOutbound=[];
let chatFixture=null;const chatReads=[];
let keyboardReply='success';const keyboardRequests=[];
function canonical(v) {
  if(v===null || typeof v!=='object') return JSON.stringify(v);
  if(Array.isArray(v)) return `[${v.map(canonical).join(',')}]`;
  return `{${Object.keys(v).sort().map(k=>`${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}`;
}
async function newDevice() {
  const keys=await webcrypto.subtle.generateKey({name:'Ed25519'},true,['sign','verify']);
  const raw=new Uint8Array(await webcrypto.subtle.exportKey('raw',keys.publicKey));
  const prefix=enc.encode('SEARCHCAR-DEVICE-FINGERPRINT-V1\n');
  const input=new Uint8Array(prefix.length+raw.length);input.set(prefix);input.set(raw,prefix.length);
  return {keys,device:{public_key:Buffer.from(raw).toString('base64url'),fingerprint_hash:Buffer.from(await webcrypto.subtle.digest('SHA-256',input)).toString('hex'),label:'Fixture'}};
}
async function signed(action,extra={},who=device,outer={}) {
  const body={protocol_version:1,request_id:crypto.randomUUID(),requested_at:new Date().toISOString(),app_version:'0.4.1',
    license_id:licenseId,device_id:deviceId,device:who.device,audience:'searchcar-telegram-pairing-v1',
    telegram:{action,owner_id:7,project_id:9,...extra},...outer};
  const proof=await webcrypto.subtle.sign('Ed25519',who.keys.privateKey,enc.encode('SEARCHCAR-DEVICE-REQUEST-V1\n'+canonical(body)));
  return {...body,proof:Buffer.from(proof).toString('base64url')};
}
async function post(path,body,headers={}) {
  const response=await mf.dispatchFetch('https://pairing.test'+path,{method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});
  return {status:response.status,data:await response.json(),headers:response.headers};
}
async function api(action,extra={},who=device) {return post('/v1/pairings',await signed(action,extra,who));}
async function start(extra={}) {
  const result=await api('start',extra);assert.equal(result.status,200,JSON.stringify(result.data));return result.data.data;
}
let updateId=0;
function update(startUrl,chat=555,extra={}) {
  return {update_id:++updateId,message:{message_id:updateId,date:Math.floor(Date.now()/1000),
    from:{id:chat,is_bot:false,first_name:'Alex'},chat:{id:chat,type:'private'},text:'/start '+new URL(startUrl).searchParams.get('start'),...extra}};
}
async function webhook(body,secret=webhookSecret) {return post('/v1/telegram/webhook',body,{'x-telegram-bot-api-secret-token':secret});}
before(async()=>{
  mf=new Miniflare({workers:[{name:'relay',modules:true,scriptPath:new URL('../dist/index.js',import.meta.url).pathname,compatibilityDate:'2026-05-15',
    compatibilityFlags:['nodejs_compat'],d1Databases:{PAIRING_DB:'pairing-fixture'},
    serviceBindings:{LICENSE_AUTH:{name:'license',entrypoint:'TelegramAuthorization'}},
    bindings:{PAIRING_ENABLED:'1',CHAT_PAIRING_ENABLED:'1',BOT_ID:'123456',TELEGRAM_WEBHOOK_SECRET:webhookSecret,DELIVERY_ENABLED:'1',TELEGRAM_BOT_TOKEN:'123456:fixture-not-a-real-secret'},
    outboundService:async request=>{
      const method=new URL(request.url).pathname.split('/').at(-1);
      if(method==='sendMessage') {
        const body=await request.clone().json();
        if(body.reply_markup?.keyboard || body.reply_markup?.inline_keyboard?.[0]?.[0]?.url?.startsWith('https://t.me/search_car_app_bot?startgroup=')) {
          assert.match(request.url,/^https:\/\/api\.telegram\.org\/bot123456:fixture-not-a-real-secret\/sendMessage$/);
          keyboardRequests.push(body);
          if(keyboardReply==='network') throw Error('fixture secret transport');
          if(keyboardReply==='redirect') return new Response(null,{status:302,headers:{location:'https://untrusted.test'}});
          if(keyboardReply==='oversize') return new Response('x'.repeat(65537));
          if(keyboardReply==='malformed') return new Response('not json');
          if(keyboardReply==='wrong-chat') return Response.json({ok:true,result:{message_id:702,chat:{id:999,type:'private'}}});
          return Response.json(keyboardReply==='success'?{ok:true,result:{message_id:702,chat:{id:body.chat_id,type:'private'}}}:keyboardReply,
            {status:keyboardReply==='success'?200:keyboardReply.error_code});
        }
      }
      if(['getChat','getChatMember'].includes(method)) {
        assert.ok(chatFixture,'No unexpected chat checks');
        assert.match(request.url,/^https:\/\/api\.telegram\.org\/bot123456:fixture-not-a-real-secret\/getChat(?:Member)?$/);
        const body=await request.json();chatReads.push({method,...body});
        assert.equal(body.chat_id,chatFixture.id);
        if(chatFixture.fault==='network') throw Error('fixture secret transport');
        if(chatFixture.fault==='redirect') return new Response(null,{status:302,headers:{location:'https://untrusted.test'}});
        if(chatFixture.fault==='oversize') return new Response('x'.repeat(65537));
        if(chatFixture.fault==='denied') return Response.json({ok:false,error_code:403,description:'private secret'},{status:403});
        if(chatFixture.fault==='rate') return Response.json({ok:false,error_code:429,parameters:{retry_after:37}},{status:429});
        const bot=body.user_id===123456;
        const status=bot?chatFixture.botStatus||'administrator':chatFixture.userStatus||'administrator';
        const result=method==='getChat'?{id:chatFixture.id,type:chatFixture.type,title:'Verified team'}:
          {user:{id:body.user_id,is_bot:bot},status,
           ...(status==='creator'?{}:{can_manage_chat:bot?chatFixture.botManage!==false:chatFixture.userManage!==false}),
           can_post_messages:bot?chatFixture.botPost!==false:chatFixture.userPost!==false};
        return Response.json({ok:true,result});
      }
      if(!deliveryAllowed) {unexpectedOutbound.push(request.url);throw Error('Pairing must not call any external API');}
      assert.match(request.url,/^https:\/\/api\.telegram\.org\/bot123456:fixture-not-a-real-secret\/send(?:Message|Photo)$/);
      const body=await request.json();outbound.push({url:request.url,body});
      if(deliveryReply==='network') throw Error('fixture transport failed');
      if(deliveryReply==='redirect') return new Response(null,{status:302,headers:{location:'https://untrusted.test'}});
      if(deliveryReply==='oversize') return new Response('x'.repeat(70000));
      if(deliveryReply==='malformed') return new Response('not json');
      return Response.json(deliveryReply,{status:deliveryReply.ok?200:deliveryReply.error_code});
    }},{name:'license',modules:true,scriptPath:process.env.SEARCHCAR_LICENSE_AUTH_TEST_BUNDLE
        ?? new URL('../../license-service/dist/index.js',import.meta.url).pathname,
      compatibilityDate:'2026-05-15',d1Databases:{LICENSE_DB:'license-fixture'},
      outboundService:()=>{throw Error('Authorization cannot make outbound calls');}},
    {name:'auth-probe',modules:true,compatibilityDate:'2026-05-15',
      serviceBindings:{AUTH:{name:'license',entrypoint:'TelegramAuthorization'}},
      script:`export default {async fetch(request,env){return Response.json(await env.AUTH.authorize(await request.json()));}};`}]});
  await mf.ready;db=await mf.getD1Database('PAIRING_DB','relay');licenses=await mf.getD1Database('LICENSE_DB','license');
  for(const sql of (await readFile(new URL('../migrations/0001_private_pairing.sql',import.meta.url),'utf8')).split(';').map(s=>s.trim()).filter(Boolean)) await db.prepare(sql).run();
  for(const sql of (await readFile(new URL('../migrations/0002_recipient_reuse.sql',import.meta.url),'utf8')).split(';').map(s=>s.trim()).filter(Boolean)) await db.prepare(sql).run();
  for(const sql of (await readFile(new URL('../migrations/0003_delivery_receipts.sql',import.meta.url),'utf8')).split(';').map(s=>s.trim()).filter(Boolean)) await db.prepare(sql).run();
  for(const sql of (await readFile(new URL('../migrations/0004_chat_pairing.sql',import.meta.url),'utf8')).split(';').map(s=>s.trim()).filter(Boolean)) await db.prepare(sql).run();
  await licenses.exec(`CREATE TABLE licenses(id TEXT PRIMARY KEY,status TEXT,expires_at TEXT,perpetual INTEGER,deleted_at TEXT);
    CREATE TABLE devices(id TEXT PRIMARY KEY,license_id TEXT,public_key TEXT,fingerprint_hash TEXT,is_active INTEGER);`);
  device=await newDevice();
  await licenses.prepare('INSERT INTO licenses VALUES (?,?,?,?,?)').bind(licenseId,'ACTIVE','2099-01-01T00:00:00Z',0,null).run();
  await licenses.prepare('INSERT INTO devices VALUES (?,?,?,?,1)').bind(deviceId,licenseId,device.device.public_key,device.device.fingerprint_hash).run();
});
beforeEach(async()=>{deliveryAllowed=false;chatFixture=null;chatReads.length=0;keyboardReply='success';keyboardRequests.length=0;await db.prepare('DELETE FROM security_windows').run();});
after(async()=>{try{assert.deepEqual(unexpectedOutbound,[]);}finally{await mf?.dispose();}});

test('authorized staging enables manual delivery with required secrets, keeping production disabled and license DB isolated',async()=>{
  const config=JSON.parse(await readFile(new URL('../wrangler.jsonc',import.meta.url),'utf8'));
  const stage=config.env?.staging;
  assert.ok(stage,'Separate staging environment required');
  assert.equal(stage.name,'searchcar-telegram-relay-staging');
  assert.equal(stage.vars.PAIRING_ENABLED,'1');assert.equal(stage.vars.DELIVERY_ENABLED,'1');
  assert.equal(stage.vars.BOT_ID,'8996925581');
  assert.equal(stage.workers_dev,true);assert.equal(stage.preview_urls,false);
  assert.deepEqual(stage.d1_databases.map(db=>db.binding),['PAIRING_DB']);
  assert.deepEqual(config.d1_databases.map(db=>db.binding),['PAIRING_DB']);
  for(const cfg of [config,stage])assert.deepEqual(cfg.services,[{
    binding:'LICENSE_AUTH',service:'searchcar-license-service',entrypoint:'TelegramAuthorization'}]);
  assert.deepEqual([...stage.secrets.required].sort(),['TELEGRAM_BOT_TOKEN','TELEGRAM_WEBHOOK_SECRET']);
  assert.equal(config.vars.PAIRING_ENABLED,'0');assert.equal(config.workers_dev,false);
  assert.equal(config.vars.DELIVERY_ENABLED,'0');
  assert.equal(config.vars.CHAT_PAIRING_ENABLED,'0');assert.equal(stage.vars.CHAT_PAIRING_ENABLED,'1');
});

test('group Start opens native bot addition with minimal rights and a claimant-bound nonce, not the owner-hiding chat filter',async()=>{
  const pair=await start({project_id:201,target_kind:'group'});
  assert.equal(pair.target_kind,'group');
  const response=await webhook(update(pair.start_url,777));
  assert.equal(keyboardRequests.length,1,'Start must await a direct Bot API request, not an unchecked webhook reply');
  assert.deepEqual(response.data,{ok:true});
  assert.equal(keyboardRequests[0].chat_id,777);
  const markup=keyboardRequests[0].reply_markup;
  assert.equal(markup.keyboard,undefined,'Native add-bot picker avoids user-admin cache filtering');
  const link=new URL(markup.inline_keyboard[0][0].url);
  assert.equal(link.origin,'https://t.me');assert.equal(link.pathname,'/search_car_app_bot');
  assert.equal(link.searchParams.get('startgroup'),new URL(pair.start_url).searchParams.get('start'));
  assert.equal(link.searchParams.get('admin'),'manage_chat');
  assert.deepEqual([...link.searchParams.keys()],['startgroup','admin']);
  assert.doesNotMatch(keyboardRequests[0].text,/добавьте бота|додайте бота/iu);
  const state=(await api('status',{project_id:201,pairing_id:pair.pairing_id})).data.data;
  assert.equal(state.status,'WAITING_START');assert.equal(state.recipient,null);
});

test('channel selection supplies the required user rights covering only bot management/posting rights',async()=>{
  const pair=await start({project_id:215,target_kind:'channel'});
  const response=await webhook(update(pair.start_url,777));
  assert.equal(response.status,200);
  assert.equal(keyboardRequests.length,1);
  const request=keyboardRequests[0].reply_markup.keyboard[0][0].request_chat;
  assert.equal(request.chat_is_channel,true);
  assert.equal(request.bot_is_member,true);
  assert.deepEqual(request.user_administrator_rights,request.bot_administrator_rights,'Telegram rejects bot rights without covering user rights: USER_RIGHTS_MISSING');
  assert.deepEqual(Object.entries(request.bot_administrator_rights).filter(([,enabled])=>enabled).map(([right])=>right),['can_manage_chat','can_post_messages']);
});

for(const type of ['group','supergroup']) test(`${type}: native group Start requires the private claimant and never grants access before confirmation`,async()=>{
  const project_id=type==='group'?221:222;
  const pair=await start({project_id,target_kind:'group'});
  const groupStart=(user=777,extra={})=>{
    const value=update(pair.start_url,user,extra);
    value.message.chat={id:-100555,type};return value;
  };
  await webhook(groupStart());
  assert.equal((await api('status',{project_id,pairing_id:pair.pairing_id})).data.data.status,'WAITING_START','Group cannot claim a link before its private Start');
  await webhook(update(pair.start_url,777));
  for(const value of [groupStart(888),groupStart(777,{forward_origin:{type:'user'}}),groupStart(777,{date:0}),
    groupStart(777,{via_bot:{id:1}}),groupStart(777,{sender_chat:{id:-100555}})]) await webhook(value);
  assert.equal((await api('status',{project_id,pairing_id:pair.pairing_id})).data.data.status,'WAITING_START');
  await webhook(groupStart());
  assert.equal(chatReads.length,0,'A group webhook records consent only');
  const row=await db.prepare('SELECT shared_chat_id,binding_id FROM pairing_requests WHERE id=?').bind(pair.pairing_id).first();
  assert.equal(row.shared_chat_id,-100555);assert.equal(row.binding_id,null);
  chatFixture={id:-100555,type,userStatus:'creator'};
  assert.equal((await api('status',{project_id,pairing_id:pair.pairing_id})).data.data.status,'AWAITING_CONFIRMATION');
  assert.equal((await api('confirm',{project_id,pairing_id:pair.pairing_id,chat_id:-100555})).data.data.status,'CONNECTED');
});

test('a rejected Start keyboard is reported, grants no access and can be retried by the same claimant',async()=>{
  const project_id=218,pair=await start({project_id,target_kind:'group'});
  const consent=update(pair.start_url,777);
  keyboardReply={ok:false,error_code:400,description:'Bad Request: BUTTON_TYPE_INVALID'};
  const failure=await webhook(consent);
  assert.equal(failure.status,502);
  assert.equal(failure.data.error.code,'PAIRING_KEYBOARD_REJECTED');
  assert.equal(keyboardRequests.length,1);
  assert.equal((await api('status',{project_id,pairing_id:pair.pairing_id})).data.data.status,'WAITING_START');
  const before=keyboardRequests.length;
  await webhook(update(pair.start_url,888));
  assert.equal(keyboardRequests.length,before,'Another person cannot trigger a claimed keyboard');
  keyboardReply='success';
  assert.equal((await webhook(consent)).status,200);
  assert.equal(keyboardRequests.length,2);
  assert.equal((await db.prepare('SELECT binding_id FROM pairing_requests WHERE id=?').bind(pair.pairing_id).first()).binding_id,null);
});

test('keyboard rate limiting reports retry timing without sending a vehicle',async()=>{
  const pair=await start({project_id:219,target_kind:'channel'});
  keyboardReply={ok:false,error_code:429,parameters:{retry_after:37},description:'fixture secret'};
  const response=await webhook(update(pair.start_url,777));
  assert.equal(response.status,503);
  assert.equal(response.data.error.code,'PAIRING_KEYBOARD_RATE_LIMITED');
  assert.equal(response.headers.get('retry-after'),'37');
  assert.equal(keyboardRequests.length,1);
  assert.doesNotMatch(JSON.stringify(response.data),/fixture secret/);
});

for(const fault of ['network','redirect','oversize','malformed','wrong-chat']) test(`Start keyboard ${fault} is not acknowledged as successful`,async()=>{
  const pair=await start({project_id:220,target_kind:'group'});
  keyboardReply=fault;
  const response=await webhook(update(pair.start_url,777));
  assert.equal(response.status,503);
  assert.equal(response.data.error.code,'PAIRING_KEYBOARD_UNAVAILABLE');
  assert.doesNotMatch(JSON.stringify(response.data),/fixture secret|untrusted/);
  assert.equal(keyboardRequests.length,1,'No automatic second send after an ambiguous result');
});

test('a disabled chat gate rejects group start before creating a pairing while leaving private pairing available',async()=>{
  const isolated=new Miniflare({workers:[{
    name:'gated',modules:true,scriptPath:new URL('../dist/index.js',import.meta.url).pathname,
    compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],d1Databases:{PAIRING_DB:'gated'},
    serviceBindings:{LICENSE_AUTH:{name:'fixture-auth',entrypoint:'Auth'}},
    bindings:{PAIRING_ENABLED:'1',CHAT_PAIRING_ENABLED:'0',BOT_ID:'123456',TELEGRAM_WEBHOOK_SECRET:webhookSecret},
    outboundService:()=>{throw Error('No external calls');},
  },{name:'fixture-auth',modules:true,compatibilityDate:'2026-05-15',
    script:`import {WorkerEntrypoint} from 'cloudflare:workers'; export class Auth extends WorkerEntrypoint {async authorize(){return true;}};export default {fetch(){return new Response(null,{status:404});}};`} ]});
  try {
    const database=await isolated.getD1Database('PAIRING_DB','gated');
    for(const name of ['0001_private_pairing.sql','0002_recipient_reuse.sql','0003_delivery_receipts.sql','0004_chat_pairing.sql'])
      for(const sql of (await readFile(new URL('../migrations/'+name,import.meta.url),'utf8')).split(';').map(s=>s.trim()).filter(Boolean)) await database.prepare(sql).run();
    const request=async extra=>isolated.dispatchFetch('https://fixture.test/v1/pairings',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(await signed('start',extra))});
    const group=await request({target_kind:'group'});
    assert.equal(group.status,503);assert.equal((await group.json()).error.code,'CHAT_PAIRING_DISABLED');
    assert.equal((await database.prepare('SELECT count(*) AS n FROM pairing_requests').first()).n,0);
    const personal=await request({});assert.equal(personal.status,200);
    const id=(await personal.json()).data.pairing_id;
    await database.prepare("UPDATE pairing_requests SET target_kind='group',shared_chat_id=-100555 WHERE id=?").bind(id).run();
    const change=async action=>isolated.dispatchFetch('https://fixture.test/v1/pairings',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(await signed(action,{pairing_id:id}))});
    assert.equal((await change('status')).status,503);
    assert.equal((await change('revoke')).status,200,'A disabled gate must still allow disconnect');
  } finally {await isolated.dispose();}
});

for(const kind of ['private','group']) test(`${kind}: retrying reuse in the same project recovers its existing grant after retiring the old one`,async()=>{
  const project_id=kind==='private'?213:214;
  const pair=kind==='private'?await start({project_id}):(await selectedChat(kind,project_id)).pair;
  const chat_id=kind==='private'?555:-100555;
  if(kind==='private')await webhook(update(pair.start_url));
  else {
    const row=await db.prepare('SELECT request_chat_id FROM pairing_requests WHERE id=?').bind(pair.pairing_id).first();
    const shared=update(pair.start_url,777);delete shared.message.text;
    shared.message.chat_shared={request_id:row.request_chat_id,chat_id};await webhook(shared);
  }
  assert.equal((await api('confirm',{project_id,pairing_id:pair.pairing_id,chat_id})).status,200);
  const id=crypto.randomUUID().replaceAll('-','');
  const args={project_id,pairing_id:id,source_pairing_id:pair.pairing_id};
  const first=await api('reuse',args);assert.equal(first.status,200);
  const retry=await api('reuse',args);assert.equal(retry.status,200,JSON.stringify(retry.data));
  assert.equal(retry.data.data.binding_id,first.data.data.binding_id);
  await api('revoke',{project_id,pairing_id:id});
  assert.equal((await api('reuse',args)).status,409);
});

async function selectedChat(kind,project_id,chatType=kind==='channel'?'channel':'supergroup') {
  const pair=await start({project_id,target_kind:kind});
  const reply=await webhook(update(pair.start_url,777));
  assert.equal(reply.status,200);
  const request=kind==='channel'?keyboardRequests.at(-1).reply_markup.keyboard[0][0].request_chat:null;
  if(request)assert.equal(request.chat_is_channel,true);
  const share=(user=777,extra={})=>{
    const message=update(pair.start_url,user,extra);
    if(kind==='group') message.message.chat={id:-100555,type:chatType};
    else {delete message.message.text;message.message.chat_shared={request_id:request.request_id,chat_id:-100555,title:'Unverified spoof'};}
    return message;
  };
  chatFixture={id:-100555,type:chatType};
  return {pair,share,project_id,request};
}

for(const type of ['group','supergroup']) test(`${type}: unfiltered selection still checks bot and human rights before creating any grant`,async()=>{
  const f=await selectedChat('group',type==='group'?216:217,type);
  await webhook(f.share());
  for(const fault of [{userStatus:'member'},{botStatus:'member'},{botManage:false},{userManage:false}]) {
    chatFixture={id:-100555,type,...fault};
    for(const action of ['status','confirm']) {
      const result=await api(action,{project_id:f.project_id,pairing_id:f.pair.pairing_id,
        ...(action==='confirm'?{chat_id:-100555}:{})});
      assert.equal(result.status,403,JSON.stringify(result.data));
      assert.equal(result.data.error.code,'CHAT_PERMISSION_REQUIRED');
    }
    assert.equal((await db.prepare('SELECT binding_id FROM pairing_requests WHERE id=?').bind(f.pair.pairing_id).first()).binding_id,null);
  }
  // Telegram returns the creator role without a separate can_manage_chat field.
  chatFixture={id:-100555,type,userStatus:'creator'};
  const status=await api('status',{project_id:f.project_id,pairing_id:f.pair.pairing_id});
  assert.equal(status.status,200,JSON.stringify(status.data));
  assert.equal(status.data.data.status,'AWAITING_CONFIRMATION');
  const grant=await api('confirm',{project_id:f.project_id,pairing_id:f.pair.pairing_id,chat_id:-100555});
  assert.equal(grant.status,200,JSON.stringify(grant.data));
  assert.equal(grant.data.data.status,'CONNECTED');
});

for(const [kind,type] of [['group','group'],['group','supergroup'],['channel','channel']]) {
  test(`${kind}/${type}: Telegram selection, both admins, explicit confirmation, reuse and delivery retain trusted type`,async()=>{
    const project_id=kind==='channel'?205:type==='group'?203:204;
    const f=await selectedChat(kind,project_id,type);
    await webhook(f.share());assert.equal(chatReads.length,0,'Webhook is consent only, not arbitrary external HTTP');
    const status=await api('status',{project_id,pairing_id:f.pair.pairing_id});
    assert.equal(status.status,200,JSON.stringify(status.data));
    assert.deepEqual(status.data.data.recipient,{chat_id:-100555,chat_type:type,title:'Verified team'});
    assert.equal(status.data.data.status,'AWAITING_CONFIRMATION');
    const grant=await api('confirm',{project_id,pairing_id:f.pair.pairing_id,chat_id:-100555});
    assert.equal(grant.status,200,JSON.stringify(grant.data));assert.equal(grant.data.data.status,'CONNECTED');
    const reused=await api('reuse',{project_id:project_id+100,pairing_id:crypto.randomUUID().replaceAll('-',''),source_pairing_id:f.pair.pairing_id});
    assert.equal(reused.status,200,JSON.stringify(reused.data));
    assert.equal(reused.data.data.recipient.chat_type,type);
    assert.notEqual(reused.data.data.binding_id,grant.data.data.binding_id);
    deliveryAllowed=true;deliveryReply={ok:true,result:{message_id:701,chat:{id:-100555,type}}};
    try {
      const payload={project_id,binding_id:grant.data.data.binding_id,bot_id:123456,attempt_id:crypto.randomUUID().replaceAll('-',''),message:{text:'Fixture vehicle',photo:null,entities:[]}};
      const sent=await post('/v1/send',await signed('send',payload,device,{audience:'searchcar-telegram-delivery-v1'}));
      assert.equal(sent.data.data.state,'SENT',JSON.stringify(sent.data));
      assert.equal(outbound.at(-1).body.chat_id,-100555);
      const before=chatReads.length;
      assert.equal((await post('/v1/send',await signed('send',payload,device,{audience:'searchcar-telegram-delivery-v1'}))).data.data.state,'SENT');
      assert.equal(chatReads.length,before,'Replaying a receipt does not recheck or send');
    } finally {deliveryReply={ok:true,result:{message_id:701,chat:{id:555,type:'private'}}};}
  });
}

test('a shared request from a stranger, old/forwarded update, or wrong request cannot select a chat',async()=>{
  const f=await selectedChat('group',206);
  for(const update of [f.share(888),f.share(777,{forward_origin:{type:'user'}}),f.share(777,{date:0})]) await webhook(update);
  const wrong=f.share();wrong.message.text='/start p_'+'x'.repeat(32);await webhook(wrong);
  assert.equal((await api('status',{project_id:206,pairing_id:f.pair.pairing_id})).data.data.status,'WAITING_START');
  assert.equal(chatReads.length,0);
  const other=await webhook(update(f.pair.start_url,888));assert.equal(other.data.method,undefined);
  await webhook(f.share());
  const wrongType=await api('confirm',{project_id:206,pairing_id:f.pair.pairing_id,chat_id:777});
  assert.equal(wrongType.status,409);
});

test('missing/revoked posting rights, non-admin users, wrong chat type and failed bounded checks never create a grant',async()=>{
  const f=await selectedChat('channel',207);await webhook(f.share());
  for(const fault of [{userStatus:'member'},{botStatus:'member'},{botPost:false},{userPost:false},{type:'supergroup'},
    {fault:'network'},{fault:'redirect'},{fault:'oversize'},{fault:'denied'}]) {
    chatFixture={id:-100555,type:'channel',...fault};
    const result=await api('confirm',{project_id:207,pairing_id:f.pair.pairing_id,chat_id:-100555});
    assert.ok([403,503].includes(result.status),JSON.stringify(result.data));
    assert.doesNotMatch(JSON.stringify(result.data),/private secret|fixture-not-a-real-secret/);
    assert.equal((await db.prepare('SELECT binding_id FROM pairing_requests WHERE id=?').bind(f.pair.pairing_id).first()).binding_id,null);
  }
  chatFixture={id:-100555,type:'channel'};
  assert.equal((await api('status',{project_id:207,pairing_id:f.pair.pairing_id})).status,200);
  chatFixture.botPost=false;
  assert.equal((await api('confirm',{project_id:207,pairing_id:f.pair.pairing_id,chat_id:-100555})).status,403);
});

test('stop from the requesting administrator revokes group grants and saved reuse, including in-flight selection',async()=>{
  const f=await selectedChat('group',208);await webhook(f.share());
  await api('confirm',{project_id:208,pairing_id:f.pair.pairing_id,chat_id:-100555});
  const reused=await api('reuse',{project_id:308,pairing_id:crypto.randomUUID().replaceAll('-',''),source_pairing_id:f.pair.pairing_id});
  assert.equal(reused.status,200);
  const pending=await selectedChat('channel',212);
  const stop=update(f.pair.start_url,777);stop.message.text='/stop';await webhook(stop);
  assert.equal((await api('status',{project_id:208,pairing_id:f.pair.pairing_id})).data.data.status,'REVOKED');
  assert.equal((await api('status',{project_id:308,pairing_id:reused.data.data.pairing_id})).data.data.status,'REVOKED');
  await webhook(pending.share());
  assert.equal((await api('status',{project_id:212,pairing_id:pending.pair.pairing_id})).data.data.status,'REVOKED');
  assert.equal((await api('reuse',{project_id:408,pairing_id:crypto.randomUUID().replaceAll('-',''),source_pairing_id:f.pair.pairing_id})).status,409);
});

test('expired/cancelled group links and a second selection cannot change the chat or grant',async()=>{
  const f=await selectedChat('group',209);await webhook(f.share());
  const other=f.share();other.message.chat.id=-100999;await webhook(other);
  assert.equal((await api('status',{project_id:209,pairing_id:f.pair.pairing_id})).data.data.recipient.chat_id,-100555);
  await db.prepare('UPDATE pairing_requests SET expires_at=? WHERE id=?').bind(Date.now()-1,f.pair.pairing_id).run();
  const reads=chatReads.length;
  assert.equal((await api('confirm',{project_id:209,pairing_id:f.pair.pairing_id,chat_id:-100555})).status,409);
  assert.equal(chatReads.length,reads);
  const pending=await selectedChat('channel',210);
  await api('revoke',{project_id:210,pairing_id:pending.pair.pairing_id});
  await webhook(pending.share());
  assert.equal((await api('status',{project_id:210,pairing_id:pending.pair.pairing_id})).data.data.status,'REVOKED');
});

test('permissions lost after confirmation block reuse and sends; read-only rate failures keep retry_after with no send receipt',async()=>{
  const f=await selectedChat('channel',211);await webhook(f.share());
  const grant=await api('confirm',{project_id:211,pairing_id:f.pair.pairing_id,chat_id:-100555});
  assert.equal(grant.status,200);
  const attempt={project_id:211,binding_id:grant.data.data.binding_id,bot_id:123456,
    attempt_id:crypto.randomUUID().replaceAll('-',''),message:{text:'Fixture',photo:null,entities:[]}};
  const send=async()=>post('/v1/send',await signed('send',attempt,device,{audience:'searchcar-telegram-delivery-v1'}));
  const before=outbound.length;
  chatFixture.botPost=false;
  assert.equal((await api('reuse',{project_id:311,pairing_id:crypto.randomUUID().replaceAll('-',''),source_pairing_id:f.pair.pairing_id})).status,403);
  assert.equal((await send()).status,403);assert.equal(outbound.length,before);
  chatFixture={id:-100555,type:'channel',fault:'rate'};
  const limited=await send();assert.equal(limited.status,429);assert.equal(limited.data.retry_after,37);
  assert.equal((await db.prepare('SELECT count(*) AS n FROM delivery_receipts WHERE project_id=211').first()).n,0);
  assert.equal(outbound.length,before);
});

test('missing license authorization binding closes pairing and webhook before any work',async()=>{
  const isolated=new Miniflare({modules:true,scriptPath:new URL('../dist/index.js',import.meta.url).pathname,
    compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],d1Databases:{PAIRING_DB:'isolated-no-auth'},
    bindings:{PAIRING_ENABLED:'1',BOT_ID:'123456',TELEGRAM_WEBHOOK_SECRET:webhookSecret},
    outboundService:async()=>{throw Error('No outbound request allowed');}});
  try {
    for(const path of ['/v1/pairings','/v1/telegram/webhook']) {
      const response=await isolated.dispatchFetch('https://pairing.test'+path,{method:'POST',headers:{
        'content-type':'application/json','x-telegram-bot-api-secret-token':webhookSecret},body:JSON.stringify({update_id:1})});
      assert.equal(response.status,503);
      assert.equal((await response.json()).error.code,'SERVICE_NOT_CONFIGURED');
    }
  } finally {await isolated.dispose();}
});

test('license RPC independently verifies proof and freshness and returns no metadata or leases',async()=>{
  const probe=await mf.getWorker('auth-probe');
  const check=async body=>{
    const result=await probe.fetch('https://fixture.test',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
    assert.equal(result.status,200);return result.json();
  };
  const snapshot=await licenses.prepare('SELECT * FROM devices').all();
  const valid=await signed('start');assert.equal(await check(valid),true);
  assert.equal(await check(valid),true); // Replay protection belongs to the relay, not this read-only RPC.
  assert.equal(await check({...valid,telegram:{...valid.telegram,project_id:999}}),false);
  assert.equal(await check(await signed('start',{},device,{requested_at:'2000-01-01T00:00:00.000Z'})),false);
  assert.equal(await check(await signed('start',{},device,{audience:'other-service'})),false);
  assert.equal(await check(await signed('start',{},await newDevice())),false);
  assert.equal(await check(await signed('start',{},device,{license_id:'30000000-0000-4000-8000-000000000001'})),false);
  assert.equal(await check({x:'x'.repeat(16385)}),false);
  assert.equal(await check({}),false);
  assert.deepEqual((await licenses.prepare('SELECT * FROM devices').all()).results,snapshot.results);
  await licenses.prepare('UPDATE licenses SET expires_at=NULL,perpetual=1 WHERE id=?').bind(licenseId).run();
  try {assert.equal(await check(await signed('start')),true);} finally {
    await licenses.prepare("UPDATE licenses SET expires_at='2099-01-01T00:00:00Z',perpetual=0 WHERE id=?").bind(licenseId).run();
  }
  const publicWorker=await mf.getWorker('license');
  assert.equal((await publicWorker.fetch('https://fixture.test/v1/telegram/authorize',{method:'POST'})).status,404);
  assert.equal((await publicWorker.fetch('https://fixture.test/health')).status,200);
});

test('delivery uses the confirmed project grant, makes one attempt and replays only its receipt',async()=>{
  const pair=await start({project_id:91});await webhook(update(pair.start_url));
  const grant=(await api('confirm',{project_id:91,pairing_id:pair.pairing_id,chat_id:555})).data.data;
  const attempt={project_id:91,binding_id:grant.binding_id,attempt_id:'e'.repeat(32),bot_id:123456,
    message:{text:'BMW X2\nIAA № 12345678',photo:null,entities:[]}};
  const send=async(extra={})=>post('/v1/send',await signed('send',{...attempt,...extra},device,{audience:'searchcar-telegram-delivery-v1'}));
  const before=outbound.length;
  deliveryAllowed=true;
  assert.equal((await send({project_id:92})).status,403);
  const result=await send();assert.equal(result.status,200,JSON.stringify(result.data));
  assert.equal(result.data.data.state,'SENT');assert.equal(result.data.data.message_id,701);
  assert.equal(outbound.length,before+1);assert.equal(outbound.at(-1).body.chat_id,555);
  assert.equal(outbound.at(-1).body.allow_paid_broadcast,false);
  assert.equal((await send()).data.data.message_id,701);assert.equal(outbound.length,before+1);
  assert.equal((await send({message:{...attempt.message,text:'Changed'}})).status,409);
  await api('revoke',{project_id:91,pairing_id:pair.pairing_id});
  assert.equal((await send({attempt_id:'d'.repeat(32)})).status,403);assert.equal(outbound.length,before+1);
});

test('relay returns flood control or explicit rejection but never schedules a second attempt',async()=>{
  const pair=await start({project_id:93});await webhook(update(pair.start_url));
  const grant=(await api('confirm',{project_id:93,pairing_id:pair.pairing_id,chat_id:555})).data.data;
  deliveryAllowed=true;
  try {
    for(const [reply,state,retry] of [
      [{ok:false,error_code:429,parameters:{retry_after:17}},'RATE_LIMITED',17],
      [{ok:false,error_code:429,parameters:{retry_after:'bad'}},'RATE_LIMITED',null],
      [{ok:false,error_code:403,description:'private details'},'FAILED',null],
      [{ok:false,error_code:400,description:'Bad Request: failed to get HTTP URL content'},'PHOTO_REJECTED',null],
      ['network','UNKNOWN',null],['redirect','UNKNOWN',null],['oversize','UNKNOWN',null],['malformed','UNKNOWN',null],
      [{ok:true,result:{message_id:4,chat:{id:666,type:'private'}}},'UNKNOWN',null],
    ]) {
      deliveryReply=reply;const attempt=crypto.randomUUID().replaceAll('-','');const before=outbound.length;
      const body={project_id:93,binding_id:grant.binding_id,attempt_id:attempt,bot_id:123456,message:{text:'Vehicle',photo:'https://cs.copart.com/photo.jpg',entities:[]}};
      const send=async()=>post('/v1/send',await signed('send',body,device,{audience:'searchcar-telegram-delivery-v1'}));
      const result=await send();assert.equal(result.status,200,JSON.stringify(result.data));
      assert.equal(result.data.data.state,state);assert.equal(result.data.data.retry_after,retry);
      assert.doesNotMatch(JSON.stringify(result.data),/private details|fixture-not-a-real-secret/);
      assert.equal(outbound.length,before+1);
      assert.equal((await send()).data.data.state,state);assert.equal(outbound.length,before+1);
    }
  } finally {deliveryReply={ok:true,result:{message_id:701,chat:{id:555,type:'private'}}};}
});

test('closed message schema rejects paid options, unsafe photos/calendar URLs and wrong UTF-16 offsets before HTTP',async()=>{
  const pair=await start({project_id:94});await webhook(update(pair.start_url));
  const grant=(await api('confirm',{project_id:94,pairing_id:pair.pairing_id,chat_id:555})).data.data;
  const before=outbound.length;deliveryAllowed=true;
  for(const message of [
    {text:'Vehicle',photo:null,entities:[],allow_paid_broadcast:true},
    {text:'Vehicle',photo:'https://localhost/photo.jpg',entities:[]},
    {text:'Vehicle',photo:'http://cs.copart.com/photo.jpg',entities:[]},
    {text:'x'.repeat(1025),photo:'https://cs.copart.com/photo.jpg',entities:[]},
    {text:'Vehicle',photo:null,entities:[{type:'text_link',offset:0,length:7,url:'https://untrusted.test'}]},
    {text:'🚗 Vehicle',photo:null,entities:[{type:'text_link',offset:1,length:1,url:'https://calendar.google.com/calendar/render?action=TEMPLATE'}]},
    ...['https://www.copart.com/account','https://www.copart.com/lot/123?token=private','https://www.iaai.com/Search','http://www.copart.com/lot/123'].map(url=>({text:'Vehicle',photo:null,entities:[{type:'text_link',offset:0,length:7,url}]})),
    {text:'Vehicle',photo:null,entities:[{type:'bold',offset:0,length:7,url:'https://www.copart.com/lot/123'}]},
    {text:'Vehicle',photo:null,entities:[{type:'italic',offset:0,length:7}]},
    {text:'Vehicle',photo:null,entities:[{type:'bold',offset:0,length:4},{type:'bold',offset:3,length:2}]},
    {text:'Vehicle',photo:null,entities:[{type:'bold',offset:0,length:0}]},
    {text:'Vehicle',photo:null,entities:Array.from({length:17},()=>({type:'bold',offset:0,length:1}))},
  ]) {
    const result=await post('/v1/send',await signed('send',{project_id:94,binding_id:grant.binding_id,attempt_id:crypto.randomUUID().replaceAll('-',''),bot_id:123456,message},device,{audience:'searchcar-telegram-delivery-v1'}));
    assert.equal(result.status,400,JSON.stringify(result.data));
  }
  assert.equal(outbound.length,before);
  const receipts=await db.prepare('SELECT * FROM delivery_receipts WHERE project_id=94').all();assert.equal(receipts.results.length,0);
});

test('concurrent identical attempts make at most one HTTP send and preserve inline calendar metadata',async()=>{
  const pair=await start({project_id:95});await webhook(update(pair.start_url));
  const grant=(await api('confirm',{project_id:95,pairing_id:pair.pairing_id,chat_id:555})).data.data;
  deliveryAllowed=true;const before=outbound.length;
  const url='https://calendar.google.com/calendar/render?action=TEMPLATE&text=Vehicle';
  const message={text:'🚗 Vehicle\nДобавить в календарь',photo:null,entities:[{type:'text_link',offset:11,length:20,url}]};
  // Offsets are native UTF-16 units, including the surrogate pair before the linked label.
  message.entities[0].offset=message.text.indexOf('Добавить');message.entities[0].length='Добавить в календарь'.length;
  const body={project_id:95,binding_id:grant.binding_id,attempt_id:crypto.randomUUID().replaceAll('-',''),bot_id:123456,message};
  const results=await Promise.all([1,2].map(async()=>post('/v1/send',await signed('send',body,device,{audience:'searchcar-telegram-delivery-v1'}))));
  assert.ok(results.every(result=>result.status===200));assert.ok(results.some(result=>result.data.data.state==='SENT'));
  assert.equal(outbound.length,before+1);assert.deepEqual(outbound.at(-1).body.entities,message.entities);
  assert.equal(outbound.at(-1).body.reply_markup.inline_keyboard[0][0].url,url);
});

test('styled text/photo preserve bold and hidden listing links while the button still opens the calendar',async()=>{
  const pair=await start({project_id:96});await webhook(update(pair.start_url));
  const grant=(await api('confirm',{project_id:96,pairing_id:pair.pairing_id,chat_id:555})).data.data;
  deliveryAllowed=true;
  for(const source of ['Copart','IAA']) {
  const text=`🚙 Vehicle\n🏷 Аукцион: ${source}\n🔗 Открыть автомобиль на ${source}\n📅 Добавить в календарь`;
  const calendar='https://searchcar-auction-calendar-preview.pages.dev/#public-fixture';
  const entity=(type,label,url)=>({type,offset:text.indexOf(label),length:label.length,...(url?{url}:{})});
  const listing=source==='Copart'?'https://www.copart.com/lot/72267656':'https://www.iaai.com/VehicleDetail/12345678~US';
  const entities=[entity('bold','Vehicle'),entity('bold','Аукцион:'),entity('text_link',`Открыть автомобиль на ${source}`,listing),entity('text_link','Добавить в календарь',calendar)];
  for(const photo of [null,'https://cs.copart.com/photo.jpg']){
    const result=await post('/v1/send',await signed('send',{project_id:96,binding_id:grant.binding_id,attempt_id:crypto.randomUUID().replaceAll('-',''),bot_id:123456,
      message:{text,photo,entities}},device,{audience:'searchcar-telegram-delivery-v1'}));
    assert.equal(result.status,200,JSON.stringify(result.data));assert.equal(result.data.data.state,'SENT');
    const sent=outbound.at(-1).body;assert.deepEqual(photo?sent.caption_entities:sent.entities,entities);
    assert.equal(photo?sent.caption:sent.text,text);assert.equal(sent.parse_mode,undefined);
    assert.equal(sent.reply_markup.inline_keyboard[0][0].url,calendar);
  }
  }
});

test('saved private recipient gets a separate idempotent project grant; Stop also revokes reused consent',async()=>{
  const original=await start({project_id:81});await webhook(update(original.start_url,8181));
  const confirmed=(await api('confirm',{project_id:81,pairing_id:original.pairing_id,chat_id:8181})).data.data;
  const id='8'.repeat(32), reuse={project_id:82,pairing_id:id,source_pairing_id:original.pairing_id};
  const result=await api('reuse',reuse);
  assert.equal(result.status,200,JSON.stringify(result.data));
  assert.equal(result.data.data.project_id,82);assert.equal(result.data.data.recipient.chat_id,8181);
  assert.notEqual(result.data.data.binding_id,confirmed.binding_id);
  assert.equal((await api('reuse',reuse)).data.data.binding_id,result.data.data.binding_id);
  assert.equal((await api('reuse',{...reuse,owner_id:8,pairing_id:'9'.repeat(32)})).status,409);
  assert.equal((await api('reuse',{...reuse,source_pairing_id:'a'.repeat(32)})).status,409);
  const stopped=update(original.start_url,8181);stopped.message.text='/stop';await webhook(stopped);
  assert.equal((await api('status',{project_id:82,pairing_id:id})).data.data.status,'REVOKED');
  assert.equal((await api('reuse',{...reuse,pairing_id:'7'.repeat(32)})).status,409);
  assert.equal((await api('reuse',reuse)).status,409);
});

test('reuse requires confirmed consent and preserves newer consent against an earlier Stop',async()=>{
  const source=await start({project_id:83});
  const request={project_id:84,pairing_id:'6'.repeat(32),source_pairing_id:source.pairing_id};
  assert.equal((await api('reuse',request)).status,409);
  const claimed=update(source.start_url,8383);await webhook(claimed);
  assert.equal((await api('reuse',request)).status,409);
  await api('confirm',{project_id:83,pairing_id:source.pairing_id,chat_id:8383});
  assert.equal((await api('reuse',request)).status,200);
  await webhook({...claimed,update_id:claimed.update_id-1,message:{...claimed.message,text:'/stop'}});
  assert.equal((await api('status',{project_id:84,pairing_id:request.pairing_id})).data.data.status,'CONNECTED');
  // Disconnect is project-local: it cannot revoke another project's separate grant.
  await api('revoke',{project_id:83,pairing_id:source.pairing_id});
  assert.equal((await api('status',{project_id:84,pairing_id:request.pairing_id})).data.data.status,'CONNECTED');
});

test('private pairing: Start proposes a recipient, explicit app confirmation grants only that project',async()=>{
  const pair=await start();assert.equal(pair.status,'WAITING_START');assert.equal(pair.recipient,null);
  assert.match(pair.pairing_id,/^[a-f0-9]{32}$/);assert.equal(pair.binding_id,null);
  const url=new URL(pair.start_url);assert.equal(url.origin+url.pathname,'https://t.me/search_car_app_bot');
  assert.match(url.searchParams.get('start'),/^p_[A-Za-z0-9_-]{32}$/);
  assert.ok(pair.expires_at_ms>Date.now());assert.ok(pair.expires_at_ms<=Date.now()+600000);
  assert.equal((await api('confirm',{pairing_id:pair.pairing_id,chat_id:555})).status,409);
  assert.equal((await webhook(update(pair.start_url))).status,200);
  const proposal=await api('status',{pairing_id:pair.pairing_id});
  assert.equal(proposal.data.data.status,'AWAITING_CONFIRMATION');assert.equal(proposal.data.data.binding_id,null);
  assert.equal(proposal.data.data.start_url,undefined);assert.equal(proposal.data.data.recipient.chat_type,'private');
  assert.equal((await api('confirm',{pairing_id:pair.pairing_id,chat_id:666})).status,409);
  const confirmed=await api('confirm',{pairing_id:pair.pairing_id,chat_id:555});
  assert.equal(confirmed.status,200);assert.equal(confirmed.data.data.status,'CONNECTED');
  assert.match(confirmed.data.data.binding_id,/^[a-f0-9]{32}$/);assert.equal(confirmed.data.data.bot_id,123456);
  const again=await api('confirm',{pairing_id:pair.pairing_id,chat_id:555});
  assert.equal(again.data.data.binding_id,confirmed.data.data.binding_id);
  assert.equal((await api('status',{pairing_id:pair.pairing_id,project_id:10})).status,404);
  assert.equal((await api('status',{pairing_id:pair.pairing_id,owner_id:8})).status,404);
  assert.equal(proposal.headers.get('cache-control'),'no-store');
});

test('untrusted webhook cannot claim; a copied Start cannot replace the first recipient',async()=>{
  const pair=await start({project_id:10});
  assert.equal((await webhook(update(pair.start_url),'wrong')).status,401);
  assert.equal((await api('status',{pairing_id:pair.pairing_id,project_id:10})).data.data.status,'WAITING_START');
  const first=update(pair.start_url,777);assert.equal((await webhook(first)).status,200);
  assert.equal((await webhook(first)).status,200);assert.equal((await webhook(update(pair.start_url,888))).status,200);
  assert.equal((await api('status',{pairing_id:pair.pairing_id,project_id:10})).data.data.recipient.chat_id,777);
});

test('signed scope, device authorization, freshness and replay are checked before state changes',async()=>{
  const body=await signed('start',{project_id:11});
  assert.equal((await post('/v1/pairings',body)).status,200);
  assert.equal((await post('/v1/pairings',body)).status,409);
  body.telegram.project_id=12;assert.equal((await post('/v1/pairings',body)).status,401);
  assert.equal((await api('start',{},await newDevice())).status,403);
  const stale=await signed('start');stale.requested_at='2000-01-01T00:00:00.000Z';
  assert.equal((await post('/v1/pairings',stale)).status,401);
  await licenses.prepare('UPDATE devices SET is_active=0 WHERE id=?').bind(deviceId).run();
  try {assert.equal((await api('start')).status,403);} finally {await licenses.prepare('UPDATE devices SET is_active=1 WHERE id=?').bind(deviceId).run();}
});

test('expired/replaced/revoked links cannot grant access, and /stop revokes existing recipient consent',async()=>{
  const expired=await start({project_id:12});
  await db.prepare('UPDATE pairing_requests SET expires_at=? WHERE id=?').bind(Date.now()-1,expired.pairing_id).run();
  await webhook(update(expired.start_url));
  assert.equal((await api('status',{pairing_id:expired.pairing_id,project_id:12})).data.data.status,'EXPIRED');
  const old=await start({project_id:13});const fresh=await start({project_id:13});
  await webhook(update(old.start_url));
  assert.equal((await api('status',{pairing_id:old.pairing_id,project_id:13})).data.data.status,'REVOKED');
  await webhook(update(fresh.start_url,999));await api('confirm',{pairing_id:fresh.pairing_id,project_id:13,chat_id:999});
  const stopped=update(fresh.start_url,999);stopped.message.text='/stop';await webhook(stopped);
  assert.equal((await api('status',{pairing_id:fresh.pairing_id,project_id:13})).data.data.status,'REVOKED');
  assert.equal((await api('confirm',{pairing_id:fresh.pairing_id,project_id:13,chat_id:999})).status,409);
  const cancelled=await start({project_id:14});await api('revoke',{pairing_id:cancelled.pairing_id,project_id:14});
  await webhook(update(cancelled.start_url));
  assert.equal((await api('status',{pairing_id:cancelled.pairing_id,project_id:14})).data.data.status,'REVOKED');
});

test('only direct private user Start messages count; raw pairing secrets are not persisted',async()=>{
  const pair=await start({project_id:15});
  for(const extra of [{chat:{id:-100,type:'supergroup'}},{from:{id:555,is_bot:true}}, {from:{id:666,is_bot:false}},
    {text:'/start wrong'},{text:'/start p_'+'x'.repeat(32),date:0}]) await webhook(update(pair.start_url,555,extra));
  assert.equal((await api('status',{pairing_id:pair.pairing_id,project_id:15})).data.data.status,'WAITING_START');
  const row=await db.prepare('SELECT * FROM pairing_requests WHERE id=?').bind(pair.pairing_id).first();
  assert.ok(!JSON.stringify(row).includes(new URL(pair.start_url).searchParams.get('start').slice(2)));
  assert.equal((await post('/v1/pairings',{token:'private-value'})).status,400);
  const malformed=await post('/v1/pairings',{token:'private-value'});assert.ok(!JSON.stringify(malformed.data).includes('private-value'));
});

test('replacement preserves old consent until confirmation, then retires only that project binding',async()=>{
  const old=await start({project_id:16});await webhook(update(old.start_url,456));
  await api('confirm',{pairing_id:old.pairing_id,project_id:16,chat_id:456});
  const other=await start({project_id:17});await webhook(update(other.start_url,456));
  await api('confirm',{pairing_id:other.pairing_id,project_id:17,chat_id:456});
  const fresh=await start({project_id:16});
  assert.equal((await api('status',{pairing_id:old.pairing_id,project_id:16})).data.data.status,'CONNECTED');
  await webhook(update(fresh.start_url,654));
  await api('confirm',{pairing_id:fresh.pairing_id,project_id:16,chat_id:654});
  assert.equal((await api('status',{pairing_id:old.pairing_id,project_id:16})).data.data.status,'REVOKED');
  assert.equal((await api('status',{pairing_id:other.pairing_id,project_id:17})).data.data.status,'CONNECTED');
});

test('a delayed stop does not revoke newer consent, even in the same second',async()=>{
  const pair=await start({project_id:18});const stop=update(pair.start_url,321);stop.message.text='/stop';
  const consent=update(pair.start_url,321);consent.message.date=stop.message.date;
  await webhook(consent);await webhook(stop);
  assert.equal((await api('status',{pairing_id:pair.pairing_id,project_id:18})).data.data.status,'AWAITING_CONFIRMATION');
});

test('concurrent Start and confirm requests produce one recipient and one live binding',async()=>{
  const pair=await start({project_id:19});
  await Promise.all([webhook(update(pair.start_url,111)),webhook(update(pair.start_url,222))]);
  const proposal=(await api('status',{pairing_id:pair.pairing_id,project_id:19})).data.data;
  assert.ok([111,222].includes(proposal.recipient.chat_id));
  const extra={pairing_id:pair.pairing_id,project_id:19,chat_id:proposal.recipient.chat_id};
  const results=await Promise.all([api('confirm',extra),api('confirm',extra)]);
  assert.ok(results.some(r=>r.status===200));assert.ok(results.every(r=>[200,409].includes(r.status)));
  const bindings=results.filter(r=>r.status===200).map(r=>r.data.data.binding_id);
  assert.equal(new Set(bindings).size,1);
  assert.equal((await api('status',{pairing_id:pair.pairing_id,project_id:19})).data.data.binding_id,bindings[0]);
});

test('authorization checks license expiry, suspension/deletion, audience and signed owner types',async()=>{
  for(const change of ["status='SUSPENDED'","expires_at='2000-01-01T00:00:00Z'","deleted_at='2026-01-01'", "expires_at='invalid'"]) {
    await licenses.prepare('UPDATE licenses SET '+change+' WHERE id=?').bind(licenseId).run();
    try {assert.equal((await api('start')).status,403);} finally {
      await licenses.prepare("UPDATE licenses SET status='ACTIVE',expires_at='2099-01-01T00:00:00Z',deleted_at=NULL WHERE id=?").bind(licenseId).run();
    }
  }
  assert.equal((await post('/v1/pairings',await signed('start',{},device,{audience:'other-service'}))).status,401);
  for(const owner_id of [true,0,-1,'7']) assert.equal((await api('start',{owner_id})).status,400);
});

test('bounded JSON, disabled configuration and pairing abuse limit fail closed without a sender',async()=>{
  const oversized=await post('/v1/pairings',{x:'я'.repeat(10000)});
  assert.equal(oversized.status,413);
  const disabled=new Miniflare({modules:true,scriptPath:new URL('../dist/index.js',import.meta.url).pathname,
    compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],bindings:{PAIRING_ENABLED:'0'}});
  try {
    const result=await disabled.dispatchFetch('https://pairing.test/v1/pairings',{method:'POST'});
    assert.equal(result.status,503);assert.equal((await result.json()).error.code,'SERVICE_NOT_CONFIGURED');
  } finally {await disabled.dispose();}
  const window=Math.floor(Date.now()/60000)*60000;
  for(const time of [window,window+60000]) await db.prepare('INSERT OR REPLACE INTO security_windows VALUES(?,?,120)').bind(device.device.public_key,time).run();
  const before=(await db.prepare('SELECT count(*) AS count FROM pairing_requests').first()).count;
  try {
    const result=await api('start');assert.equal(result.status,429);assert.equal(result.data.error.code,'PAIRING_RATE_LIMITED');
    assert.equal((await db.prepare('SELECT count(*) AS count FROM pairing_requests').first()).count,before);
  } finally {await db.prepare('DELETE FROM security_windows').run();}
});
