const DEFAULT_SELECTOR='[data-scroll-root]';

function scrollKey(node,index){
  const explicit=String(node?.dataset?.scrollKey||node?.id||'').trim();
  return explicit||`index:${index}`;
}

function scrollRoots(host,selector=DEFAULT_SELECTOR){
  if(!host?.querySelectorAll)return [];
  return [...host.querySelectorAll(selector)];
}

export function captureScrollState(host,selector=DEFAULT_SELECTOR){
  return scrollRoots(host,selector).map((node,index)=>({
    key:scrollKey(node,index),
    top:Number(node.scrollTop||0),
    left:Number(node.scrollLeft||0)
  }));
}

export function restoreScrollState(host,snapshot,selector=DEFAULT_SELECTOR){
  if(!Array.isArray(snapshot)||!snapshot.length)return false;
  const remembered=new Map(snapshot.map(item=>[String(item.key),item]));
  let restored=false;
  scrollRoots(host,selector).forEach((node,index)=>{
    const item=remembered.get(scrollKey(node,index));
    if(!item)return;
    node.scrollTop=Number(item.top||0);
    node.scrollLeft=Number(item.left||0);
    restored=true;
  });
  return restored;
}

export function resetScrollState(host,selector=DEFAULT_SELECTOR){
  for(const node of scrollRoots(host,selector)){
    node.scrollTop=0;
    node.scrollLeft=0;
  }
}

export function updateScrollableRegion(host,html,{previousHtml=null,force=false,snapshot=null,reset=false,selector=DEFAULT_SELECTOR}={}){
  const replaced=Boolean(force)||html!==previousHtml;
  if(!replaced)return {replaced:false,restored:false};
  host.innerHTML=html;
  const restored=restoreScrollState(host,snapshot,selector);
  if(reset&&!restored)resetScrollState(host,selector);
  return {replaced:true,restored};
}
