import { syncDiscoverMap } from './discover_map.js';

export function installDiscoverMap({store,effects,router}) {
  const host=document.querySelector('#screenHost');
  if(!host)return;
  const sync=()=>syncDiscoverMap({host,state:store.getState(),store,effects,router});
  store.subscribe(sync);
  sync();
}
