import * as S from './app/selectors.js';
import { availabilityMode } from './search_intelligence.js';
import { escapeHtml as e, escapeAttr as a, fmtDate, euro } from './shared/html.js';
import { icon } from './shared/icons.js';

const gridDismissed=new Set();
const REPEAT_VALUES=Object.freeze({UNKNOWN:'UNKNOWN',YES:'Ja',MAYBE:'Vielleicht',NO:'Nein'});
const localDateTime=value=>{if(!value)return '';const d=new Date(value);if(Number.isNaN(d.getTime()))return '';const z=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;};
const repeatValue=value=>REPEAT_VALUES[String(value||'UNKNOWN').toUpperCase()]||String(value||'UNKNOWN');
const ratingButtons=(name,value)=>{const selected=Number(value||0);return `<div class="rating-row" data-rating-name="${a(name)}">${[1,2,3,4,5].map(n=>`<button type="button" class="${selected===n?'selected ':''}${selected>=n?'active':''}" data-action="rating-select" data-name="${a(name)}" data-value="${n}" aria-label="${n} von 5 Sternen">${icon('star')}</button>`).join('')}</div>`;};
const choice=(name,value,options)=>`<div class="choice-field" data-choice-name="${a(name)}">${options.map(opt=>{const selected=String(value||'UNKNOWN')===opt;return `<button type="button" class="${selected?'selected':''}" data-action="choice-select" data-name="${a(name)}" data-value="${a(opt)}">${e(opt==='UNKNOWN'?'Keine Angabe':opt)}</button>`;}).join('')}</div>`;

function encounterForm(detail,encounter={}) {
  const encounters=detail.encounters||[];
  const isExisting=Boolean(encounter.encounter_id);
  const useLegacy=!isExisting&&!encounters.length;
  const listingId=encounter.listing_id||detail.listings?.[0]?.listing_id||'';
  const repeat=repeatValue(encounter.repeat_interest||(useLegacy?detail.repeatInterest:'UNKNOWN'));
  const privateNote=encounter.private_note??(useLegacy?detail.privateNote:'');
  return `<form class="my-data-form requested-encounter-form" data-encounter-services-form data-full-encounter="true" data-person-id="${a(detail.personId)}" data-encounter-id="${a(encounter.encounter_id||'')}">
    <input type="hidden" name="listing_id" value="${a(listingId)}">
    <label class="field-input"><span>Zeitpunkt</span><input name="occurred_at" type="datetime-local" value="${a(localDateTime(encounter.occurred_at))}"></label>
    <label><span>Gesamteindruck</span>${ratingButtons('overall_rating',encounter.overall_rating)}</label>
    <label><span>Sympathie</span>${ratingButtons('sympathy_rating',encounter.sympathy_rating)}</label>
    <label><span>Fotos</span>${choice('photo_status',encounter.photo_status,['UNKNOWN','Wie auf Anzeige','Besser','Schlechter'])}</label>
    <label><span>Wiederholungsinteresse</span>${choice('repeat_interest',repeat,['UNKNOWN','Ja','Vielleicht','Nein'])}</label>
    <label><span>Service korrekt</span>${choice('service_accuracy',encounter.service_accuracy,['UNKNOWN','Ja','Teilweise','Nein'])}</label>
    <label><span>Hygiene</span>${choice('hygiene',encounter.hygiene,['UNKNOWN','Sehr gut','Okay','Schlecht'])}</label>
    <label><span>Küssen</span>${choice('kissing',encounter.kissing,['UNKNOWN','Ja','Auf Wunsch','Nein'])}</label>
    <label><span>Verhandelbar</span>${choice('negotiable',encounter.negotiable,['UNKNOWN','Ja','Teilweise','Nein'])}</label>
    <div class="field-grid"><label class="field-input"><span>Dauer Minuten</span><input name="duration_minutes" type="number" min="0" value="${a(encounter.duration_minutes??'')}"></label><label class="field-input"><span>Preis €</span><input name="total_price_eur" type="number" min="0" step="1" value="${a(encounter.total_price_eur??'')}"></label></div>
    <label class="field-input"><span>Private Notiz</span><textarea name="private_note" rows="4">${e(privateNote||'')}</textarea></label>
    <button class="settings-primary" type="submit">${icon('check')}${isExisting?'Treffen aktualisieren':'Treffen speichern'}</button>
  </form>`;
}

function dataCapture(detail) {
  const encounters=detail.encounters||[];
  const existing=encounters.map((encounter,index)=>`<details class="price-list-block requested-encounter-block" ${index===0?'open':''}><summary><b>${e(fmtDate(encounter.occurred_at))}</b> · ${e(encounter.duration_minutes?`${encounter.duration_minutes} Min.`:'Dauer offen')} · ${e(euro(encounter.total_price_eur))}</summary>${encounterForm(detail,encounter)}</details>`).join('');
  return `<section class="profile-card requested-data-capture" data-requested-data-capture><div class="profile-card-head"><h2>${icon('note')}Daten erfassen</h2><small>${e(encounters.length)} Treffen gespeichert</small></div><p class="requested-data-intro">Bewertungen, Wiederholungsinteresse und private Notizen werden jeweils dem konkreten Treffen zugeordnet.</p><details class="price-list-block requested-new-encounter" ${encounters.length?'':'open'}><summary><b>${icon('plus')} Neues Treffen erfassen</b></summary>${encounterForm(detail,{})}</details>${existing||'<p class="muted">Noch kein Treffen gespeichert.</p>'}</section>`;
}

function enhanceSearchToggle(store) {
  const state=store.getState();
  let input=document.querySelector('#screenHost input[data-local-filter="hideLikes"]');
  if(!input){
    const dislike=document.querySelector('#screenHost input[data-local-filter="hideDislikes"]');
    const row=dislike?.closest('.option-row');
    if(row){
      row.insertAdjacentHTML('afterend',`<label class="option-row requested-hide-likes"><span class="option-copy"><b>Bereits getätigte Likes nicht anzeigen</b><small>Bereits gelikte Profile aus Swipe- und Rasteransicht entfernen</small></span><span class="switch"><input type="checkbox" data-local-filter="hideLikes"><span></span></span></label>`);
      input=document.querySelector('#screenHost input[data-local-filter="hideLikes"]');
    }
  }
  if(input)input.checked=Boolean(state.search.filters.local?.hideLikes);
}

function enhanceGrid(store) {
  const rows=S.searchRows(store.getState());
  document.querySelectorAll('#screenHost .grid-card').forEach(card=>{
    const advertId=String(card.dataset.advertId||'');
    if(gridDismissed.has(advertId)){card.remove();return;}
    card.querySelector('.grid-card-chips')?.remove();
    card.querySelector('.grid-card-like')?.remove();
    const heart=card.querySelector('.grid-card-heart');
    if(!heart)return;
    const row=rows.find(item=>String(item.advertId||'')===advertId);
    heart.dataset.action='requested-grid-like';
    heart.setAttribute('aria-label','Like');
    heart.title='Like';
    heart.classList.toggle('selected',row?.preference==='LIKE');
  });
}

function enhanceSwipe(store) {
  const identity=document.querySelector('#screenHost .swipe-details .profile-identity');
  if(!identity)return;
  identity.querySelector('.profile-meta-place')?.remove();
  const badgeRow=identity.querySelector('.badge-row');
  badgeRow?.querySelectorAll('.badge').forEach(badge=>{if(!badge.querySelector('svg'))badge.remove();});
  if(badgeRow&&!badgeRow.querySelector('.badge'))badgeRow.remove();
  const pill=document.querySelector('#screenHost .swipe-screen .online-pill, #screenHost .swipe-media-panel .media-availability');
  if(pill){
    let row=identity.querySelector('.requested-availability-row');
    if(!row){row=document.createElement('div');row.className='requested-availability-row';identity.querySelector('.profile-name-row')?.insertAdjacentElement('afterend',row);}
    const mode=availabilityMode(store.getState().search.filters);
    const label=mode==='NOW'?'Jetzt laut Anzeige':mode==='TODAY'?'Heute laut Anzeige':'Heute verfügbar';
    pill.classList.remove('media-availability');
    pill.innerHTML=`${icon('calendar')}<span>${label}</span>`;
    row.appendChild(pill);
  }
}

function enhanceEndState(store) {
  const heading=document.querySelector('#screenHost .swipe-shell .empty-state h2');
  if(!heading)return;
  heading.textContent=(store.getState().search.results||[]).length?'Du hast alle Profile angeschaut.':'Keine Profile gefunden.';
}

function enhanceRatings(root=document) {
  root.querySelectorAll('.requested-data-capture .rating-row').forEach(row=>{
    const buttons=[...row.querySelectorAll('button[data-value]')];
    const selected=Number(buttons.find(button=>button.classList.contains('selected'))?.dataset.value||0);
    buttons.forEach(button=>{
      const value=Number(button.dataset.value||0);
      button.classList.toggle('active',selected>0&&value<=selected);
      button.innerHTML=icon('star');
    });
  });
}

function enhanceDataCapture(store) {
  const state=store.getState();
  const route=S.currentRoute(state);
  if(!['profile','swipe'].includes(route.name))return;
  const personId=Number(document.querySelector('#screenHost [data-profile-media]')?.dataset.personId||0);
  if(!personId)return;
  const detail=S.detailFor(state,personId);
  if(!detail)return;
  const tabs=document.querySelector('#screenHost .profile-tabs');
  const dataTab=tabs?.querySelector('button[data-tab="data"]');
  const encounterTab=tabs?.querySelector('button[data-tab="encounters"]');
  const label=dataTab?.querySelector('.profile-tab-label');
  if(label)label.textContent='Daten erfassen';else if(dataTab)dataTab.textContent='Daten erfassen';
  dataTab?.setAttribute('aria-label','Daten erfassen');
  encounterTab?.remove();
  const active=S.profileTab(state,personId);
  if(active==='encounters'){
    queueMicrotask(()=>store.dispatch({type:'PROFILE_TAB',personId,tab:'data'}));
    return;
  }
  if(active!=='data')return;
  const panel=document.querySelector('#screenHost .profile-details .profile-panel.active');
  if(!panel)return;
  const signature=JSON.stringify({encounters:detail.encounters,repeatInterest:detail.repeatInterest,privateNote:detail.privateNote});
  if(panel.dataset.requestedDataSignature!==signature||!panel.querySelector('[data-requested-data-capture]')){
    panel.innerHTML=dataCapture(detail);
    panel.dataset.requestedDataSignature=signature;
  }
  enhanceRatings(panel);
}

export function installRequestedExplorerFixes({store,effects}) {
  let wasSearchLoading=Boolean(store.getState().search.loading);
  const enhance=()=>{
    enhanceSearchToggle(store);
    enhanceGrid(store);
    enhanceSwipe(store);
    enhanceEndState(store);
    enhanceDataCapture(store);
  };

  document.addEventListener('click',async event=>{
    const heart=event.target.closest('[data-action="requested-grid-like"]');
    if(heart){
      event.preventDefault();
      const card=heart.closest('.grid-card');
      const advertId=String(card?.dataset.advertId||heart.dataset.advertId||'');
      if(!card||!advertId)return;
      const rows=S.searchRows(store.getState());
      const row=rows.find(item=>String(item.advertId||'')===advertId)||{advertId,personId:Number(card.dataset.personId||0)||null};
      const marker=document.createComment(`liked:${advertId}`);
      card.parentNode?.insertBefore(marker,card);
      gridDismissed.add(advertId);
      card.remove();
      try {
        const vm=await effects.loadBundle(row,{detail:false,quiet:true});
        const personId=Number(vm?.personId||row.personId||0)||null;
        if(!personId)throw new Error('Person konnte nicht aufgelöst werden');
        await effects.mutatePerson(personId,{preference:'LIKE'});
        effects.loadDashboard();
        marker.remove();
      } catch(error) {
        gridDismissed.delete(advertId);
        marker.replaceWith(card);
        effects.toast(error.message||String(error));
        enhanceGrid(store);
      }
      return;
    }
    if(event.target.closest('.requested-data-capture [data-action="rating-select"]'))queueMicrotask(()=>enhanceRatings());
  });

  store.subscribe(()=>{
    const loading=Boolean(store.getState().search.loading);
    if(wasSearchLoading&&!loading)gridDismissed.clear();
    wasSearchLoading=loading;
    queueMicrotask(enhance);
  });
  enhance();
}
