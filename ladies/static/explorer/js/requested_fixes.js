import * as S from './app/selectors.js';
import { escapeHtml as e, escapeAttr as a, fmtDate, euro } from './shared/html.js';
import { icon } from './shared/icons.js';
import { availabilityMode } from './search_intelligence.js';

const stateOf=store=>store.getState();
const localDateTime=value=>{if(!value)return '';const d=new Date(value);if(Number.isNaN(d.getTime()))return '';const z=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;};
const ratingButtons=(name,value)=>`<div class="rating-row" data-rating-name="${a(name)}">${[1,2,3,4,5].map(n=>`<button type="button" class="${Number(value)===n?'selected':''}" data-action="rating-select" data-name="${a(name)}" data-value="${n}">${n}</button>`).join('')}</div>`;
const choice=(name,value,options)=>`<div class="choice-field" data-choice-name="${a(name)}">${options.map(opt=>`<button type="button" class="${String(value||'UNKNOWN')===opt?'selected':''}" data-action="choice-select" data-name="${a(name)}" data-value="${a(opt)}">${e(opt)}</button>`).join('')}</div>`;
const repeatInterestLabel=value=>({YES:'Ja',MAYBE:'Vielleicht',NO:'Nein'}[String(value||'').toUpperCase()]||value||'UNKNOWN');

export function createRequestedEffects({store,core}) {
  let discoverBusy=false;
  const state=()=>stateOf(store);

  async function discoverDecision(decision) {
    if(discoverBusy)return;
    const row=S.currentSearchRow(state());
    if(!row)return;
    discoverBusy=true;
    try {
      // Search rows can contain an identity that has meanwhile been merged into
      // another person. Always hydrate the listing first and prefer its current
      // canonical person id before writing a LIKE/DISLIKE/FAVORITE decision.
      const bundleVm=await core.loadBundle(row,{detail:false,quiet:true});
      if(!bundleVm) {
        store.dispatch({type:'DISCOVER_INDEX',index:state().discover.index+1});
        void core.prefetchCurrent();
        return;
      }
      const personId=Number(bundleVm.personId||row.personId||0)||null;
      if(!personId) {
        store.dispatch({type:'DISCOVER_INDEX',index:state().discover.index+1});
        void core.prefetchCurrent();
        return;
      }
      const before={
        preference:bundleVm.preference||row.preference||'NONE',
        favorite:bundleVm.favorite??row.favorite??false,
        index:state().discover.index,
        personId
      };
      const local=state().search.filters.local||{};
      const replayLikes=state().settings.values.replay_likes!==false;
      const hideLikes=local.hideLikes===true;
      const hideDislikes=local.hideDislikes!==false;
      if(decision==='LIKE')await core.mutatePerson(personId,{preference:'LIKE'});
      else if(decision==='DISLIKE')await core.mutatePerson(personId,{preference:'DISLIKE'});
      else if(decision==='FAVORITE')await core.mutatePerson(personId,{favorite:!before.favorite});
      store.dispatch({type:'DISCOVER_HISTORY_PUSH',entry:before});
      if(decision!=='FAVORITE') {
        const remains=decision==='LIKE' ? (!hideLikes&&replayLikes) : !hideDislikes;
        store.dispatch({type:'DISCOVER_INDEX',index:remains?before.index+1:before.index});
      }
      core.loadDashboard();
      void core.prefetchCurrent();
    } catch(error) {
      core.toast(error.message||String(error));
    } finally {
      discoverBusy=false;
    }
  }

  return Object.freeze({discoverDecision});
}

function encounterForm(detail,encounter={}) {
  const listingId=encounter.listing_id||detail.listings?.[0]?.listing_id||'';
  const repeatInterest=repeatInterestLabel(encounter.repeat_interest);
  return `<form class="my-data-form requested-encounter-form" data-encounter-services-form data-full-encounter="true" data-person-id="${a(detail.personId)}" data-encounter-id="${a(encounter.encounter_id||'')}">
    <input type="hidden" name="listing_id" value="${a(listingId)}">
    <label class="field-input"><span>Zeitpunkt</span><input name="occurred_at" type="datetime-local" value="${a(localDateTime(encounter.occurred_at))}"></label>
    <label><span>Gesamteindruck</span>${ratingButtons('overall_rating',encounter.overall_rating)}</label>
    <label><span>Sympathie</span>${ratingButtons('sympathy_rating',encounter.sympathy_rating)}</label>
    <label><span>Fotos</span>${choice('photo_status',encounter.photo_status,['UNKNOWN','Wie auf Anzeige','Besser','Schlechter'])}</label>
    <label><span>Wiederholungsinteresse</span>${choice('repeat_interest',repeatInterest,['UNKNOWN','Ja','Vielleicht','Nein'])}</label>
    <label><span>Service korrekt</span>${choice('service_accuracy',encounter.service_accuracy,['UNKNOWN','Ja','Teilweise','Nein'])}</label>
    <label><span>Hygiene</span>${choice('hygiene',encounter.hygiene,['UNKNOWN','Sehr gut','Okay','Schlecht'])}</label>
    <label><span>Küssen</span>${choice('kissing',encounter.kissing,['UNKNOWN','Ja','Auf Wunsch','Nein'])}</label>
    <label><span>Verhandelbar</span>${choice('negotiable',encounter.negotiable,['UNKNOWN','Ja','Teilweise','Nein'])}</label>
    <div class="field-grid"><label class="field-input"><span>Dauer Minuten</span><input name="duration_minutes" type="number" min="0" value="${a(encounter.duration_minutes??'')}"></label><label class="field-input"><span>Preis €</span><input name="total_price_eur" type="number" min="0" step="1" value="${a(encounter.total_price_eur??'')}"></label></div>
    <label class="field-input"><span>Privatnotiz</span><textarea name="private_note" rows="4">${e(encounter.private_note||'')}</textarea></label>
    <button class="settings-primary" type="submit">${icon('check')}${encounter.encounter_id?'Treffen aktualisieren':'Treffen speichern'}</button>
  </form>`;
}

function encounterSignature(detail) {
  return `${detail.personId}|${(detail.encounters||[]).map(item=>[
    item.encounter_id,item.updated_at,item.repeat_interest,item.private_note,item.duration_minutes,item.total_price_eur,
    item.overall_rating,item.sympathy_rating,item.photo_status,item.service_accuracy,item.hygiene,item.kissing,item.negotiable
  ].join(':')).join('|')}`;
}

function dataPanel(detail) {
  const encounters=detail.encounters||[];
  const existing=encounters.map((encounter,index)=>`<details class="price-list-block requested-encounter-entry" ${index===0?'open':''}><summary><b>${e(fmtDate(encounter.occurred_at))}</b> · ${e(encounter.duration_minutes?`${encounter.duration_minutes} Min.`:'Dauer offen')} · ${e(euro(encounter.total_price_eur))}</summary>${encounterForm(detail,encounter)}</details>`).join('');
  return `<section class="profile-card requested-data-card"><div class="profile-card-head"><h2>${icon('calendar')}Daten erfassen</h2><small>${e(encounters.length)} Treffen gespeichert</small></div><p class="muted requested-data-note">Bewertungen und Privatnotizen werden jeweils dem konkreten Treffen zugeordnet.</p>${existing||'<p class="muted">Noch kein Treffen gespeichert.</p>'}<details class="price-list-block requested-new-encounter" ${encounters.length?'':'open'}><summary><b>${icon('plus')} Neues Treffen erfassen</b></summary>${encounterForm(detail,{})}</details></section>`;
}

function addHideLikesOption(store) {
  const hideDislikes=document.querySelector('#screenHost input[data-local-filter="hideDislikes"]');
  if(!hideDislikes)return;
  const row=hideDislikes.closest('.option-row');
  if(!row||row.parentElement?.querySelector('input[data-local-filter="hideLikes"]'))return;
  const on=stateOf(store).search.filters.local?.hideLikes===true;
  const label=document.createElement('label');
  label.className='option-row';
  label.innerHTML=`<span class="option-copy"><b>Bereits getätigte Likes nicht anzeigen</b><small>Bereits gelikte Profile aus Swipe- und Rasteransicht entfernen</small></span><span class="switch"><input type="checkbox" data-local-filter="hideLikes" ${on?'checked':''}><span></span></span>`;
  row.insertAdjacentElement('afterend',label);
}

function cleanGrid(hiddenAdverts) {
  document.querySelectorAll('#screenHost .grid-card-chips').forEach(node=>node.remove());
  document.querySelectorAll('#screenHost .grid-card[data-advert-id]').forEach(card=>{
    if(hiddenAdverts.has(String(card.dataset.advertId||'')))card.remove();
  });
}

function cleanSwipe(store) {
  const root=document.querySelector('#screenHost .swipe-screen');
  if(!root) {
    const heading=document.querySelector('#screenHost .empty-state h2');
    if(heading&&/Keine weiteren Profile/i.test(heading.textContent||''))heading.textContent='Du hast alle Profile angeschaut.';
    return;
  }
  root.querySelectorAll('.swipe-details .profile-meta-place,.swipe-details .badge-row').forEach(node=>node.remove());
  const identity=root.querySelector('.swipe-details .profile-identity');
  const nameRow=identity?.querySelector('.profile-name-row');
  const pill=root.querySelector('.online-pill');
  if(identity&&nameRow&&pill) {
    const mode=availabilityMode(stateOf(store).search.filters);
    const text=mode==='NOW'?'Jetzt laut Anzeige':mode==='TODAY'?'Heute laut Anzeige':'Heute verfügbar';
    pill.classList.remove('media-availability');
    pill.classList.add('requested-profile-availability');
    pill.innerHTML=`${icon('calendar')}<span>${e(text)}</span>`;
    nameRow.insertAdjacentElement('afterend',pill);
  }
}

function consolidateDataTab(store) {
  const encounterTab=document.querySelector('#screenHost .profile-tabs button[data-tab="encounters"]');
  if(encounterTab?.classList.contains('active')) {
    const personId=Number(encounterTab.dataset.personId||0);
    if(personId)store.dispatch({type:'PROFILE_TAB',personId,tab:'data'});
    return;
  }
  encounterTab?.remove();
  const dataTab=document.querySelector('#screenHost .profile-tabs button[data-tab="data"]');
  if(!dataTab)return;
  const text=dataTab.querySelector('.profile-tab-label');
  if(text)text.textContent='Daten erfassen';else dataTab.textContent='Daten erfassen';
  if(!dataTab.classList.contains('active'))return;
  const personId=Number(dataTab.dataset.personId||0);
  const detail=personId?S.detailFor(stateOf(store),personId):null;
  const panel=dataTab.closest('.profile-details')?.querySelector('.profile-panel');
  if(!detail||!panel)return;
  const signature=encounterSignature(detail);
  if(panel.dataset.requestedEncounterSignature===signature)return;
  panel.dataset.requestedEncounterSignature=signature;
  panel.classList.add('requested-data-panel');
  panel.innerHTML=dataPanel(detail);
}

export function installRequestedFixes({store,effects}) {
  const gridBusy=new Set();
  const hiddenLikedAdverts=new Set();

  function enhanceDom() {
    addHideLikesOption(store);
    cleanGrid(hiddenLikedAdverts);
    cleanSwipe(store);
    consolidateDataTab(store);
  }

  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-action="grid-like"]');
    if(!button)return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const advertId=String(button.dataset.advertId||'');
    if(!advertId||gridBusy.has(advertId))return;
    gridBusy.add(advertId);
    button.disabled=true;
    const snapshot=stateOf(store);
    const vm=S.searchRows(snapshot).find(row=>row.advertId===advertId)||null;
    const addingLike=vm?.preference!=='LIKE';
    const card=button.closest('.grid-card');
    const parent=card?.parentElement||null;
    const nextSibling=card?.nextSibling||null;
    if(addingLike) {
      hiddenLikedAdverts.add(advertId);
      card?.remove();
    }
    try {
      if(!vm)throw new Error('Profil konnte nicht aufgelöst werden');
      const bundle=await effects.loadBundle(vm,{detail:false,quiet:true});
      const personId=Number(bundle?.personId||vm.personId||0);
      if(!personId)throw new Error('Person konnte nicht aufgelöst werden');
      await effects.mutatePerson(personId,{preference:addingLike?'LIKE':'NONE'});
      effects.loadDashboard();
    } catch(error) {
      if(addingLike) {
        hiddenLikedAdverts.delete(advertId);
        if(card&&parent?.isConnected&&!card.isConnected)parent.insertBefore(card,nextSibling?.parentNode===parent?nextSibling:null);
      }
      effects.toast(error.message||String(error));
    } finally {
      gridBusy.delete(advertId);
      if(button.isConnected)button.disabled=false;
    }
  },true);

  store.subscribe(()=>queueMicrotask(enhanceDom));
  enhanceDom();
}
