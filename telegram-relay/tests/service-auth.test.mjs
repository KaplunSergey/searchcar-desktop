import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { webcrypto } from 'node:crypto';
import { Miniflare } from 'miniflare';

function canonical(value) {
  if(value===null||typeof value!=='object')return JSON.stringify(value);
  if(Array.isArray(value))return `[${value.map(canonical).join(',')}]`;
  return `{${Object.keys(value).sort().map(k=>`${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
}
test('relay uses only a boolean license-service decision and fails closed on denial or failure',async()=>{
  const keys=await webcrypto.subtle.generateKey('Ed25519',true,['sign','verify']);
  const raw=Buffer.from(await webcrypto.subtle.exportKey('raw',keys.publicKey));
  const fingerprint=Buffer.from(await webcrypto.subtle.digest('SHA-256',Buffer.concat([
    Buffer.from('SEARCHCAR-DEVICE-FINGERPRINT-V1\n'),raw]))).toString('hex');
  const mf=new Miniflare({workers:[{
    name:'relay',modules:true,scriptPath:new URL('../dist/index.js',import.meta.url).pathname,
    compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],
    d1Databases:{PAIRING_DB:'rpc-pairing'},serviceBindings:{LICENSE_AUTH:{name:'auth',entrypoint:'Authorization'}},
    bindings:{PAIRING_ENABLED:'1',BOT_ID:'123456',TELEGRAM_WEBHOOK_SECRET:'fixture-webhook-secret-not-a-real-credential'},
    outboundService:()=>{throw Error('No outbound HTTP allowed');},
  },{name:'auth',modules:true,compatibilityDate:'2026-05-15',script:`
    import {WorkerEntrypoint} from 'cloudflare:workers';
    export class Authorization extends WorkerEntrypoint {
      async authorize(value) {
        if(value.telegram.project_id===3)throw Error('private failure detail');
        if(value.telegram.project_id===4)return {allowed:true};
        return value.telegram.project_id===1;
      }
    }
    export default {fetch(){return new Response(null,{status:404});}};`} ]});
  try {
    const db=await mf.getD1Database('PAIRING_DB','relay');
    for(const name of ['0001_private_pairing.sql','0002_recipient_reuse.sql','0003_delivery_receipts.sql','0004_chat_pairing.sql'])
      for(const sql of (await readFile(new URL('../migrations/'+name,import.meta.url),'utf8')).split(';').map(s=>s.trim()).filter(Boolean))
        await db.prepare(sql).run();
    for(const [project,status,code] of [[1,200,null],[2,403,'DEVICE_NOT_AUTHORIZED'],[3,503,'AUTHORIZATION_UNAVAILABLE'],[4,503,'AUTHORIZATION_UNAVAILABLE']]) {
      const body={protocol_version:1,request_id:crypto.randomUUID(),requested_at:new Date().toISOString(),app_version:'0.4.1',
        license_id:'10000000-0000-4000-8000-000000000001',device_id:'20000000-0000-4000-8000-000000000001',
        device:{public_key:raw.toString('base64url'),fingerprint_hash:fingerprint},audience:'searchcar-telegram-pairing-v1',
        telegram:{action:'start',owner_id:7,project_id:project}};
      const proof=Buffer.from(await webcrypto.subtle.sign('Ed25519',keys.privateKey,Buffer.from('SEARCHCAR-DEVICE-REQUEST-V1\n'+canonical(body)))).toString('base64url');
      const response=await mf.dispatchFetch('https://relay.test/v1/pairings',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({...body,proof})});
      const result=await response.json();assert.equal(response.status,status,JSON.stringify(result));
      if(code)assert.equal(result.error.code,code);
    }
    assert.equal((await db.prepare('SELECT count(*) AS n FROM pairing_requests').first()).n,1);
    assert.equal((await db.prepare('SELECT count(*) AS n FROM request_nonces').first()).n,1);
  } finally {await mf.dispose();}
});
