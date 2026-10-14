import { renderApp } from '../ui/render.js';
import * as S from './selectors.js';

export function createController({store,router,effects,commands}) {
  const app=document.querySelector('#app'),header=document.querySelector('#appHeader'),host=document.querySelector('#screenHost'),nav=document.querySelector('#bottomNav'),overlay=document.querySelector('#overlayHost'),toast=document.querySelector('#toast');
  let toastTimer=null,swipeBinding=null,lastRouteKey=null,lastToastNonce=null,renderQueued=false;
  const rendered={header:null,body:null,nav:null,sheet:null};
  const chatScrollMemory=new Map();

  function bindSwipeGesture(){
    swipeBinding?.();swipeBinding=null;
    const route=S.currentRoute(store.getState());if(route.name!=='swipe')return;
    const surface=host.querySelector('[data-profile-media]');if(!surface)return;
    let active=false,startX=0,startY=0,dx=0,dy=0,pid=null;
    const down=ev=>{if(ev.target.closest('button,a,video[controls]'))return;active=true;pid=ev.pointerId;startX=ev.clientX;startY=ev.clientY;dx=dy=0;surface.setPointerCapture?.(pid);};
    const move=ev=>{if(!active)return;dx=ev.clientX-startX;dy=ev.clientY-startY;if(Math.abs(dx)>18&&Math.abs(dx)>Math.abs(dy)*1.15){ev.preventDefault();surface.style.transform=`translateX(${dx*.32}px) rotate(${dx*.012}deg)`;surface.style.opacity=String(Math.max(.55,1-Math.abs(dx)/700));}};
    const end=async()=>{if(!active)return;active=false;surface.style.transform='';surface.style.opacity='';try{surface.releasePointerCapture?.(pid);}catch{}if(Math.abs(dx)>65&&Math.abs(dx)>Math.abs(dy)*1.15)await effects.discoverDecision(dx>0?'LIKE':'DISLIKE');};
    surface.addEventListener('pointerdown',down);surface.addEventListener('pointermove',move,{passive:false});surface.addEventListener('pointerup',end);surface.addEventListener('pointercancel',end);
    swipeBinding=()=>{surface.removeEventListener('pointerdown',down);surface.removeEventListener('pointermove',move);surface.removeEventListener('pointerup',end);surface.removeEventListener('pointercancel',end);};
  }

  function rememberChatScroll(){
    const box=host.querySelector('#chatScroll'),form=host.querySelector('[data-chat-form]'),threadId=Number(form?.dataset.threadId||0);
    if(!box||!threadId)return;
    const first=box.querySelector('[data-message-id]');
    chatScrollMemory.set(threadId,{scrollTop:box.scrollTop,bottomGap:Math.max(0,box.scrollHeight-box.scrollTop-box.clientHeight),firstId:first?.dataset.messageId||null,anchorOffset:first?first.offsetTop-box.scrollTop:0});
  }

  function restoreChatScroll(threadId){
    requestAnimationFrame(()=>{
      const box=host.querySelector('#chatScroll');if(!box)return;
      const memory=chatScrollMemory.get(Number(threadId));if(!memory){box.scrollTop=box.scrollHeight;return;}
      const first=box.querySelector('[data-message-id]');
      if(memory.firstId&&first?.dataset.messageId!==memory.firstId){const anchor=[...box.querySelectorAll('[data-message-id]')].find(el=>el.dataset.messageId===memory.firstId);if(anchor){box.scrollTop=Math.max(0,anchor.offsetTop-memory.anchorOffset);return;}}
      box.scrollTop=memory.bottomGap<80?box.scrollHeight:memory.scrollTop;
    });
  }

  function updateHtml(node,key,value,beforeReplace=null){
    if(!node||rendered[key]===value)return false;
    beforeReplace?.();
    node.innerHTML=value;
    rendered[key]=value;
    return true;
  }

  function render(){
    const state=store.getState(),route=S.currentRoute(state),out=renderApp(state);
    document.documentElement.classList.toggle('reduced-motion',!!state.settings.values.reduced_motion);
    app?.classList.toggle('chat-mode',route.name==='chat');
    app?.classList.toggle('detail-mode',['profile','history','archive'].includes(route.name));

    updateHtml(header,'header',out.header);
    const bodyChanged=updateHtml(host,'body',out.body,route.name==='chat'?rememberChatScroll:null);
    updateHtml(nav,'nav',out.nav);
    updateHtml(overlay,'sheet',out.sheet);

    if(bodyChanged){
      bindSwipeGesture();
      if(route.name==='chat')restoreChatScroll(Number(route.params?.threadId||S.currentThread(state)?.threadId||0));
    }

    const toastState=state.ui.toast;
    if(toastState?.nonce&&toastState.nonce!==lastToastNonce){
      lastToastNonce=toastState.nonce;
      toast.textContent=toastState.message;
      toast.classList.add('show');
      clearTimeout(toastTimer);
      toastTimer=setTimeout(()=>store.dispatch({type:'TOAST',value:null}),2600);
    } else if(!toastState&&lastToastNonce!==null){
      lastToastNonce=null;
      toast.classList.remove('show');
    }

    if(route.key!==lastRouteKey){
      lastRouteKey=route.key;
      requestAnimationFrame(()=>host.querySelector('[data-scroll-root]')?.scrollTo?.({top:0}));
    }
  }

  function scheduleRender(){
    if(renderQueued)return;
    renderQueued=true;
    requestAnimationFrame(()=>{renderQueued=false;render();});
  }

  document.addEventListener('click',async ev=>{const el=ev.target.closest('[data-action]');if(!el)return;if(el.closest('[data-action]')!==el)return;const action=el.dataset.action;if(['open-profile','grid-favorite','grid-like','status-single','message-menu'].includes(action))ev.stopPropagation();await commands(action,el,ev);});
  document.addEventListener('change',ev=>{const input=ev.target;if(input.matches('[data-filter-input]')){const f={...store.getState().search.filters};const key=input.dataset.filterInput;f[key]=input.type==='number'?Number(input.value):input.value;store.dispatch({type:'SEARCH_FILTERS',filters:f});effects.schedulePreview();}else if(input.matches('[data-local-filter]')){const current=store.getState().search.filters;store.dispatch({type:'SEARCH_FILTERS',filters:{...current,local:{...(current.local||{}),[input.dataset.localFilter]:input.checked}}});}else if(input.matches('[data-setting-key]'))effects.toggleSetting(input.dataset.settingKey,input.checked);});
  document.addEventListener('input',ev=>{const input=ev.target;if(input.matches('[data-message-search]'))store.dispatch({type:'MESSAGE_QUERY',value:input.value});if(input.matches('[data-filter-search]'))store.dispatch({type:'SEARCH_FILTER_QUERY',value:input.value});if(input.matches('[data-filter-input]')&&input.type!=='number'){const current=store.getState().search.filters;store.dispatch({type:'SEARCH_FILTERS',filters:{...current,[input.dataset.filterInput]:input.value}});effects.schedulePreview();}if(input.closest('[data-chat-form]')&&input.name==='text'){const t=S.currentThread(store.getState());if(t&&store.getState().messaging.status.typing_enabled!==false){clearTimeout(input.__typingTimer);effects.sendPresence(t.threadId,'composing');input.__typingTimer=setTimeout(()=>effects.sendPresence(t.threadId,'paused'),1800);}}});
  document.addEventListener('submit',async ev=>{const form=ev.target;if(form.matches('[data-chat-form]')){ev.preventDefault();await effects.sendChat(form);}else if(form.matches('[data-profile-data-form]')){ev.preventDefault();await effects.saveProfileData(form);}else if(form.matches('[data-encounter-services-form]')){ev.preventDefault();await effects.saveEncounterServices(form);}else if(form.matches('[data-price-form]')){ev.preventDefault();await effects.addPrice(form);}else if(form.matches('[data-merge-form]')){ev.preventDefault();await effects.merge(form);}else if(form.matches('[data-attach-form]')){ev.preventDefault();await effects.sendAttachment(form);}else if(form.matches('[data-create-person-form]')){ev.preventDefault();await effects.createPersonAndAssign(form);}else if(form.matches('[data-pricing-form]')){ev.preventDefault();await effects.acceptPricing(form);}else if(form.matches('[data-pricing-settings-form]')){ev.preventDefault();await effects.savePricingSettings(form);}});
  document.addEventListener('keydown',ev=>{if(ev.key==='Escape'){if(store.getState().ui.sheet)store.dispatch({type:'SHEET',value:null});else router.back();}if(ev.key==='ArrowLeft'&&S.currentRoute(store.getState()).name==='swipe')effects.discoverDecision('DISLIKE');if(ev.key==='ArrowRight'&&S.currentRoute(store.getState()).name==='swipe')effects.discoverDecision('LIKE');if((ev.key==='Backspace'||ev.key==='z')&&ev.altKey&&S.currentRoute(store.getState()).name==='swipe')effects.undoDecision();});

  const unsubscribe=store.subscribe(scheduleRender);
  render();
  return Object.freeze({render,scheduleRender,dispose(){unsubscribe();swipeBinding?.();clearTimeout(toastTimer);}});
}
