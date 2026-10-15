import { renderApp } from '../ui/render.js';
import { syncDiscoverMap } from '../ui/discover_map.js';
import * as S from './selectors.js';
import { captureScrollState, updateScrollableRegion } from './scroll_state.js';

function sheetRenderKey(sheet){
  if(!sheet?.kind)return null;
  const payload=sheet.payload||{};
  const identity=[
    payload.personId,payload.threadId,payload.endpointId,payload.messageId,payload.listingId,payload.extractionId,
    payload.message?.id,payload.proposal?.price_extraction_id,payload.encounter?.encounter_id,payload.kind
  ].filter(value=>value!==undefined&&value!==null&&value!=='').join(':');
  return identity?`${sheet.kind}:${identity}`:String(sheet.kind);
}

export function createController({store,router,effects,commands}) {
  const app=document.querySelector('#app');
  const header=document.querySelector('#appHeader');
  const host=document.querySelector('#screenHost');
  const nav=document.querySelector('#bottomNav');
  const overlay=document.querySelector('#overlayHost');
  const toast=document.querySelector('#toast');
  let toastTimer=null;
  let swipeBinding=null;
  let lastRouteKey=null;
  let lastBodyHtml=null;
  let lastHeaderHtml=null;
  let lastNavHtml=null;
  let lastOverlayHtml=null;
  let lastSheetKey=null;
  let lastSwipeAnalysisKey=null;
  let autoFocusedChatKey=null;
  let lastEditingMessageId=0;
  const chatScrollMemory=new Map();
  const screenScrollMemory=new Map();

  function rememberScreenScroll(routeKey){
    if(!routeKey)return;
    const snapshot=captureScrollState(host);
    if(!snapshot.length)return;
    screenScrollMemory.delete(routeKey);
    screenScrollMemory.set(routeKey,snapshot);
    while(screenScrollMemory.size>40)screenScrollMemory.delete(screenScrollMemory.keys().next().value);
  }

  function captureManagedFocus(){
    const active=document.activeElement;
    if(!active||!host.contains(active)||!active.matches?.('[data-chat-form] textarea[name="text"]'))return null;
    return {
      selector:'[data-chat-form] textarea[name="text"]',
      value:'value' in active?active.value:null,
      selectionStart:typeof active.selectionStart==='number'?active.selectionStart:null,
      selectionEnd:typeof active.selectionEnd==='number'?active.selectionEnd:null,
      selectionDirection:active.selectionDirection||'none'
    };
  }

  function restoreManagedFocus(snapshot,{restoreValue=true}={}){
    if(!snapshot)return false;
    const target=host.querySelector(snapshot.selector);
    if(!target)return false;
    if(restoreValue&&snapshot.value!==null&&'value' in target)target.value=snapshot.value;
    try{target.focus({preventScroll:true});}catch{target.focus();}
    if(typeof target.setSelectionRange==='function'){
      const max=String(target.value||'').length;
      const start=restoreValue&&snapshot.selectionStart!==null?Math.max(0,Math.min(max,snapshot.selectionStart)):max;
      const end=restoreValue&&snapshot.selectionStart!==null?Math.max(start,Math.min(max,snapshot.selectionEnd??start)):max;
      try{target.setSelectionRange(start,end,restoreValue?snapshot.selectionDirection||'none':'none');}catch{}
    }
    return document.activeElement===target;
  }

  function patchFocusedMessageSearch(nextHtml){
    const search=host.querySelector('[data-message-search]');
    if(!search||document.activeElement!==search)return false;
    const currentScroll=search.closest('.screen-scroll');
    if(!currentScroll)return false;
    const template=document.createElement('template');
    template.innerHTML=nextHtml.trim();
    const nextScroll=template.content.querySelector('.messages-screen .screen-scroll');
    if(!nextScroll)return false;

    const currentFilters=currentScroll.querySelector('.message-filters');
    const nextFilters=nextScroll.querySelector('.message-filters');
    if(currentFilters&&nextFilters&&currentFilters.innerHTML!==nextFilters.innerHTML)currentFilters.innerHTML=nextFilters.innerHTML;

    const currentList=currentScroll.querySelector('.thread-list');
    const nextList=nextScroll.querySelector('.thread-list');
    if(!currentList||!nextList)return false;
    if(currentList.innerHTML!==nextList.innerHTML)currentList.innerHTML=nextList.innerHTML;

    const currentWarning=currentScroll.querySelector('.whatsapp-disconnected-banner');
    const nextWarning=nextScroll.querySelector('.whatsapp-disconnected-banner');
    if(currentWarning&&!nextWarning)currentWarning.remove();
    else if(!currentWarning&&nextWarning)currentScroll.insertBefore(nextWarning.cloneNode(true),currentFilters||currentScroll.firstChild);
    else if(currentWarning&&nextWarning&&currentWarning.innerHTML!==nextWarning.innerHTML)currentWarning.innerHTML=nextWarning.innerHTML;
    return true;
  }

  function focusChatComposer(){
    const textarea=host.querySelector('[data-chat-form] textarea[name="text"]');
    if(!textarea)return false;
    try{textarea.focus({preventScroll:true});}catch{textarea.focus();}
    const end=String(textarea.value||'').length;
    try{textarea.setSelectionRange(end,end);}catch{}
    return document.activeElement===textarea;
  }

  function bindSwipeGesture(){
    swipeBinding?.();
    swipeBinding=null;
    if(!S.isSwipeDiscover(store.getState()))return;
    const surface=host.querySelector('[data-profile-media]');
    if(!surface)return;
    let active=false,startX=0,startY=0,dx=0,dy=0,pid=null;
    const down=ev=>{
      if(ev.target.closest('button,a,video[controls]'))return;
      active=true;
      pid=ev.pointerId;
      startX=ev.clientX;
      startY=ev.clientY;
      dx=dy=0;
      surface.setPointerCapture?.(pid);
    };
    const move=ev=>{
      if(!active)return;
      dx=ev.clientX-startX;
      dy=ev.clientY-startY;
      if(Math.abs(dx)>18&&Math.abs(dx)>Math.abs(dy)*1.15){
        ev.preventDefault();
        surface.style.transform=`translateX(${dx*.32}px) rotate(${dx*.012}deg)`;
        surface.style.opacity=String(Math.max(.55,1-Math.abs(dx)/700));
      }
    };
    const end=async()=>{
      if(!active)return;
      active=false;
      surface.style.transform='';
      surface.style.opacity='';
      try{surface.releasePointerCapture?.(pid);}catch{}
      if(Math.abs(dx)>65&&Math.abs(dx)>Math.abs(dy)*1.15)await effects.discoverDecision(dx>0?'LIKE':'DISLIKE');
    };
    surface.addEventListener('pointerdown',down);
    surface.addEventListener('pointermove',move,{passive:false});
    surface.addEventListener('pointerup',end);
    surface.addEventListener('pointercancel',end);
    swipeBinding=()=>{
      surface.removeEventListener('pointerdown',down);
      surface.removeEventListener('pointermove',move);
      surface.removeEventListener('pointerup',end);
      surface.removeEventListener('pointercancel',end);
    };
  }

  function rememberChatScroll(){
    const box=host.querySelector('#chatScroll');
    const form=host.querySelector('[data-chat-form]');
    const threadId=Number(form?.dataset.threadId||0);
    if(!box||!threadId)return;
    const first=box.querySelector('[data-message-id]');
    chatScrollMemory.set(threadId,{
      scrollTop:box.scrollTop,
      bottomGap:Math.max(0,box.scrollHeight-box.scrollTop-box.clientHeight),
      firstId:first?.dataset.messageId||null,
      anchorOffset:first?first.offsetTop-box.scrollTop:0
    });
  }

  function restoreChatScroll(threadId){
    requestAnimationFrame(()=>{
      const box=host.querySelector('#chatScroll');
      if(!box)return;
      const memory=chatScrollMemory.get(Number(threadId));
      if(!memory){box.scrollTop=box.scrollHeight;return;}
      const first=box.querySelector('[data-message-id]');
      if(memory.firstId&&first?.dataset.messageId!==memory.firstId){
        const anchor=[...box.querySelectorAll('[data-message-id]')].find(el=>el.dataset.messageId===memory.firstId);
        if(anchor){box.scrollTop=Math.max(0,anchor.offsetTop-memory.anchorOffset);return;}
      }
      box.scrollTop=memory.bottomGap<80?box.scrollHeight:memory.scrollTop;
    });
  }

  function maybeAutoAnalyzeSwipe(state){
    if(!S.isSwipeDiscover(state)||!state.settings.values.visual_check_enabled){lastSwipeAnalysisKey=null;return;}
    const vm=S.currentProfileBundle(state);
    const detail=vm?.personId?S.detailFor(state,vm.personId):null;
    if(!detail)return;
    const media=detail.media?.[S.profileMediaIndex(state,detail.personId)];
    if(!media?.media_asset_id)return;
    const key=`${detail.personId}:${media.media_asset_id}`;
    if(key===lastSwipeAnalysisKey)return;
    lastSwipeAnalysisKey=key;
    queueMicrotask(()=>effects.autoAnalyzeCurrent());
  }

  function updateRangePreview(input){
    const root=input.closest('.dual-range');
    if(!root)return;
    const output=root.parentElement?.querySelector(`[data-range-output="${input.dataset.filterInput}"]`);
    if(output)output.textContent=input.value;
    const values=[...root.querySelectorAll('input[type="range"]')].map(el=>Number(el.value)).sort((x,y)=>x-y);
    const min=Number(root.dataset.rangeMin||0);
    const max=Number(root.dataset.rangeMax||100);
    const track=root.querySelector('.dual-range-track');
    if(track&&values.length===2&&max>min){
      track.style.setProperty('--range-from',`${((values[0]-min)/(max-min))*100}%`);
      track.style.setProperty('--range-to',`${((values[1]-min)/(max-min))*100}%`);
    }
  }

  function render(){
    rememberChatScroll();
    rememberScreenScroll(lastRouteKey);
    const focusSnapshot=captureManagedFocus();

    const state=store.getState();
    const route=S.currentRoute(state);
    const out=renderApp(state);
    const routeChanged=route.key!==lastRouteKey;
    const editingMessageId=route.name==='chat'?Number(state.messaging.editing?.id||0):0;
    const editingChanged=editingMessageId!==lastEditingMessageId;
    const nextSheetKey=sheetRenderKey(state.ui.sheet);
    const sheetChanged=nextSheetKey!==lastSheetKey;
    const overlaySnapshot=!sheetChanged&&nextSheetKey?captureScrollState(overlay,'.bottom-sheet'):[];

    document.documentElement.classList.toggle('reduced-motion',!!state.settings.values.reduced_motion);
    app?.classList.toggle('chat-mode',route.name==='chat');
    app?.classList.toggle('detail-mode',['profile','history','archive'].includes(route.name));

    if(out.header!==lastHeaderHtml){header.innerHTML=out.header;lastHeaderHtml=out.header;}
    if(out.nav!==lastNavHtml){nav.innerHTML=out.nav;lastNavHtml=out.nav;}

    const screenUpdate=route.name==='messages'&&!routeChanged&&patchFocusedMessageSearch(out.body)
      ? {replaced:false,restored:false}
      : updateScrollableRegion(host,out.body,{
          previousHtml:lastBodyHtml,
          force:routeChanged,
          snapshot:screenScrollMemory.get(route.key),
          reset:routeChanged
        });
    lastBodyHtml=out.body;

    updateScrollableRegion(overlay,out.sheet,{
      previousHtml:lastOverlayHtml,
      force:sheetChanged,
      snapshot:sheetChanged?null:overlaySnapshot,
      reset:sheetChanged,
      selector:'.bottom-sheet'
    });
    lastOverlayHtml=out.sheet;
    lastSheetKey=nextSheetKey;

    if(out.toast){
      toast.textContent=out.toast;
      toast.classList.add('show');
      clearTimeout(toastTimer);
      toastTimer=setTimeout(()=>{
        toast.classList.remove('show');
        store.dispatch({type:'TOAST',value:null});
      },2600);
    }

    lastRouteKey=route.key;
    lastEditingMessageId=editingMessageId;
    if(screenUpdate.replaced)bindSwipeGesture();
    syncDiscoverMap({host,state,store,effects});
    maybeAutoAnalyzeSwipe(state);
    if(screenUpdate.replaced&&!routeChanged)restoreManagedFocus(focusSnapshot,{restoreValue:!editingChanged});
    if(route.name==='chat'&&screenUpdate.replaced)restoreChatScroll(Number(route.params?.threadId||S.currentThread(state)?.threadId||0));
    if(route.name==='chat'){
      const chatFocusKey=`${route.key}:${Number(route.params?.threadId||S.currentThread(state)?.threadId||0)}`;
      if(autoFocusedChatKey!==chatFocusKey&&focusChatComposer())autoFocusedChatKey=chatFocusKey;
    }else autoFocusedChatKey=null;
  }

  document.addEventListener('click',async ev=>{
    const el=ev.target.closest('[data-action]');
    if(!el)return;
    if(el.closest('[data-action]')!==el)return;
    const action=el.dataset.action;
    if(['open-profile','grid-favorite','grid-like','list-favorite','status-single','message-menu'].includes(action))ev.stopPropagation();
    await commands(action,el,ev);
  });

  document.addEventListener('change',ev=>{
    const input=ev.target;
    if(input.matches('[data-filter-input]')){
      const f=structuredClone(store.getState().search.filters),key=input.dataset.filterInput;
      f[key]=['number','range'].includes(input.type)?Number(input.value):input.value;
      if(key==='ageMin'&&f.ageMin>f.ageMax)f.ageMax=f.ageMin;
      if(key==='ageMax'&&f.ageMax<f.ageMin)f.ageMin=f.ageMax;
      if(key==='heightMin'&&f.heightMin>f.heightMax)f.heightMax=f.heightMin;
      if(key==='heightMax'&&f.heightMax<f.heightMin)f.heightMin=f.heightMax;
      store.dispatch({type:'SEARCH_FILTERS',filters:f});
      effects.schedulePreview();
    }else if(input.matches('[data-local-filter]')){
      const f=structuredClone(store.getState().search.filters);
      f.local={...(f.local||{}),[input.dataset.localFilter]:input.checked};
      store.dispatch({type:'SEARCH_FILTERS',filters:f});
      if(input.dataset.localFilter==='hideBusinessProfiles'&&input.checked)void effects.classifyBusinessProfiles();
    }else if(input.matches('[data-setting-key]'))effects.toggleSetting(input.dataset.settingKey,input.checked);
    else if(input.matches('[data-list-sort]'))store.dispatch({type:'LIST_SORT',value:input.value});
  });

  document.addEventListener('input',ev=>{
    const input=ev.target;
    if(input.matches('[data-message-search]'))store.dispatch({type:'MESSAGE_QUERY',value:input.value});
    if(input.matches('[data-filter-search]'))store.dispatch({type:'SEARCH_FILTER_QUERY',value:input.value});
    if(input.matches('[data-filter-input][type="range"]'))updateRangePreview(input);
    else if(input.matches('[data-filter-input]')&&input.type!=='number'){
      const f=structuredClone(store.getState().search.filters);
      f[input.dataset.filterInput]=input.value;
      store.dispatch({type:'SEARCH_FILTERS',filters:f});
      effects.schedulePreview();
    }
    if(input.closest('[data-chat-form]')&&input.name==='text'){
      const t=S.currentThread(store.getState());
      if(t&&store.getState().messaging.status.typing_enabled!==false){
        clearTimeout(input.__typingTimer);
        effects.sendPresence(t.threadId,'composing');
        input.__typingTimer=setTimeout(()=>effects.sendPresence(t.threadId,'paused'),1800);
      }
    }
  });

  document.addEventListener('submit',async ev=>{
    const form=ev.target;
    if(form.matches('[data-chat-form]')){ev.preventDefault();await effects.sendChat(form);}
    else if(form.matches('[data-profile-data-form]')){ev.preventDefault();await effects.saveProfileData(form);}
    else if(form.matches('[data-encounter-services-form]')){ev.preventDefault();await effects.saveEncounterServices(form);}
    else if(form.matches('[data-price-form]')){ev.preventDefault();await effects.addPrice(form);}
    else if(form.matches('[data-merge-form]')){ev.preventDefault();await effects.merge(form);}
    else if(form.matches('[data-attach-form]')){ev.preventDefault();await effects.sendAttachment(form);}
    else if(form.matches('[data-create-person-form]')){ev.preventDefault();await effects.createPersonAndAssign(form);}
    else if(form.matches('[data-pricing-form]')){ev.preventDefault();await effects.acceptPricing(form);}
    else if(form.matches('[data-pricing-settings-form]')){ev.preventDefault();await effects.savePricingSettings(form);}
  });

  document.addEventListener('keydown',ev=>{
    if(ev.key==='Escape'){
      if(store.getState().ui.sheet)store.dispatch({type:'SHEET',value:null});
      else router.back();
    }
    if(ev.key==='ArrowLeft'&&S.isSwipeDiscover(store.getState()))effects.discoverDecision('DISLIKE');
    if(ev.key==='ArrowRight'&&S.isSwipeDiscover(store.getState()))effects.discoverDecision('LIKE');
    if((ev.key==='Backspace'||ev.key==='z')&&ev.altKey&&S.isSwipeDiscover(store.getState()))effects.undoDecision();
  });

  store.subscribe(render);
  render();
  return Object.freeze({render});
}
