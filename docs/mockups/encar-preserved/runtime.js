// In-memory boundaries supplied by mount(). No network/OS bridge or storage.
let currentRequest=async()=>{throw Error('preview_not_mounted');};
let currentNotice=()=>{};
const memory=new Map();
export const previewStorage={getItem:key=>memory.get(key)??null,setItem:(key,value)=>memory.set(key,value)};
export const previewRequest=(path,options)=>currentRequest(path,options);
export const previewNotice=()=>currentNotice();
export function configure(request,notice){currentRequest=request;currentNotice=notice;}
