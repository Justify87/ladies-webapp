function scrollKey(node,index){
  const explicit=String(node?.dataset?.scrollKey||node?.id||'').trim();
  return explicit||`index:${index}`;
}

export function captureScrollState(host){
  if(!host?.querySelectorAll)return [];
  return [...host.querySelectorAll('[data-scroll-root]')].map((node,index)=>({
    key:scrollKey(node,index),
    top:Number(node.scrollTop||0),
    left:Number(node.scrollLeft||0)
  }));
}

export function restoreScrollState(host,snapshot){
  if(!host?.querySelectorAll||!Array.isArray(snapshot)||!snapshot.length)return false;
  const remembered=new Map(snapshot.map(item=>[String(item.key),item]));
  let restored=false;
  [...host.querySelectorAll('[data-scroll-root]')].forEach((node,index)=>{
    const item=remembered.get(scrollKey(node,index));
    if(!item)return;
    node.scrollTop=Number(item.top||0);
    node.scrollLeft=Number(item.left||0);
    restored=true;
  });
  return restored;
}

export function resetScrollState(host){
  if(!host?.querySelectorAll)return;
  for(const node of host.querySelectorAll('[data-scroll-root]')){
    node.scrollTop=0;
    node.scrollLeft=0;
  }
}
