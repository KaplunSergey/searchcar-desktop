import assert from 'node:assert/strict';
import {after,before,test} from 'node:test';
import {Miniflare} from 'miniflare';

const token='8996925581:fixture_token_not_a_real_credential_abc';
const secret='fixture_webhook_secret_not_a_real_secret';
const target='https://searchcar-telegram-relay-staging.mapatar23.workers.dev/v1/telegram/webhook';
let mf,mode='ok',calls=[],hookReads=0;
before(async()=>{
  mf=new Miniflare({workers:[{name:'operator',modules:true,
    scriptPath:new URL('../dist/operator/index.js',import.meta.url).pathname,
    compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat','global_fetch_strictly_public'],
    bindings:{PAIRING_ENABLED:'1',DELIVERY_ENABLED:'0',BOT_ID:'8996925581',TELEGRAM_BOT_TOKEN:token,TELEGRAM_WEBHOOK_SECRET:secret},
    outboundService:async request=>{
      const url=request.url;calls.push(url);
      if(url===target){
        assert.equal(request.headers.get('x-telegram-bot-api-secret-token'),secret);
        assert.deepEqual(await request.json(),{update_id:0});
        return Response.json({ok:mode!=='receiver_failed'},{status:mode==='receiver_failed'?404:200});
      }
      assert.ok(url.startsWith('https://api.telegram.org/bot'+token+'/'));
      const method=url.split('/').at(-1);
      assert.ok(['getMe','getWebhookInfo','setWebhook'].includes(method),'No sendMessage/getUpdates allowed');
      if(mode==='network')throw Error('private token '+token);
      if(mode==='oversize')return new Response('x'.repeat(70000));
      if(mode==='redirect')return new Response(null,{status:302,headers:{location:'https://untrusted.test'}});
      if(method==='getMe')return Response.json({ok:true,result:{id:8996925581,is_bot:true,username:mode==='wrong_bot'?'other_bot':'search_car_app_bot'}});
      if(method==='getWebhookInfo'){
        const url=mode==='conflict'?'https://other-service.test/hook':hookReads++===0?'':target;
        return Response.json({ok:true,result:{url,pending_update_count:3,allowed_updates:mode==='bad_updates'?['message','channel_post']:['message']}});
      }
      const body=await request.json();
      assert.deepEqual(body,{url:target,secret_token:secret,allowed_updates:['message'],drop_pending_updates:false});
      return Response.json({ok:true,result:mode!=='set_rejected'});
    }},
    {name:'client',modules:true,compatibilityDate:'2026-05-15',serviceBindings:{SETUP:{name:'operator',entrypoint:'TelegramSetup'}},
      script:`export default {async fetch(request,env){return Response.json(await env.SETUP.configure());}};`}]});
});
after(async()=>{await mf?.dispose();});
async function configure(scenario){mode=scenario;calls=[];hookReads=0;const client=await mf.getWorker('client');return (await client.fetch('https://fixture.test')).json();}
test('private operator configures only the staging webhook and preserves pending updates without sending',async()=>{
  const result=await configure('ok');
  assert.deepEqual(result,{ok:true,bot_id:8996925581,webhook_configured:true,pending_updates:3,delivery_enabled:false});
  assert.deepEqual(calls,[`https://api.telegram.org/bot${token}/getMe`,`https://api.telegram.org/bot${token}/getWebhookInfo`,target,
    `https://api.telegram.org/bot${token}/setWebhook`,`https://api.telegram.org/bot${token}/getWebhookInfo`]);
  assert.ok(!JSON.stringify(result).includes(token));assert.ok(!JSON.stringify(result).includes(secret));
});
test('wrong bot, foreign webhook and failed receiver cannot alter webhook',async()=>{
  for(const scenario of ['wrong_bot','conflict','receiver_failed']){
    const result=await configure(scenario);assert.equal(result.ok,false);
    if(scenario==='receiver_failed')assert.deepEqual(result,{ok:false,code:'RECEIVER_NOT_READY',receiver_status:404});
    assert.ok(!calls.some(url=>url.endsWith('/setWebhook')));
  }
});
test('API failures and malformed responses are bounded, sanitized, and never automatically retried',async()=>{
  for(const scenario of ['network','oversize','redirect','set_rejected','bad_updates']){
    const result=await configure(scenario);assert.equal(result.ok,false);
    assert.ok(!JSON.stringify(result).includes(token));assert.ok(!JSON.stringify(result).includes(secret));
    assert.ok(calls.filter(url=>url.endsWith('/setWebhook')).length<=1);
  }
});
test('operator refuses setup if delivery is enabled or pairing/bot/secret are not ready',async()=>{
  for(const change of [{DELIVERY_ENABLED:'1'},{PAIRING_ENABLED:'0'},{BOT_ID:'0'},{TELEGRAM_WEBHOOK_SECRET:''}]){
    const isolated=new Miniflare({workers:[{name:'operator',modules:true,
      scriptPath:new URL('../dist/operator/index.js',import.meta.url).pathname,
      compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],
      bindings:{PAIRING_ENABLED:'1',DELIVERY_ENABLED:'0',BOT_ID:'8996925581',TELEGRAM_BOT_TOKEN:token,TELEGRAM_WEBHOOK_SECRET:secret,...change},
      outboundService:()=>{throw Error('Disabled configuration must not make any requests');}},
      {name:'client',modules:true,compatibilityDate:'2026-05-15',serviceBindings:{SETUP:{name:'operator',entrypoint:'TelegramSetup'}},
        script:`export default {async fetch(request,env){return Response.json(await env.SETUP.configure());}};`}]});
    try {
      const client=await isolated.getWorker('client');
      assert.deepEqual(await (await client.fetch('https://fixture.test')).json(),{ok:false,code:'SETUP_NOT_CONFIGURED'});
    }finally{await isolated.dispose();}
  }
});
