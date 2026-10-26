import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const { auctionReportBatches, checkedAuctionReport, AuctionHistoryReport } = await import('../app/auction-report.tsx');
const report=Array.from({length:45},(_,i)=>({car_id:i+1,project_id:9,after_snapshot_id:i+101,title:`Car ${i+1}`,change:'NEW'}));
const response={scan_id:8,source_key:'copart',computer_timezone:'UTC',items:[{car_id:1,snapshot_id:101,recorded_at:'2026-10-19T10:00:00Z',issue:null,
  view:{generated_at:'2026-10-19T10:00:00Z',lot:{id:1,source_key:'copart'}}}]};
test('report snapshot reads are bounded to twenty and never request uncaptured legacy pointers',()=>{
  assert.deepEqual(auctionReportBatches([...report,{car_id:99}]).map(batch=>batch.length),[20,20,5]);
  assert.deepEqual(auctionReportBatches([{car_id:1,after_snapshot_id:-1},{car_id:2,after_snapshot_id:NaN}]),[]);
  assert.deepEqual(auctionReportBatches(report.slice(0,20))[0].map(item=>item.car_id),Array.from({length:20},(_,i)=>i+1));
});
test('historical cards reject wrong run, source, car, pointer and current/unbound timestamps',()=>{
  assert.equal(checkedAuctionReport(response,'copart',8,report.slice(0,1)),response);
  for(const mutate of [r=>r.scan_id=9,r=>r.source_key='iaa',r=>r.items.push(r.items[0]),r=>r.items[0].car_id=99,
    r=>r.items[0].snapshot_id=500,r=>r.items[0].view.lot.id=99,r=>r.items[0].view.lot.source_key='iaa',
    r=>r.items[0].recorded_at='2027-01-01T10:00:00Z']) {
    const value=structuredClone(response);mutate(value);
    assert.throws(()=>checkedAuctionReport(value,'copart',8,report.slice(0,1)));
  }
  const missing=structuredClone(response);missing.items[0]={car_id:1,view:null,issue:'SNAPSHOT_UNAVAILABLE'};
  assert.equal(checkedAuctionReport(missing,'copart',8,report.slice(0,1)),missing);
});
test('a large stored report initially renders only twenty rows with a keyboard load-more control',()=>{
  const client=new QueryClient();
  try {
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(AuctionHistoryReport,{
      source:'copart',scanId:8,uk:false,report,filter:'all',setFilter(){},read:async()=>response,renderSnapshot:()=>null
    })));
    assert.equal((html.match(/data-report-item=/g)||[]).length,20);
    assert.match(html,/Показать ещё · 20 \/ 45/);assert.doesNotMatch(html,/>Car 21</);
    assert.match(html,/Загрузка сохранённой карточки/);
  }finally{client.clear();}
});
