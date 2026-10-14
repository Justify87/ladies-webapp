import * as S from './app/selectors.js';
import { icon } from './shared/icons.js';

export function createRegressionEffects({store,router,core}) {
  let discoverBusy=false;
  const state=()=>store.getState();

  async function openProfile({personId,advertId}) {
    let pid=Number(personId||0)||null;
    const advert=String(advertId||'').trim();
    if(advert) {
      const rows=[...S.searchRows(state()),...S.listRows(state())];
      const row=rows.find(item=>String(item.advertId||'')===advert)||{advertId:advert,personId:pid};
      const vm=await core.loadBundle(row,{detail:false,quiet:true});
      pid=vm?.personId||pid;
      if(pid)await core.loadPerson(pid,{force:true,quiet:true});
    }
    if(!pid)return core.toast('Person nicht gefunden');
    router.push('profile',{personId:pid,advertId:advert||null});
    if(!state().profiles.details[String(pid)])await core.loadPerson(pid,{force:false,quiet:false});
  }

  async function discoverDecision(decision) {
    if(discoverBusy)return;
    const row=S.currentSearchRow(state());if(!row)return;
    discoverBusy=true;
    try{
      let personId=Number(row.personId||0)||null,bundleVm=null;
      if(!personId){bundleVm=await core.loadBundle(row,{detail:false,quiet:false});personId=bundleVm?.personId||null;}
      if(!personId)return core.toast('Person konnte nicht aufgelöst werden');
      const before={preference:bundleVm?.preference||row.preference||'NONE',favorite:bundleVm?.favorite??row.favorite??false,index:state().discover.index,personId};
      const replayLikes=state().settings.values.replay_likes!==false;
      const hideDislikes=state().search.filters.local?.hideDislikes!==false;
      if(decision==='LIKE')await core.mutatePerson(personId,{preference:'LIKE'});
      else if(decision==='DISLIKE')await core.mutatePerson(personId,{preference:'DISLIKE'});
      else if(decision==='FAVORITE')await core.mutatePerson(personId,{favorite:!before.favorite});
      store.dispatch({type:'DISCOVER_HISTORY_PUSH',entry:before});
      if(decision!=='FAVORITE'){
        const remains=(decision==='LIKE'&&replayLikes)||(decision==='DISLIKE'&&!hideDislikes);
        store.dispatch({type:'DISCOVER_INDEX',index:remains?before.index+1:before.index});
      }
      core.loadDashboard();
      void core.prefetchCurrent();
    }catch(error){core.toast(error.message||String(error));}
    finally{discoverBusy=false;}
  }

  return Object.freeze({openProfile,discoverDecision});
}

const PRICING_LABELS=Object.freeze({
  price_intelligence_enabled:['Preiserkennung aktiv','Preisangaben in Chats automatisch erkennen und verarbeiten.'],
  price_magic_button_enabled:['Pricing-Button anzeigen','Manuelle Preiserkennung direkt im Chat verfügbar machen.'],
  price_ocr_enabled:['Texterkennung für Bilder','Preisangaben in Bildern per OCR auslesen.'],
  price_semantic_model_enabled:['Semantische KI-Auswertung','Optionales Modell zur Interpretation komplexerer Preisangaben verwenden.']
});
const TAB_ICONS=Object.freeze({info:'info',offer:'heart',analysis:'sparkles',data:'star',encounters:'calendar',history:'list'});

export function installRegressionUi({store,effects}) {
  const expandedDescriptions=new Set();
  let swipePointer=null;
  let swipeBusy=false;
  let suppressMediaTapUntil=0;

  function enhanceAvailability() {
    const panel=document.querySelector('#screenHost .swipe-media-panel');
    const pill=document.querySelector('#screenHost .swipe-details .online-pill');
    if(!panel||!pill)return;
    pill.classList.add('media-availability');
    pill.innerHTML=`${icon('calendar')}<span>Heute verfügbar</span>`;
    panel.appendChild(pill);
  }

  function enhanceBadges() {
    document.querySelectorAll('#screenHost .swipe-details .badge-row .badge').forEach(badge=>{
      badge.title=badge.textContent.trim();
    });
  }

  function enhanceDescription() {
    const root=document.querySelector('#screenHost [data-scroll-root]');
    const personId=String(document.querySelector('#screenHost [data-profile-media]')?.dataset.personId||'');
    if(!root||!personId)return;
    root.querySelectorAll('.about-copy').forEach(copy=>{
      const card=copy.closest('.profile-card');
      if(!card)return;
      card.classList.add('about-card');
      const expanded=expandedDescriptions.has(personId);
      copy.classList.toggle('is-collapsed',!expanded);
      let toggle=card.querySelector('.about-toggle');
      if(!toggle){
        toggle=document.createElement('button');
        toggle.type='button';
        toggle.className='about-toggle';
        toggle.dataset.personId=personId;
        copy.insertAdjacentElement('afterend',toggle);
      }
      toggle.textContent=expanded?'Einklappen':'Anzeigen';
      toggle.setAttribute('aria-expanded',String(expanded));
    });
  }

  function enhanceRatings() {
    document.querySelectorAll('#screenHost .rating-row').forEach(row=>{
      const buttons=[...row.querySelectorAll('button[data-value]')];
      const selected=Number(buttons.find(button=>button.classList.contains('selected'))?.dataset.value||0);
      buttons.forEach(button=>{
        const value=Number(button.dataset.value||0);
        button.innerHTML=icon('star');
        button.classList.toggle('active',selected>0&&value<=selected);
        button.setAttribute('aria-label',`${value} von 5 Sternen`);
        button.title=`${value} von 5`;
      });
    });
  }

  function enhanceChoices() {
    document.querySelectorAll('#screenHost .choice-field button[data-value="UNKNOWN"]').forEach(button=>{
      button.textContent='Keine Angabe';
      button.title='Keine Angabe';
    });
  }

  function enhanceProfileTabs() {
    document.querySelectorAll('#screenHost .profile-tabs button[data-tab]').forEach(button=>{
      if(button.querySelector('.profile-tab-icon'))return;
      const label=button.textContent.trim();
      const tab=button.dataset.tab;
      button.textContent='';
      const ico=document.createElement('span');
      ico.className='profile-tab-icon';
      ico.innerHTML=icon(TAB_ICONS[tab]||'list');
      const text=document.createElement('span');
      text.className='profile-tab-label';
      text.textContent=label;
      button.append(ico,text);
    });
  }

  function enhancePricingSettings() {
    if(store.getState().ui.sheet?.kind!=='pricing-settings')return;
    document.querySelectorAll('#overlayHost [data-pricing-settings-form] input[name]').forEach(input=>{
      const meta=PRICING_LABELS[input.name];
      if(!meta)return;
      const label=input.closest('label');
      const copy=label?.querySelector(':scope > span:first-child');
      if(!copy)return;
      copy.classList.add('pricing-setting-copy');
      copy.innerHTML=`<b>${meta[0]}</b><small>${meta[1]}</small>`;
    });
  }

  function enhancePreviewError() {
    document.querySelector('.search-preview-error')?.remove();
    const preview=store.getState().search.preview;
    if(preview?.status!=='error'||!preview.error)return;
    const footer=document.querySelector('#screenHost .search-footer');
    if(!footer)return;
    const note=document.createElement('div');
    note.className='search-preview-error';
    note.textContent='Treffervorschau derzeit nicht verfügbar. Die Suche kann trotzdem gestartet werden.';
    footer.insertAdjacentElement('beforebegin',note);
  }

  function enhanceDom() {
    enhanceAvailability();
    enhanceBadges();
    enhanceDescription();
    enhanceRatings();
    enhanceChoices();
    enhanceProfileTabs();
    enhancePricingSettings();
    enhancePreviewError();
  }

  document.addEventListener('click',event=>{
    const toggle=event.target.closest('.about-toggle');
    if(toggle){
      event.preventDefault();
      event.stopImmediatePropagation();
      const personId=String(toggle.dataset.personId||'');
      if(expandedDescriptions.has(personId))expandedDescriptions.delete(personId);else expandedDescriptions.add(personId);
      enhanceDescription();
      return;
    }

    const tab=event.target.closest('[data-action="profile-tab"]');
    if(tab){
      const current=document.querySelector('#screenHost [data-scroll-root]');
      const top=current?.scrollTop||0;
      requestAnimationFrame(()=>requestAnimationFrame(()=>{
        const replacement=document.querySelector('#screenHost [data-scroll-root]');
        if(replacement)replacement.scrollTop=top;
      }));
    }

    if(event.target.closest('[data-action="rating-select"]'))queueMicrotask(enhanceRatings);

    if(performance.now()<suppressMediaTapUntil&&event.target.closest('.media-tap')){
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  },true);

  document.addEventListener('pointerdown',event=>{
    if(swipeBusy||S.currentRoute(store.getState()).name!=='swipe')return;
    const tap=event.target.closest('.media-tap');
    const surface=event.target.closest('[data-profile-media]');
    if(!tap||!surface)return;
    swipePointer={id:event.pointerId,startX:event.clientX,startY:event.clientY,dx:0,dy:0,surface};
  },true);

  document.addEventListener('pointermove',event=>{
    if(!swipePointer||event.pointerId!==swipePointer.id)return;
    swipePointer.dx=event.clientX-swipePointer.startX;
    swipePointer.dy=event.clientY-swipePointer.startY;
    if(Math.abs(swipePointer.dx)>18&&Math.abs(swipePointer.dx)>Math.abs(swipePointer.dy)*1.15){
      event.preventDefault();
      swipePointer.surface.style.transform=`translateX(${swipePointer.dx*.32}px) rotate(${swipePointer.dx*.012}deg)`;
      swipePointer.surface.style.opacity=String(Math.max(.55,1-Math.abs(swipePointer.dx)/700));
    }
  },{capture:true,passive:false});

  document.addEventListener('pointerup',async event=>{
    if(!swipePointer||event.pointerId!==swipePointer.id)return;
    const gesture=swipePointer;
    swipePointer=null;
    gesture.surface.style.transform='';
    gesture.surface.style.opacity='';
    if(Math.abs(gesture.dx)>65&&Math.abs(gesture.dx)>Math.abs(gesture.dy)*1.15){
      event.preventDefault();
      suppressMediaTapUntil=performance.now()+500;
      swipeBusy=true;
      try{await effects.discoverDecision(gesture.dx>0?'LIKE':'DISLIKE');}finally{swipeBusy=false;}
    }
  },true);
  document.addEventListener('pointercancel',()=>{if(swipePointer?.surface){swipePointer.surface.style.transform='';swipePointer.surface.style.opacity='';}swipePointer=null;},true);

  store.subscribe(()=>queueMicrotask(enhanceDom));
  enhanceDom();
}
