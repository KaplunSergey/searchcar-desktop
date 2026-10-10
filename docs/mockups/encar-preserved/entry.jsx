import {createRoot} from 'react-dom/client';
import {useState} from 'react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {Projects,Project,Favorites,Scans} from 'virtual:encar-components';
import {dict} from '../../../app/translations';
import styles from '../../../app/globals.css?inline';
import details from '../../../app/details.css?inline';
import {createPreviewData} from './preview-data.mjs';
import {configure} from './runtime';

// Keep the actual Encar presentation inside a CSS boundary, not the auction card.
let remembered={view:'projects',projectId:1,selected:[]};
let previousCars=[];
export function mount(host,context) {
 const data=createPreviewData(context.projects,context.lots);
 for(const car of data.cars){const old=previousCars.find(c=>c.encar_id===car.encar_id);if(old){car.favorite=old.favorite;car.viewed=old.viewed;}}
 previousCars=data.cars;
 const locale=context.lang==='uk'?'uk':'ru',t=key=>dict[locale][key];
 const notice=()=>context.notice(locale==='uk'?'Лише макет: дія пошуку, видалення або переходу на сайт не виконується.':'Только макет: поиск, удаление или переход на сайт не выполняется.');
 configure(data.request,notice);
 const client=new QueryClient({defaultOptions:{queries:{retry:false,staleTime:Infinity,gcTime:Infinity,refetchOnWindowFocus:false}}});
 for(const p of data.projects){client.setQueryData(['project',p.id],p);client.setQueryData(['project-cars',p.id],data.cars.filter(c=>c.project_id===p.id));}
 client.setQueryData(['favorites'],data.cars.filter(c=>c.favorite));
 const report=data.cars.slice(0,2).map((c,index)=>({car_id:c.id,project_id:c.project_id,project_name:c.project_name,
  encar_id:c.encar_id,title:c.title,change:index?'PRICE_DROP':'NEW',price:c.price,
  old_price:index?17800000:null,updated_at:c.updated_at}));
 const pagination={1:{mode:'ALL_PAGES',current_page:2,total_pages:2,pages_visited:2,found_count:data.cars.length,complete:true}};
 const scans=[
  {id:101,kind:'MANUAL',status:'RUNNING',progress:25,created_at:'2026-10-08T10:37:00Z',payload:{project_ids:[1,2],pagination:{1:{...pagination[1],current_page:1,pages_visited:1,complete:false}}}},
  {id:102,kind:'MANUAL',status:'SUCCEEDED',progress:100,created_at:'2026-10-08T10:24:00Z',payload:{project_ids:[1],report,pagination}},
  {id:103,kind:'MANUAL',status:'SUCCEEDED',progress:100,created_at:'2026-10-08T07:15:00Z',payload:{project_ids:[1],pagination}},
  {id:104,kind:'MANUAL',status:'CANCELLED',progress:40,created_at:'2026-10-07T11:00:00Z',payload:{pagination:{1:{...pagination[1],pages_visited:1,complete:false}}}},
  {id:105,kind:'MANUAL',status:'FAILED',progress:0,created_at:'2026-10-07T09:00:00Z',payload:{failures:[{scope:'search',project_name:data.projects[0]?.name||'Encar',code:'TIMEOUT',technical:'Demo fixture: no source request was made.'}]}}
 ];
 const shadow=host.attachShadow({mode:'open'}),style=document.createElement('style');
 // Host owns the same production tokens; the sidebar/auction CSS cannot reach it.
 style.textContent=(styles+'\n'+details).replaceAll(':root',':host')+`
 :host{display:block;font:14px Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--ink)}
 .preview-root{min-width:0}
 .stat-button{display:flex;flex-direction:column;align-items:flex-start;justify-content:flex-start}
 /* Embedding boundary only: let the existing toolbars fit a narrow mockup. */
 @media(max-width:600px){
  .title-row{flex-direction:column;align-items:stretch;gap:14px}
  .title-row>div{min-width:0}
  .title-row>div:last-child{justify-content:flex-start}
  .filterbar{flex-wrap:wrap}.filterbar span{display:none}
  .filterbar select{max-width:100%}
 }
 `;
 shadow.append(style);const container=document.createElement('div');container.className='preview-root';shadow.append(container);
 const root=createRoot(container);
 function Preview(){
  const [view,setView]=useState(context.section==='runs'?'scans':remembered.view),[id,setId]=useState(remembered.projectId),[selected,setSelected]=useState(remembered.selected);
  const go=(next,projectId=id)=>{remembered.view=next;remembered.projectId=projectId;setId(projectId);setView(next);};
  const edit=p=>context.edit(p.preview_id);
  const common={t,locale,notify:notice,openCar:notice,cancelScan:notice};
  return <QueryClientProvider client={client}>{view==='scans'?<Scans {...common} scans={scans} projects={data.projects}/>
    :view==='project'?<Project {...common} projectId={id} back={()=>go('projects')} refresh={notice} edit={edit} remove={notice}/>
    :view==='favorites'?<Favorites {...common} back={()=>go('projects')}/>
    :<Projects {...common} projects={data.projects} loading={false} error={false} selected={selected}
      setSelected={ids=>{remembered.selected=ids;setSelected(ids);}}
      activeScans={[]} latestReport={scans[1]} refresh={notice}
      open={p=>go('project',p.id)} create={context.create} edit={edit} remove={notice}
      openLookupCar={notice} openReportCar={notice} openFavorites={()=>go('favorites')}/>}</QueryClientProvider>;
 }
 root.render(<Preview/>);
 return ()=>{root.unmount();client.clear();};
}
