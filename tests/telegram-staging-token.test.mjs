import assert from 'node:assert/strict';
import {test} from 'node:test';
import {configureToken,storeStagingSecret} from '../scripts/configure_telegram_staging_token.mjs';

const token='123456789:fixture_token_value_not_a_real_credential';
function fixture(overrides={}) {
  const stored=[],calls=[];
  return {stored,calls,options:{prompt:async()=>token,
    api:async(method,value)=>{assert.equal(value,token);calls.push(method);
      return method==='getMe'?{id:123456789,is_bot:true,username:'search_car_app_bot'}:{url:'',pending_update_count:0};},
    store:async(value)=>stored.push(value),...overrides}};
}
test('verified staging token is stored without returning it or mutating webhook',async()=>{
  const f=fixture();const result=await configureToken(f.options);
  assert.deepEqual(result,{status:'STORED',bot_id:123456789,username:'search_car_app_bot',webhook_configured:false,pending_updates:0});
  assert.deepEqual(f.stored,[token]);assert.deepEqual(f.calls,['getMe','getWebhookInfo']);
  assert.ok(!JSON.stringify(result).includes(token));
});
test('cancel, malformed token and wrong bot never write a secret',async()=>{
  for(const overrides of [
    {prompt:async()=>''},
    {prompt:async()=>token+'\nother'},
    {api:async()=>({id:123456789,is_bot:true,username:'wrong_bot'})},
    {api:async()=>({id:987654321,is_bot:true,username:'search_car_app_bot'})},
  ]){
    const f=fixture(overrides);await assert.rejects(configureToken(f.options));assert.deepEqual(f.stored,[]);
  }
});
test('existing webhook is not overwritten and token is not stored until conflict is resolved',async()=>{
  const f=fixture({api:async method=>method==='getMe'?{id:123456789,is_bot:true,username:'search_car_app_bot'}:
    {url:'https://another-service.test/private-path',pending_update_count:4}});
  await assert.rejects(configureToken(f.options),{message:'WEBHOOK_ALREADY_CONFIGURED'});assert.deepEqual(f.stored,[]);
});
test('transport and storage errors are sanitized and an ambiguous store is not retried',async()=>{
  const badApi=fixture({api:async()=>{throw Error('private URL '+token);}});
  await assert.rejects(configureToken(badApi.options),{message:'TELEGRAM_VERIFICATION_FAILED'});
  assert.deepEqual(badApi.stored,[]);
  let writes=0;
  const badStore=fixture({store:async()=>{writes++;throw Error('private output '+token);}});
  await assert.rejects(configureToken(badStore.options),{message:'CLOUDFLARE_STORE_UNCONFIRMED'});
  assert.equal(writes,1);
});
test('pending updates are reported without consuming or discarding them',async()=>{
  const calls=[];
  const f=fixture({api:async method=>{calls.push(method);return method==='getMe'?
    {id:123456789,is_bot:true,username:'search_car_app_bot'}:{url:'',pending_update_count:7};}});
  assert.equal((await configureToken(f.options)).pending_updates,7);
  assert.deepEqual(calls,['getMe','getWebhookInfo']);
});
test('staging secret writer rejects arbitrary names or invalid values before starting Wrangler',async()=>{
  for(const [name,value] of [['ADMIN_API_TOKEN',token],['TELEGRAM_WEBHOOK_SECRET',''],['TELEGRAM_BOT_TOKEN','a\nb']])
    await assert.rejects(storeStagingSecret(name,value),{message:'INVALID_STAGING_SECRET'});
});
