import {execFile,spawn} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

const root=fileURLToPath(new URL('../',import.meta.url));
const run=promisify(execFile);

export async function configureToken({prompt,api,store}) {
  let token;
  try {token=(await prompt()).trim();} catch {throw Error('INPUT_CANCELLED');}
  if(!/^[1-9][0-9]{4,15}:[A-Za-z0-9_-]{30,100}$/u.test(token))throw Error('INVALID_TOKEN_FORMAT');
  let bot,webhook;
  try {bot=await api('getMe',token);webhook=await api('getWebhookInfo',token);}
  catch {throw Error('TELEGRAM_VERIFICATION_FAILED');}
  if(!bot||bot.is_bot!==true||bot.username?.toLowerCase()!=='search_car_app_bot'
      ||!Number.isSafeInteger(bot.id)||bot.id<=0||String(bot.id)!==token.split(':')[0])throw Error('WRONG_BOT');
  if(!webhook||typeof webhook.url!=='string'||!Number.isSafeInteger(webhook.pending_update_count)
      ||webhook.pending_update_count<0)throw Error('INVALID_WEBHOOK_INFO');
  if(webhook.url)throw Error('WEBHOOK_ALREADY_CONFIGURED');
  try {await store(token);} catch {throw Error('CLOUDFLARE_STORE_UNCONFIRMED');}
  return {status:'STORED',bot_id:bot.id,username:'search_car_app_bot',webhook_configured:false,
    pending_updates:webhook.pending_update_count};
}

async function hiddenPrompt() {
  process.stdout.write('SECURE_INPUT_OPEN\n');
  const {stdout}=await run('/usr/bin/osascript',['-e',
    'set answer to display dialog "Вставьте токен из BotFather для @search_car_app_bot. Токен будет проверен и сохранён только в Cloudflare staging. Не отправляйте его в чат." with title "SearchCar — токен Telegram" default answer "" with hidden answer buttons {"Отмена", "Сохранить"} default button "Сохранить" cancel button "Отмена" giving up after 300',
    '-e','return text returned of answer'],{timeout:310000,maxBuffer:4096});
  return stdout;
}

async function telegramRead(method,token) {
  if(!['getMe','getWebhookInfo'].includes(method))throw Error('UNSUPPORTED_METHOD');
  const response=await fetch(`https://api.telegram.org/bot${token}/${method}`,{
    method:'POST',redirect:'error',signal:AbortSignal.timeout(20000)});
  if(!response.ok||!response.body)throw Error('TELEGRAM_VERIFICATION_FAILED');
  const reader=response.body.getReader(),chunks=[];let size=0;
  try {
    while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;
      if(size>65536){await reader.cancel();throw Error('INVALID_RESPONSE');}chunks.push(value);}
  } finally {reader.releaseLock();}
  const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if(data?.ok!==true)throw Error('TELEGRAM_VERIFICATION_FAILED');
  return data.result;
}

export async function storeStagingSecret(name,value) {
  if(!['TELEGRAM_BOT_TOKEN','TELEGRAM_WEBHOOK_SECRET'].includes(name)
      ||typeof value!=='string'||!value||value.length>256||/[\r\n]/u.test(value))throw Error('INVALID_STAGING_SECRET');
  const env={...process.env,CLOUDFLARE_ACCOUNT_ID:'d2e1b509f8e40cab560f655df4bb54ea',
    WRANGLER_LOG:'none',WRANGLER_WRITE_LOGS:'false',WRANGLER_LOG_SANITIZE:'true',WRANGLER_SEND_METRICS:'false'};
  // Never allow a custom API origin to receive the credential.
  delete env.CLOUDFLARE_API_BASE_URL;delete env.CF_API_BASE_URL;
  await new Promise((done,fail)=>{
    const child=spawn(process.execPath,[resolve(root,'node_modules/wrangler/bin/wrangler.js'),
      'secret','put',name,'--config','telegram-relay/wrangler.jsonc','--env','staging'],
      {cwd:root,env,stdio:['pipe','ignore','ignore'],timeout:120000});
    child.once('error',fail);child.once('close',code=>code===0?done():fail(Error('STORE_UNCONFIRMED')));
    child.stdin.once('error',fail);child.stdin.end(value+'\n');
  });
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  configureToken({prompt:hiddenPrompt,api:telegramRead,store:token=>storeStagingSecret('TELEGRAM_BOT_TOKEN',token)})
    .then(result=>process.stdout.write(JSON.stringify(result)+'\n'))
    .catch(error=>{
      const known=['INPUT_CANCELLED','INVALID_TOKEN_FORMAT','TELEGRAM_VERIFICATION_FAILED','WRONG_BOT',
        'INVALID_WEBHOOK_INFO','WEBHOOK_ALREADY_CONFIGURED','CLOUDFLARE_STORE_UNCONFIRMED'];
      process.stderr.write(JSON.stringify({status:'NOT_COMPLETED',code:known.includes(error.message)?error.message:'SETUP_FAILED'})+'\n');
      process.exitCode=1;
    });
}
