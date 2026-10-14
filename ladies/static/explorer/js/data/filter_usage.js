const KEY='ladies.explorer.filter-usage.v1';
const DEFAULT_SKIP=new Set(['geschlecht:weiblich','treff:treffen-besuchbar','treff:treffen-haus','treff:treffen-hotel','treff:treffen-buero']);

function read(){try{return JSON.parse(localStorage.getItem(KEY)||'{}')||{};}catch{return {};}}
function write(value){try{localStorage.setItem(KEY,JSON.stringify(value));}catch{}}
function labels(config){const map=new Map();for(const group of config?.filter_groups||[])for(const opt of group.options||[])map.set(`${group.key}:${opt.value}`,{group:group.key,value:String(opt.value),label:String(opt.label||opt.value)});for(const opt of config?.categories||[])if(opt.value)map.set(`category:${opt.value}`,{group:'category',value:String(opt.value),label:String(opt.label||opt.value)});return map;}

export function recordFilterUsage(filters,config){const counts=read();const seen=new Set();const add=(key)=>{if(!key||DEFAULT_SKIP.has(key)||seen.has(key))return;seen.add(key);counts[key]=Number(counts[key]||0)+1;};if(filters?.category)add(`category:${filters.category}`);for(const [group,values] of Object.entries(filters?.selected||{}))for(const value of values||[])add(`${group}:${value}`);write(counts);}

export function frequentFilters(config,limit=5){const map=labels(config),counts=read();return Object.entries(counts).map(([key,count])=>({key,count:Number(count||0),...map.get(key)})).filter(x=>x.group&&x.value&&x.label&&!DEFAULT_SKIP.has(x.key)).sort((a,b)=>b.count-a.count||a.label.localeCompare(b.label,'de')).slice(0,limit);}
