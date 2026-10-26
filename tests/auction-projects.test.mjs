import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const {AuctionProjects,sortedAuctionProjects}=await import('../app/auction-projects.tsx');
test('auction projects expose editing captured filters separately from refresh and deletion',()=>{
  const html=renderToStaticMarkup(React.createElement(AuctionProjects,{
    source:'copart',locale:'ru',projects:[{id:9,source_key:'copart',name:'Audi',cars:2}],loading:false,failed:false,showProjects:true,canRefresh:true,
    read:async()=>({}),open(){},renderStatus(){},readState:null,
    renderEdit:(id)=>React.createElement('button',{'aria-label':`Изменить фильтры ${id}`},'Edit'),
  }));
  assert.match(html,/aria-label="Изменить фильтры 9"/);
});
test('auction project actions include a separately scoped delete action without search permission',()=>{
  const html=renderToStaticMarkup(React.createElement(AuctionProjects,{
    source:'iaa',locale:'uk',projects:[{id:9,source_key:'iaa',name:'FIAT',cars:2}],loading:false,failed:false,showProjects:true,canRefresh:false,
    read:async()=>({}),open(){},renderStatus(){},readState:null,
    renderDelete:(id)=>React.createElement('button',{'aria-label':`Видалити проєкт ${id}`},'Delete'),
  }));
  assert.match(html,/aria-label="Видалити проєкт 9"/);
});
test('auction sorting uses actual refresh, puts missing/invalid dates last, and never mutates the input',()=>{
  const projects=[
    {id:3,name:'Car 10',last_refreshed_at:null,updated_at:'2027-01-01'},
    {id:2,name:'Car 2',last_refreshed_at:'invalid',created_at:'2028-01-01'},
    {id:4,name:'Z',last_refreshed_at:'2026-10-18T10:00:00Z'},
    {id:1,name:'A',last_refreshed_at:'2026-10-15T10:00:00Z'},
  ];
  assert.deepEqual(sortedAuctionProjects(projects,'updated','ru').map(p=>p.id),[4,1,2,3]);
  assert.deepEqual(sortedAuctionProjects(projects,'name','uk').map(p=>p.id),[1,2,3,4]);
  assert.deepEqual(projects.map(p=>p.id),[3,2,4,1]);
  assert.deepEqual(sortedAuctionProjects([{id:5,name:'Same'},{id:2,name:'Same'}],'updated','ru').map(p=>p.id),[2,5]);
  assert.deepEqual(sortedAuctionProjects([],'updated','ru'),[]);
});
test('Today keeps loading/errors and an empty project list outside the hidden project rows, even without browser storage',()=>{
  const previousWindow=globalThis.window;
  globalThis.window={get localStorage(){throw new Error('Storage disabled');}};
  try {
    const render=(props={})=>renderToStaticMarkup(React.createElement(AuctionProjects,{
      source:'copart',locale:'ru',projects:[],loading:false,failed:false,showProjects:false,canRefresh:false,
      read:async()=>({}),open(){},renderStatus(){},readState:null,...props
    }));
    const html=render({failed:true,readState:React.createElement('p',{role:'alert'},'Read failed')});
    assert.ok(html.indexOf('role="alert"')<html.indexOf('class="projects-collapsible-content"'));
    assert.doesNotMatch(html,/Сохранённых проектов нет/);
    assert.match(html,/Проекты: — · Авто: —/);
    const empty=render();
    assert.ok(empty.indexOf('Сохранённых проектов нет')<empty.indexOf('class="projects-collapsible-content"'));
    assert.match(render({showProjects:true}),/aria-pressed="true">☷/);
  } finally {
    if(previousWindow===undefined) delete globalThis.window;else globalThis.window=previousWindow;
  }
});
