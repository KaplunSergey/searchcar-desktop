const {test} = require('node:test');
const assert = require('node:assert/strict');
const {validateDraft, channelValue, examples} = require('./auction-project-form.js');
const draft = {name:'Audi A5', url:examples.Copart, publish:false, channel:''};

test('the integrated preview script parses before opening a browser', () => {
  const html = require('node:fs').readFileSync(require('node:path').join(__dirname,'auction-app-integration.html'),'utf8');
  const script = html.match(/<script type="module">([\s\S]*?)<\/script>/)[1];
  assert.doesNotThrow(() => new (require('node:vm').Script)(script.replace(/^import .*$/gm,'')));
});

test('the preview accepts a search URL without rewriting its query', () => {
  for (const source of ['Copart','IAA']) assert.deepEqual(validateDraft({...draft,url:examples[source]}, source, 'offline'), {});
  assert.ok(examples.Copart.includes('searchCriteria='));
});
test('wrong source and direct lot links cannot silently change a project', () => {
  assert.equal(validateDraft({...draft,url:examples.IAA},'Copart','ready').url,'other:IAA');
  assert.equal(validateDraft({...draft,url:'https://www.copart.com/lot/123'},'Copart','ready').url,'search');
  assert.equal(validateDraft({...draft,url:'https://www.iaai.com/Search'},'IAA','ready').url,'search');
});
test('unsafe and unsupported URLs are rejected locally', () => {
  for (const url of ['javascript:alert(1)','http://www.copart.com/lotSearchResults?searchCriteria=x','https://www.copart.com.evil.example/lotSearchResults?searchCriteria=x','https://secret@www.copart.com/lotSearchResults?searchCriteria=x']) {
    assert.ok(validateDraft({...draft,url},'Copart','ready').url);
  }
  assert.equal(validateDraft({...draft,name:' '},'Copart','ready').name,'required');
});
test('channel formats do not claim invitation links grant access', () => {
  assert.equal(channelValue('https://t.me/auto_auctions_demo'),'@auto_auctions_demo');
  assert.equal(channelValue('@auto_auctions_demo'),'@auto_auctions_demo');
  assert.equal(channelValue('-1001234567890'),'-1001234567890');
  for (const value of ['https://t.me/+privateInvite','https://t.me/joinchat/invite','https://evil.example/channel','@']) assert.equal(channelValue(value),null);
});
test('enabled publication requires ready demo connection, channel and reviewed preview', () => {
  const enabled = {...draft,publish:true,channel:'@auto_auctions_demo',previewed:true};
  assert.deepEqual(validateDraft(enabled,'Copart','ready'),{});
  assert.equal(validateDraft(enabled,'Copart','offline').publish,'connection');
  assert.equal(validateDraft({...enabled,channel:''},'Copart','ready').channel,'channel');
  assert.equal(validateDraft({...enabled,previewed:false},'Copart','ready').preview,'preview');
  assert.deepEqual(validateDraft({...draft,channel:''},'Copart','offline'),{});
});
const {validateEncarDraft}=require('./auction-project-form.js');
test('Encar draft validates its own URL, KRW range and channel link without auction publishing',()=>{
  const valid={name:'Avante',url:'https://www.encar.com/dc/dc_carsearchlist.do?carType=kor',channel:'https://t.me/auto_auctions_demo',priceMode:'LINK',priceMin:null,priceMax:null};
  assert.deepEqual(validateEncarDraft(valid),{});
  assert.equal(validateEncarDraft({...valid,url:'https://www.copart.com/lotSearchResults'}).url,'url');
  assert.equal(validateEncarDraft({...valid,priceMode:'CUSTOM',priceMin:30000000,priceMax:20000000}).price,'price');
  assert.equal(validateEncarDraft({...valid,priceMode:'CUSTOM',priceMin:12345}).price,'price');
  assert.equal(validateEncarDraft({...valid,channel:'@auto_auctions_demo'}).channel,'channel');
});
