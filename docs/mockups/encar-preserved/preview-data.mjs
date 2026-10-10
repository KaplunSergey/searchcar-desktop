// Disposable UI fixtures only, never a source adapter or backend.
export function createPreviewData(projects, lots) {
  const at='2026-10-08T10:37:00Z';
  const records=projects.map((p,index)=>({id:index+1,preview_id:p.id,name:p.name,search_url:p.url,
    telegram_url:p.channel||null,scan_mode:p.scan_mode,search_page_mode:p.scope==='first'?'FIRST_PAGE':'ALL_PAGES',
    price_filter_mode:p.priceMode||'LINK',price_min_krw:p.priceMin,price_max_krw:p.priceMax,
    auto_update:p.auto,cars:lots.filter(c=>c.project===p.id).length,updated_at:at,
    latest_scan:{id:1,status:'SUCCEEDED',created_at:at}}));
  const cars=lots.map((c,index)=>({id:index+1,project_id:records.find(p=>p.preview_id===c.project)?.id,
    project_name:records.find(p=>p.preview_id===c.project)?.name,encar_id:String(80000001+index),
    title:c.name,price:Number(c.price.replace(/\D/g,'')),url:'',image:null,
    details:{year_month:c.meta.split(' · ')[0],mileage_km:Number(c.meta.split(' · ')[1]?.replace(/\D/g,'')||0),condition_summary:'UNVERIFIED'},
    favorite:false,viewed:index!==0,status:index===0?'NEW':'UPDATED',first_seen_at:at,updated_at:at,
    price_change:index===1?{direction:'DOWN',previous:17800000,current:16800000,difference:1000000,percent:5.62}:null}));
  const copy=value=>structuredClone(value);
  async function request(path,options={}) {
    const method=options.method||'GET';
    if(method==='PATCH') {
      const m=path.match(/^\/projects\/(\d+)\/cars\/(\d+)\/(favorite|viewed)$/),car=m&&cars.find(c=>c.project_id===Number(m[1])&&c.id===Number(m[2]));
      const value=JSON.parse(options.body||'{}').value;
      if(car&&typeof value==='boolean'){car[m[3]]=value;return copy(car);}
    }
    if(method==='GET') {
      if(path==='/favorites')return copy(cars.filter(c=>c.favorite));
      if(path==='/projects')return copy(records);
      const m=path.match(/^\/projects\/(\d+)(\/cars)?$/);
      if(m)return copy(m[2]?cars.filter(c=>c.project_id===Number(m[1])):records.find(p=>p.id===Number(m[1])));
      if(path.startsWith('/car-lookup?q=')) {
        const q=decodeURIComponent(path.slice('/car-lookup?q='.length)).trim(),id=q.match(/(?:^|\/)(\d{8})(?:$|[?#])/ )?.[1],car=cars.find(c=>c.encar_id===id);
        return copy({found:!!car,car,encar_id:id||q,projects:car?records.filter(p=>p.id===car.project_id):[]});
      }
    }
    throw new Error('preview_action_disabled');
  }
  return {projects:records,cars,request};
}
