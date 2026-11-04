// Local-only old/new/rollback Worker API check using the real Python client.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {webcrypto, randomUUID, createHash} from 'node:crypto';
import {Miniflare} from 'miniflare';

const root = fileURLToPath(new URL('../', import.meta.url));
const [legacyBundle, reportPath] = process.argv.slice(2);
assert(legacyBundle && reportPath, 'usage: node scripts/license_server_compatibility_smoke.mjs LEGACY_BUNDLE REPORT_JSON');
const existingSchema=process.env.SEARCHCAR_COMPAT_RECOVERY_SCHEMA==='existing';
const clientBackend=process.env.SEARCHCAR_COMPAT_CLIENT_BACKEND || path.join(root,'backend');
const fingerprints={legacy_sha256:createHash('sha256').update(await readFile(legacyBundle)).digest('hex'),
  current_sha256:createHash('sha256').update(await readFile(path.join(root,'license-service/dist/index.js'))).digest('hex'),
  client_sha256:createHash('sha256').update(await readFile(path.join(clientBackend,'app/desktop_license_client.py'))).digest('hex')};
await rm(reportPath,{force:true});
// Fail before starting workerd if the execution sandbox forbids loopback.
const preflight=createServer();
await new Promise((resolve,reject)=> {
  preflight.once('error',()=>reject(new Error('Local sockets are unavailable; compatibility check was NOT run.')));
  preflight.listen(0,'127.0.0.1',()=>preflight.close(resolve));
});
const work = await mkdtemp(path.join(tmpdir(), 'searchcar-rollout-'));
const keys = await webcrypto.subtle.generateKey({name:'Ed25519'}, true, ['sign','verify']);
const publicKey = Buffer.from(await webcrypto.subtle.exportKey('raw', keys.publicKey)).toString('base64url');
const adminToken = randomUUID() + randomUUID();
const options = {
  modules:true, compatibilityDate:'2026-05-15', d1Databases:{LICENSE_DB:'rollout-fixture'},
  d1Persist:path.join(work, 'd1'), bindings:{
    LICENSE_SIGNING_PRIVATE_KEY:Buffer.from(await webcrypto.subtle.exportKey('pkcs8', keys.privateKey)).toString('base64url'),
    LICENSE_SIGNING_PUBLIC_KEY:publicKey, LICENSE_SIGNING_KEY_ID:'compat-fixture',
    CODE_PEPPER:randomUUID(), RATE_LIMIT_PEPPER:randomUUID(), OWNER_PASSWORD_PEPPER:randomUUID(),
    ADMIN_API_TOKEN:adminToken, LEASE_HOURS:'48', TRIAL_DAYS:'30',
    PUBLIC_RATE_LIMIT_PER_MINUTE:'1000', TRIAL_RATE_LIMIT_PER_HOUR:'100',
  },
};
let mf;
const server = createServer(async (req, res) => {
  try {
    assert.equal(req.method, 'POST');
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const headers = {...req.headers, 'CF-Connecting-IP':'203.0.113.55'};
    delete headers.host;
    const response = await mf.dispatchFetch('https://license.test' + req.url,
      {method:'POST', headers, body:Buffer.concat(chunks)});
    res.writeHead(response.status, {'Content-Type':'application/json'});
    res.end(await response.text());
  } catch { res.writeHead(500); res.end('{}'); }
});
const results = [];
let originalDevice;
async function verifyDatabase(renewals) {
  const db=await mf.getD1Database('LICENSE_DB');
  const devices=(await db.prepare('SELECT id,license_id,public_key,fingerprint_hash,is_active FROM devices').all()).results;
  assert.equal(devices.length,1);
  assert.equal(devices[0].is_active,1);
  if (originalDevice) assert.deepEqual(devices[0],originalDevice); else originalDevice=devices[0];
  assert.equal(await db.prepare('SELECT COUNT(*) AS n FROM licenses').first('n'),1);
  assert.equal(await db.prepare('SELECT COUNT(*) AS n FROM renewals').first('n'),renewals);
}
async function probe(phase) {
  const output = await new Promise((resolve, reject) => {
    const child = spawn(process.env.SEARCHCAR_COMPAT_PYTHON || 'python',
      [path.join(root,'scripts/license_client_rollout_probe.py'), work, `http://127.0.0.1:${server.address().port}`, phase],
      {env:{...process.env, PYTHONPATH:clientBackend, SEARCHCAR_SOURCE_TEST_MODE:'0',
        SEARCHCAR_LICENSE_PUBLIC_KEYS_JSON:JSON.stringify({'compat-fixture':publicKey}),
        SEARCHCAR_COMPAT_ADMIN_TOKEN:adminToken}, stdio:['ignore','pipe','pipe']});
    let stdout='', stderr='';
    const timer=setTimeout(() => { child.kill(); reject(new Error('client probe timeout')); },60000);
    child.stdout.on('data', chunk => {stdout+=chunk;});
    child.stderr.on('data', chunk => {stderr+=chunk;});
    child.on('error', error => {clearTimeout(timer); reject(error);});
    child.on('close', code => {clearTimeout(timer); code===0 ? resolve(stdout) : reject(new Error(stderr));});
  });
  const result=JSON.parse(output); assert.equal(result.status,'ok'); results.push(result);
}
try {
  mf = new Miniflare({...options,scriptPath:path.resolve(legacyBundle)}); await mf.ready;
  const db=await mf.getD1Database('LICENSE_DB');
  const migrations=path.join(root,'license-service/migrations');
  const {readdir}=await import('node:fs/promises');
  // D1 exec accepts a single-line statement per line; migration files are SQL scripts.
  for (const name of (await readdir(migrations)).filter(n=>n.endsWith('.sql') && (existingSchema || !n.startsWith('0007'))).sort()) {
    const sql=await readFile(path.join(migrations,name),'utf8');
    for (const statement of sql.split(';').map(s=>s.trim()).filter(Boolean)) await db.prepare(statement).run();
  }
  await new Promise((resolve,reject)=> {server.once('error',reject); server.listen(0,'127.0.0.1',resolve);});
  await probe('bootstrap');
  await verifyDatabase(1);
  if (!existingSchema) {
    const migration=await readFile(path.join(migrations,'0007_license_recovery_codes.sql'),'utf8');
    for (const statement of migration.split(';').map(s=>s.trim()).filter(Boolean)) await db.prepare(statement).run();
  }
  await mf.dispose();
  mf=new Miniflare({...options,scriptPath:path.join(root,'license-service/dist/index.js')}); await mf.ready;
  await probe('current');
  await verifyDatabase(2);
  await mf.dispose();
  mf=new Miniflare({...options,scriptPath:path.resolve(legacyBundle)}); await mf.ready;
  await probe('rollback');
  await verifyDatabase(3);
  await writeFile(reportPath,JSON.stringify({status:'ok',scope:'local synthetic D1/HTTP, not installed apps',
    recovery_schema:existingSchema?'existing':'late migration',...fingerprints,results},null,2)+'\n');
  console.log(JSON.stringify({status:'ok',results}));
} finally {
  await new Promise(resolve=>server.close(resolve));
  await mf?.dispose();
  await rm(work,{recursive:true,force:true});
}
