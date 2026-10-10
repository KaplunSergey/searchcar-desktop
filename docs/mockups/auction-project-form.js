// Local prototype only. No source requests, Telegram requests or disk persistence.
export const examples = {
  Encar:'https://www.encar.com/dc/dc_carsearchlist.do?carType=kor#!%7B%22action%22%3A%22(And.Hidden.N.)%22%2C%22page%22%3A1%2C%22limit%22%3A20%7D',
  Copart:'https://www.copart.com/lotSearchResults?searchCriteria=%7B%22query%22%3A%5B%22*%22%5D%2C%22filter%22%3A%7B%22MISC%22%3A%5B%22%23MakeCode%3AAUDI%22%2C%22%23LotModel%3A%5C%22A5%5C%22%22%5D%7D%7D',
  IAA:'https://www.iaai.com/Search?url=TJVcN0Pg055OgM3rLyqqqhE9kuXpUANoF4BPsJ0C8WM%3d'
};
export function validateEncarDraft(d){
  const errors={};if(!d.name.trim())errors.name='required';
  try{const u=new URL(d.url.trim());if(u.protocol!=='https:'||u.username||u.password||u.port||!['encar.com','www.encar.com','fem.encar.com','m.encar.com'].includes(u.hostname)||!u.pathname.includes('carsearchlist'))errors.url='url';}catch{errors.url='url';}
  if(d.priceMode==='CUSTOM'&&((d.priceMin==null&&d.priceMax==null)||[d.priceMin,d.priceMax].some(v=>v!=null&&(!Number.isInteger(v)||v<0||v>100000000||v%10000))||(d.priceMin!=null&&d.priceMax!=null&&d.priceMin>d.priceMax)))errors.price='price';
  if(d.channel.trim()){try{const u=new URL(d.channel.trim());if(!['https:','http:'].includes(u.protocol)||!['t.me','www.t.me','telegram.me','www.telegram.me'].includes(u.hostname)||u.username||u.password)errors.channel='channel';}catch{errors.channel='channel';}}
  return errors;
}
export function channelValue(value) {
  const raw = value.trim();
  if (/^-100\d+$/.test(raw)) return raw;
  if (/^@[A-Za-z][A-Za-z0-9_]{4,31}$/.test(raw)) return raw;
  try {
    const url = new URL(raw);
    if (url.protocol === 'https:' && url.hostname === 't.me' && !url.username && !url.password && !url.port && !url.search && !url.hash && /^\/[A-Za-z][A-Za-z0-9_]{4,31}\/?$/.test(url.pathname)) {
      return '@' + url.pathname.replaceAll('/','');
    }
  } catch {}
  return null;
}
export function validateDraft(draft, source, connection) {
  const errors = {};
  if (!draft.name.trim()) errors.name = 'required';
  try {
    const raw = draft.url.trim(), url = new URL(raw);
    if (url.protocol !== 'https:' || url.username || url.password || url.port || /[\r\n\t]/.test(raw)) errors.url = 'url';
    else {
      const site = ['copart.com','www.copart.com'].includes(url.hostname) ? 'Copart' : ['iaai.com','www.iaai.com'].includes(url.hostname) ? 'IAA' : null;
      if (!site) errors.url = 'url';
      else if (site !== source) errors.url = 'other:' + site;
      else if (site === 'Copart' ? url.pathname !== '/lotSearchResults' || !url.searchParams.get('searchCriteria')?.trim() : url.pathname !== '/Search' || !url.searchParams.get('url')?.trim()) errors.url = 'search';
    }
  } catch { errors.url = 'url'; }
  if (draft.channel.trim() && !channelValue(draft.channel)) errors.channel = 'channel';
  if (draft.publish) {
    if (connection !== 'ready') errors.publish = 'connection';
    if (!channelValue(draft.channel)) errors.channel = 'channel';
    if (!draft.previewed) errors.preview = 'preview';
  }
  return errors;
}
