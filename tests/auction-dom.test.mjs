import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

for (const source of ['copart','iaa']) {
  test(`${source} transient document without a body is loading, not a script error or empty success`, () => {
    const code=readFileSync(new URL(`../backend/app/sources/${source}.py`,import.meta.url),'utf8');
    const script=code.match(/dom_script = r"""([\s\S]*?)"""/)[1];
    const document={body:null,querySelector:()=>null,querySelectorAll:()=>[]};
    const result=new Function('document',`return (${script})()`)(document);
    assert.equal(result.ready,false);
    assert.equal(result.empty,false);
    assert.deepEqual(result.rows,[]);
  });
  test(`${source} plain denial is not a CAPTCHA; an explicit human check is`, () => {
    const code=readFileSync(new URL(`../backend/app/sources/${source}.py`,import.meta.url),'utf8');
    const script=code.match(/dom_script = r"""([\s\S]*?)"""/)[1];
    const document={body:{innerText:'Access Denied'},querySelector:()=>null,querySelectorAll:()=>[]};
    const read=()=>new Function('document',`return (${script})()`)(document);
    assert.equal(read().blocked,true);
    assert.equal(read().manual_required,false);
    const prompts=['Please verify you are human','Confirm you are human','Complete the CAPTCHA to continue','Complete this security check'];
    if(source==='copart')prompts.push('Security check','CAPTCHA');
    for(const prompt of prompts){
      document.body.innerText=prompt;
      assert.equal(read().blocked,true);
      assert.equal(read().manual_required,true);
    }
    if(source==='iaa'){
      for(const [label,blocked] of [['Security check',true],['CAPTCHA',false],['Protected by reCAPTCHA',false]]){
        document.body.innerText=label;
        assert.equal(read().blocked,blocked);
        assert.equal(read().manual_required,false);
      }
      document.body.innerText='Please verify that you are a human';
      assert.equal(read().manual_required,true);
      assert.equal(read().blocked,true);
    }
  });
}

test('Copart does not wait for a retained but CSS-hidden cookie overlay', () => {
  const code=readFileSync(new URL('../backend/app/sources/copart.py',import.meta.url),'utf8');
  const script=code.match(/dom_script = r"""([\s\S]*?)"""/)[1];
  const overlay={getClientRects:()=>[{}]};
  const document={body:{innerText:''},querySelector:()=>null,querySelectorAll:selector=>selector.includes('fc-dialog-overlay')?[overlay]:[]};
  const result=new Function('document','getComputedStyle',`return (${script})()`)(document,()=>({visibility:'hidden'}));
  assert.equal(result.consent_required,false);
});

test('Copart reports visible OneTrust consent even with a retained hidden FC overlay', () => {
  const code=readFileSync(new URL('../backend/app/sources/copart.py',import.meta.url),'utf8');
  const script=code.match(/dom_script = r"""([\s\S]*?)"""/)[1];
  const hidden={getClientRects:()=>[]},visible={getClientRects:()=>[{}]};
  const document={body:{innerText:''},querySelector:()=>null,querySelectorAll:selector=>selector.includes('#onetrust-banner-sdk')?[hidden,visible]:[]};
  const result=new Function('document','getComputedStyle',`return (${script})()`)(document,()=>({visibility:'visible'}));
  assert.equal(result.consent_required,true);
});

test('IAA server-rendered partial rows or an uninitialized pager are not a ready page', () => {
  const code=readFileSync(new URL('../backend/app/sources/iaa.py',import.meta.url),'utf8');
  const script=code.match(/dom_script = r"""([\s\S]*?)"""/)[1];
  const link={href:'https://www.iaai.com/VehicleDetail/123~US',innerText:'Real car'};
  const row={querySelector:s=>s.startsWith('h4 a')?link:null,querySelectorAll:()=>[]};
  let rows=[row],pager=true;
  const grid={querySelectorAll:()=>rows,getAttribute:()=>null};
  const document={body:{innerText:''},querySelectorAll:()=>[],querySelector:selector=>({
    '#ListingGrid':grid,'#ResultFoundOnPage':{parentElement:{innerText:'2 VEHICLES'}},
    '#pageSizeHeader':{innerText:'100'},'#ddlCity option:checked':{textContent:'Chicago'},
    '#divPagination button[aria-label="Next"]':pager?{disabled:true}:null,
    '#divPagination button.btn-page.active':pager?{innerText:'1'}:null,
  })[selector]||null};
  const read=()=>new Function('document',`return (${script})()`)(document);
  assert.equal(read().ready,false);
  rows=[row,row];
  assert.equal(read().ready,true);
  pager=false;
  assert.equal(read().ready,false);
});

test('Copart Next preserves the complete initial search payload but advances the native cursor', () => {
  const code=readFileSync(new URL('../backend/app/sources/copart.py',import.meta.url),'utf8');
  const script=code.match(/pagination_script = r"""([\s\S]*?)"""/)?.[1];
  assert.ok(script,'Copart pagination must preserve the first request criteria');
  const initial={draw:1,start:0,length:20,page:0,size:20,query:'*',
    filter:{ODM:['odometer_reading_received:[0 TO 9999999]'],YEAR:['lot_year:[2022 TO 2027]'],
      VEHT:['#VehicleTypeCode:VEHTYPE_V'],MAKE:['#MakeCode:AUDI OR #MakeDesc:Audi'],MODL:['#LotModel:"A4"']},
    exclusions:['fixture'],watchListOnly:false,freeFormSearch:false,search:{value:'',regex:false}};
  let current=structuredClone(initial),handler;
  const table={DataTable:()=>({ajax:{params:()=>current}}),off:()=>table,
    one:(event,fn)=>{assert.equal(event,'preXhr.dt.searchcar');handler=fn;return table;}};
  const jQuery=()=>table;
  jQuery.fn={dataTable:{isDataTable:()=>true}};
  const run=()=>new Function('jQuery',`return (${script})()`)(jQuery);
  for(const page of [1,2]){
    const expected=structuredClone(initial);
    assert.equal(run(),true);
    const next={...structuredClone(current),draw:page+1,start:page*20,page,
      filter:{ODM:initial.filter.ODM,YEAR:initial.filter.YEAR},
      includeTagByField:{ODM:'{!tag=ODM}',YEAR:'{!tag=YEAR}'}};
    // Site internals mutate their old object too: the saved request must be a copy.
    current.filter.MAKE=[];
    handler({}, {}, next);
    for(const [key,value] of Object.entries({draw:page+1,start:page*20,page})) assert.equal(next[key],value);
    assert.deepEqual(next.filter,expected.filter);
    assert.deepEqual(next.exclusions,expected.exclusions);
    assert.equal(next.includeTagByField,undefined);
    assert.deepEqual(next.query,expected.query);
    assert.equal(next.length,20);
    assert.equal(next.size,20);
    current=next;
  }
  assert.equal(new Function(`return (${script})()`)(),false);
  jQuery.fn.dataTable.isDataTable=()=>false;
  assert.equal(run(),false);
  jQuery.fn.dataTable.isDataTable=()=>true;
  current=undefined;
  assert.equal(run(),false);
});
