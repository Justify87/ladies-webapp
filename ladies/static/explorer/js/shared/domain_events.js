const mapProjectionListeners=new Set();

export function subscribeMapProjectionChanges(listener){
  if(typeof listener!=='function')return ()=>{};
  mapProjectionListeners.add(listener);
  return ()=>mapProjectionListeners.delete(listener);
}

export function notifyMapProjectionChanged(reason='domain-mutation'){
  for(const listener of [...mapProjectionListeners]){
    try{listener(reason);}catch{}
  }
}
