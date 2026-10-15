const DEFAULT_SELECTOR='[data-scroll-root]';

function scrollKey(node,index){
  const explicit=String(node?.dataset?.scrollKey||node?.id||'').trim();
  return explicit||`index:${index}`;
}

function scrollRoots(host,selector=DEFAULT_SELECTOR){
  if(!host?.querySelectorAll)return [];
  return [...host.querySelectorAll(selector)];
}

function applyScrollPosition(node,item){
  if(!node||!item)return;
  if(node.style){
    node.style.overflowAnchor='none';
    node.style.scrollBehavior='auto';
  }
  node.scrollTop=Number(item.top||0);
  node.scrollLeft=Number(item.left||0);
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
  const restored=[];
  scrollRoots(host,selector).forEach((node,index)=>{
    const item=remembered.get(scrollKey(node,index));
    if(!item)return;
    applyScrollPosition(node,item);
    restored.push([node,item]);
  });
  if(restored.length&&typeof requestAnimationFrame==='function'){
    requestAnimationFrame(()=>{
      for(const [node,item] of restored){
        if(node.isConnected)applyScrollPosition(node,item);
      }
    });
  }
  return restored.length>0;
}

export function resetScrollState(host,selector=DEFAULT_SELECTOR){
  for(const node of scrollRoots(host,selector)){
    if(node.style)node.style.overflowAnchor='none';
    node.scrollTop=0;
    node.scrollLeft=0;
  }
}

export function updateScrollableRegion(host,html,{previousHtml=null,force=false,snapshot=null,reset=false,selector=DEFAULT_SELECTOR}={}){
  const replaced=Boolean(force)||html!==previousHtml;
  if(!replaced)return {replaced:false,restored:false};
  const liveSnapshot=Array.isArray(snapshot)&&snapshot.length?snapshot:captureScrollState(host,selector);
  host.innerHTML=html;
  const restored=restoreScrollState(host,liveSnapshot,selector);
  if(reset&&!restored)resetScrollState(host,selector);
  return {replaced:true,restored};
}
