const {test}=require('node:test'),assert=require('node:assert/strict');
test('actual Encar components receive independent project and car fixtures',async()=>{
 const {createPreviewData}=await import('./preview-data.mjs');
 const projects=[{id:'p1',name:'Avante',url:'https://www.encar.com/example',scope:'all',scan_mode:'FAST',auto:true,priceMode:'LINK'}];
 const lots=[{project:'p1',name:'Avante CN7',price:'₩ 15 600 000',meta:'2024/06 · 31 500 km'}];
 const a=createPreviewData(projects,lots),b=createPreviewData(projects,lots);
 assert.equal(a.projects[0]?.scan_mode,'FAST');
 assert.equal(a.projects[0]?.search_page_mode,'ALL_PAGES');
 assert.equal((await a.request('/projects/1/cars'))[0]?.price,15600000);
 await a.request('/projects/1/cars/1/favorite',{method:'PATCH',body:JSON.stringify({value:true})});
 assert.equal((await a.request('/favorites')).length,1);
 assert.equal((await b.request('/favorites')).length,0);
 assert.equal(lots[0].favorite,undefined);
});
test('preview refuses external endpoints and source/data mutation routes',async()=>{
 const {createPreviewData}=await import('./preview-data.mjs');const d=createPreviewData([],[]);
 for(const [path,options] of [['https://www.encar.com',{}],['/scans',{method:'POST'}],['/projects/1',{method:'DELETE'}]])await assert.rejects(d.request(path,options),/preview_action_disabled/);
});
