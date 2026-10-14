function route(key,name,params={}) { return {key,name,params}; }

export function createRouter(store) {
  let suppressPop = false;
  const update = stack => store.dispatch({type:'NAV_SET',navigation:{stack,seq:store.getState().navigation.seq + 1}});
  const current = () => store.getState().navigation.stack.at(-1);
  const replaceRoot = (name,params={}) => {
    const nav = store.getState().navigation; const key = `route-${nav.seq + 1}`;
    update([route(key,name,params)]);
    history.pushState({ladiesRoute:true},'',`#${name}`);
  };
  const push = (name,params={}) => {
    const nav = store.getState().navigation; const key = `route-${nav.seq + 1}`;
    update([...nav.stack,route(key,name,params)]);
    history.pushState({ladiesRoute:true},'',`#${name}`);
  };
  const replace = (name,params={}) => {
    const nav = store.getState().navigation; const key = `route-${nav.seq + 1}`;
    const stack=nav.stack.length?[...nav.stack.slice(0,-1),route(key,name,params)]:[route(key,name,params)];
    update(stack);
    history.replaceState({ladiesRoute:true},'',`#${name}`);
  };
  const back = () => {
    const nav = store.getState().navigation;
    if (nav.stack.length > 1) {
      update(nav.stack.slice(0,-1));
      suppressPop = true;
      history.back();
      return;
    }
    if (current()?.name !== 'search') replaceRoot('search');
  };
  window.addEventListener('popstate',() => {
    if (suppressPop) { suppressPop = false; return; }
    const nav = store.getState().navigation;
    if (nav.stack.length > 1) update(nav.stack.slice(0,-1));
    else if (nav.stack[0]?.name !== 'search') update([route(`route-${nav.seq + 1}`,'search',{})]);
  });
  return Object.freeze({current,replaceRoot,push,replace,back});
}
