import * as S from './app/selectors.js';
import { api } from './api/client.js';
import { availabilityMode } from './search_intelligence.js';
import { escapeHtml as e, escapeAttr as a, flatten, fmtDate, euro } from './shared/html.js';
import { icon } from './shared/icons.js';

const stateOf=store=>store.getState();
const localDateTime=value=>{if(!value)return '';const d=new Date(value);if(Number.isNaN(d.getTime()))return '';const z=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;};
const ratingButtons=(name,value)=>`<div class="rating-row" data-rating-name="${a(name)}">${[1,2,3,4,5].map(n=>`<button type="button" class="${Number(value)===n?'selected':''} ${Number(value)>=n?'active':''}" data-action="rating-select" data-name="${a(name)}" data-value="${n}" aria-label="${n} von 5 Sternen" title="${n} von 5">${icon('star')}</button>`).join('')}</div>`;
const choice=(name,value,options)=>`<div class="choice-field" data-choice-name="${a(name)}">${options.map(opt=>`<button type="button" class="${String(value||'UNKNOWN')===opt?'selected':''}" data-action="choice-select" data-name="${a(name)}" data-value="${a(opt)}">${e(opt==='UNKNOWN'?'Keine Angabe':opt)}</button>`).join('')}</div>`;
const repeatInterestLabel=value=>({YES:'Ja',MAYBE:'Vielleicht',NO:'Nein'}[String(value||'').toUpperCase()]||value||'UNKNOWN');
const normalizeText=value=>String(value||'').trim().toLocaleLowerCase('de-DE').replace(/[\s_-]+/g,' ');
const humanize=value=>{const text=String(value||'').trim().replace(/[_-]+/g,' ');return text?text.charAt(0).toLocaleUpperCase('de-DE')+text.slice(1):'';};
const chipList=values=>`<div class="offer-chips requested-compact-chips">${values.length?values.map(value=>`<span>${e(value)}</span>`).join(''):'<small>Keine Angaben</small>'}</div>`;
const sourcePriceLabel=source=>String(source||'').toUpperCase()==='WHATSAPP_EXTRACTED'?'Aus Nachrichten':'Preisrevision';
export const canonicalPriceRows=proposal=>[
  ...(proposal?.offers||[]).filter(item=>item?.amount_cents!=null).map(item=>({label:item.label||(item.duration_minutes?`${item.duration_minutes} Minuten`:'Preis'),amount_cents:item.amount_cents})),
  ...(proposal?.service_rules||[]).filter(item=>item?.amount_cents!=null).map(item=>({label:[item.raw_label||humanize(item.service_key)||'Service',item.status?humanize(item.status):''].filter(Boolean).join(' · '),amount_cents:item.amount_cents})),
  ...(proposal?.conditions||[]).filter(item=>item?.amount_cents!=null).map(item=>({label:item.label||humanize(item.condition_type)||'Bedingung',amount_cents:item.amount_cents}))
];
const priceRows=proposal=>canonicalPriceRows(proposal).map(item=>`<div class="requested-price-row"><span>${e(item.label)}</span><b>${e(euro(Number(item.amount_cents)/100))}</b></div>`).join('');
export const invalidatesProfilePricing=type=>type==='PRICING_THREAD'||type==='PRICING_PROPOSAL';

export function createRequestedEffects({store,core}) {
  let discoverBusy=false;
  const state=()=>stateOf(store);

  async function discoverDecision(decision) {
    if(discoverBusy)return;
    const row=S.currentSearchRow(state());
    if(!row)return;
    discoverBusy=true;
    try {
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
      const before={preference:bundleVm.preference||row.preference||'NONE',favorite:bundleVm.favorite??row.favorite??false,index:state().discover.index,personId};
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
    } catch(error) { core.toast(error.message||String(error)); }
    finally { discoverBusy=false; }
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
    <label class="field-input"><span>Privatnotiz</span><textarea name="private_note" rows="3">${e(encounter.private_note||'')}</textarea></label>
    <button class="settings-primary" type="submit">${icon('check')}${encounter.encounter_id?'Treffen aktualisieren':'Treffen speichern'}</button>
  </form>`;
}

function encounterSignature(detail) {
  return `${detail.personId}|${(detail.encounters||[]).map(item=>[item.encounter_id,item.updated_at,item.repeat_interest,item.private_note,item.duration_minutes,item.total_price_eur,item.overall_rating,item.sympathy_rating,item.photo_status,item.service_accuracy,item.hygiene,item.kissing,item.negotiable].join(':')).join('|')}`;
}

function dataPanel(detail) {
  const encounters=detail.encounters||[];
  const existing=encounters.map((encounter,index)=>`<details class="price-list-block requested-encounter-entry" ${index===0?'open':''}><summary><b>${e(fmtDate(encounter.occurred_at))}</b> · ${e(encounter.duration_minutes?`${encounter.duration_minutes} Min.`:'Dauer offen')} · ${e(euro(encounter.total_price_eur))}</summary>${encounterForm(detail,encounter)}</details>`).join('');
  const legacySeed=encounters.length?{}:{repeat_interest:detail.repeatInterest,private_note:detail.privateNote};
  return `<section class="profile-card requested-data-card"><div class="profile-card-head"><h2>${icon('calendar')}Daten erfassen</h2><small>${e(encounters.length)} Treffen gespeichert</small></div><p class="muted requested-data-note">Bewertungen und Privatnotizen werden jeweils dem konkreten Treffen zugeordnet.</p>${existing||'<p class="muted">Noch kein Treffen gespeichert.</p>'}<details class="price-list-block requested-new-encounter"><summary><b>${icon('plus')} Neues Treffen erfassen</b></summary>${encounterForm(detail,legacySeed)}</details></section>`;
}

function ensureRequestedStyles() {
  if(document.querySelector('#requestedExplorerProfileStyles'))return;
  const style=document.createElement('style');
  style.id='requestedExplorerProfileStyles';
  style.textContent=`
    .header-actions.discovery-header-actions{display:flex;align-items:center;gap:8px;margin-left:auto}
    .header-discovery-modes{display:flex;align-items:center;gap:2px;padding:3px;border-radius:999px;background:var(--color-surface-soft,#f2f3f7)}
    .header-discovery-modes button{min-width:44px;height:34px;padding:0 11px;border:0;border-radius:999px;background:transparent;color:inherit;font:inherit;font-size:13px;font-weight:650;cursor:pointer}
    .header-discovery-modes button.selected{background:var(--color-surface,#fff);color:var(--color-accent,#e54888);box-shadow:0 1px 4px rgba(25,28,38,.12)}
    .view-switch-glyph{width:21px;height:21px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
    .swipe-media-panel{position:relative}
    .swipe-media-panel .requested-swipe-identity{position:absolute!important;z-index:8!important;left:18px!important;right:auto!important;top:auto!important;bottom:14px!important;width:min(66%,520px)!important;padding:0!important;margin:0!important;color:#fff!important;pointer-events:none;text-shadow:0 1px 8px rgba(0,0,0,.55)}
    .swipe-media-panel .requested-swipe-identity .profile-name-row{display:block!important;margin:0!important}
    .swipe-media-panel .requested-swipe-identity h1{margin:0!important;font-size:clamp(22px,5.4vw,27px)!important;line-height:1.05!important;letter-spacing:-.02em!important}
    .swipe-media-panel .requested-profile-availability{position:absolute!important;z-index:9!important;right:16px!important;left:auto!important;top:auto!important;bottom:16px!important;margin:0!important;width:max-content!important;max-width:34%!important;min-height:32px!important;padding:0 10px!important;display:inline-flex!important;align-items:center!important;gap:5px!important;border-radius:999px!important;background:rgba(255,247,251,.95)!important;color:var(--color-accent,#e54888)!important;border:1px solid rgba(255,255,255,.78)!important;box-shadow:0 4px 18px rgba(0,0,0,.12)!important;font-size:11px!important;white-space:nowrap!important}
    .requested-profile-availability svg{width:13px!important;height:13px!important}
    .swipe-details>.profile-action-rail:first-child{margin-top:0!important}
    .profile-action{position:relative}
    .profile-action.selected{color:var(--color-accent,#e54888)!important;background:color-mix(in srgb,var(--color-accent,#e54888) 11%,transparent)!important}
    .profile-action-badge,.nav-badge{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 5px;border-radius:999px;background:var(--color-accent,#e54888);color:#fff;font-size:10px;font-weight:750;line-height:1;box-sizing:border-box}
    .profile-action-badge{position:absolute;top:2px;right:7px}
    .nav-badge{position:absolute;top:3px;right:calc(50% - 20px);z-index:3}
    .info-grid .info-item{min-height:54px!important;padding:8px 10px!important;gap:8px!important;border-radius:14px!important}
    .info-grid .info-item>span:first-child{width:27px!important;height:27px!important}
    .info-grid .info-item small{font-size:10px!important}
    .info-grid .info-item b{font-size:13px!important}
    .requested-filter-info[data-status="Ja"] b{color:#18824b}.requested-filter-info[data-status="Nein"] b{color:#b94048}.requested-filter-info[data-status="Unbekannt"] b{color:#788194}
    .requested-current-price{border-color:color-mix(in srgb,var(--color-accent,#e54888) 22%,transparent)!important}
    .requested-price-meta{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:-2px 0 8px;color:var(--color-text-muted,#707789);font-size:11px}
    .requested-price-source{display:inline-flex;align-items:center;gap:4px;padding:3px 7px;border-radius:999px;background:color-mix(in srgb,var(--color-accent,#e54888) 10%,transparent);color:var(--color-accent,#e54888);font-weight:650}
    .requested-price-list{display:grid;gap:5px}
    .requested-price-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 10px;border-radius:12px;background:var(--color-surface-soft,#f5f5f8);font-size:13px}
    .requested-price-row b{white-space:nowrap}
    .requested-compact-chips{gap:6px!important}.requested-compact-chips span{min-height:28px!important;padding:4px 9px!important;font-size:11px!important}
    .requested-price-history{display:grid;gap:10px}.requested-price-revision{padding:12px;border:1px solid var(--color-border,#e8e8ee);border-radius:16px;background:var(--color-surface,#fff)}
    .requested-price-revision-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:8px}.requested-price-revision-head small{display:block;color:var(--color-text-muted,#707789);margin-top:2px}
    .requested-data-card>.price-list-block{margin-top:7px!important}.requested-data-note{margin:0 0 8px!important;line-height:1.35!important}
    .requested-encounter-form{padding-top:8px!important;display:grid!important;gap:8px!important}.requested-encounter-form>label{margin:0!important}.requested-encounter-form .field-grid{gap:8px!important}.requested-encounter-form textarea{min-height:66px!important}.requested-encounter-form .choice-field,.requested-encounter-form .rating-row{gap:5px!important}.requested-encounter-form .choice-field button{min-height:32px!important;padding:5px 8px!important;font-size:11px!important}.requested-encounter-form .rating-row button{width:34px!important;height:34px!important}
    @media(max-width:430px){.header-actions.discovery-header-actions{gap:5px}.header-discovery-modes{padding:2px}.header-discovery-modes button{min-width:39px;height:32px;padding:0 8px;font-size:12px}.view-switch{width:36px!important;height:36px!important}.swipe-media-panel .requested-swipe-identity{left:14px!important;bottom:12px!important;width:62%!important}.swipe-media-panel .requested-profile-availability{right:12px!important;bottom:13px!important;max-width:36%!important}}
  `;
  document.head.appendChild(style);
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
  document.querySelectorAll('#screenHost .grid-card[data-advert-id]').forEach(card=>{if(hiddenAdverts.has(String(card.dataset.advertId||'')))card.remove();});
}

function cleanSwipe(store) {
  const root=document.querySelector('#screenHost .swipe-screen');
  if(!root)return;
  root.querySelectorAll('.swipe-details .profile-meta-place,.swipe-details .badge-row').forEach(node=>node.remove());
  const panel=root.querySelector('.swipe-media-panel');
  const identity=root.querySelector('.profile-identity');
  const pill=root.querySelector('.online-pill');
  if(panel&&identity) {
    identity.classList.add('requested-swipe-identity');
    panel.appendChild(identity);
  }
  if(panel&&pill) {
    const mode=availabilityMode(stateOf(store).search.filters);
    const label=mode==='NOW'?'Jetzt laut Anzeige':mode==='TODAY'?'Heute laut Anzeige':'Heute verfügbar';
    pill.classList.remove('media-availability');
    pill.classList.add('requested-profile-availability');
    pill.innerHTML=`${icon('calendar')}<span>${label}</span>`;
    panel.appendChild(pill);
  }
}

function threadUnreadForPerson(state,personId) {
  return (state.messaging.threads||[]).reduce((sum,row)=>Number(row.person_id||0)===Number(personId)?sum+Number(row.unread_count||row.unread||0):sum,0);
}

function enhanceActionStates(store) {
  const state=stateOf(store);
  document.querySelectorAll('#screenHost .profile-action-rail').forEach(rail=>{
    const like=rail.querySelector('[data-action="profile-like"]');
    const favorite=rail.querySelector('[data-action="profile-favorite"]');
    const chat=rail.querySelector('[data-action="profile-chat"]');
    const personId=Number(like?.dataset.personId||favorite?.dataset.personId||chat?.dataset.personId||0);
    const detail=personId?S.detailFor(state,personId):null;
    const liked=detail?detail.preference==='LIKE':like?.classList.contains('selected');
    const favored=detail?Boolean(detail.favorite):favorite?.classList.contains('selected');
    if(like){like.classList.toggle('selected',Boolean(liked));like.setAttribute('aria-pressed',String(Boolean(liked)));like.innerHTML=`${icon(liked?'heart-fill':'heart')}<span>${liked?'Geliket':'Like'}</span>`;}
    if(favorite){favorite.classList.toggle('selected',Boolean(favored));favorite.setAttribute('aria-pressed',String(Boolean(favored)));favorite.innerHTML=`${icon(favored?'star-fill':'star')}<span>Favorit</span>`;}
    if(chat&&personId){const unread=threadUnreadForPerson(state,personId);chat.querySelector('.profile-action-badge')?.remove();if(unread){chat.insertAdjacentHTML('beforeend',`<span class="profile-action-badge" aria-label="${e(unread)} ungelesene Nachrichten">${e(Math.min(99,unread))}</span>`);}}
  });
}

function enhanceBottomUnread(store) {
  const unread=S.unreadTotal(stateOf(store));
  document.querySelectorAll('button[data-action="root-route"][data-route="messages"]').forEach(button=>{
    const badge=button.querySelector('.nav-badge');
    if(!unread){badge?.remove();return;}
    if(badge)badge.textContent=String(Math.min(99,unread));else button.insertAdjacentHTML('afterbegin',`<span class="nav-badge">${e(Math.min(99,unread))}</span>`);
  });
}

function selectedFilterStatuses(detail,filters) {
  const selected=[...new Set(Object.values(filters?.selected||{}).flatMap(value=>Array.isArray(value)?value:[]).map(String).filter(Boolean))];
  const evidence=[...flatten(detail.services),...flatten(detail.traits),...flatten(detail.openingTimes)].map(value=>String(value));
  const normalized=evidence.map(normalizeText);
  return selected.map(raw=>{
    const target=normalizeText(raw);
    const yes=normalized.some(value=>value===target||value.endsWith(`: ${target}`));
    const no=normalized.some(value=>value===`kein ${target}`||value===`ohne ${target}`||value===`${target}: nein`||value===`${target} nein`);
    return {label:humanize(raw),status:yes?'Ja':no?'Nein':'Unbekannt'};
  });
}

function currentPriceCard(current) {
  const rows=priceRows(current?.proposal);
  if(!rows)return '';
  return `<section class="profile-card requested-current-price"><div class="profile-card-head"><h2>${icon('tag')}Letzter Preis</h2></div><div class="requested-price-meta"><span class="requested-price-source">${icon('chat')}${e(sourcePriceLabel(current.source))}</span>${current.observed_at?`<span>${e(fmtDate(current.observed_at))}</span>`:''}</div><div class="requested-price-list">${rows}</div></section>`;
}

function priceHistoryPanel(entry) {
  if(entry?.status==='loading')return `<section class="profile-card"><div class="profile-card-head"><h2>${icon('tag')}Preishistorie</h2></div><div class="loading-card"><span class="spinner"></span><span>Preise werden geladen …</span></div></section>`;
  if(entry?.status==='error')return `<section class="profile-card"><div class="profile-card-head"><h2>${icon('tag')}Preishistorie</h2></div><p class="muted">Preishistorie konnte nicht geladen werden.</p></section>`;
  const items=entry?.history||[];
  return `<section class="profile-card"><div class="profile-card-head"><h2>${icon('tag')}Preishistorie</h2><small>${e(items.length)} Revision${items.length===1?'':'en'}</small></div>${items.length?`<div class="requested-price-history">${items.map((item,index)=>`<article class="requested-price-revision"><div class="requested-price-revision-head"><span><b>${index===0?'Aktueller Preis':'Frühere Revision'}</b><small>${e(item.observed_at?fmtDate(item.observed_at):'Zeitpunkt unbekannt')}</small></span><span class="requested-price-source">${icon('chat')}${e(sourcePriceLabel(item.source))}</span></div><div class="requested-price-list">${priceRows(item.proposal)||'<span class="muted">Keine Preispositionen gespeichert.</span>'}</div></article>`).join('')}</div>`:'<p class="muted">Noch keine aus Nachrichten bestätigten Preise gespeichert.</p>'}</section>`;
}

function compactTraitLabels(detail) {
  return Object.entries(detail.traits||{}).flatMap(([key,value])=>(Array.isArray(value)?value:[value]).filter(Boolean).map(item=>`${key}: ${item}`));
}

function enhanceInfoPanel(panel,detail,filters,current) {
  panel.querySelectorAll('.requested-current-price,.requested-info-traits,.requested-info-opening').forEach(node=>node.remove());
  const currentCard=currentPriceCard(current);
  if(currentCard)panel.insertAdjacentHTML('afterbegin',currentCard);
  const important=panel.querySelector('.info-grid');
  important?.querySelectorAll('.requested-filter-info').forEach(node=>node.remove());
  if(important){
    for(const item of selectedFilterStatuses(detail,filters))important.insertAdjacentHTML('beforeend',`<div class="info-item requested-filter-info" data-status="${a(item.status)}"><span>${icon('filter')}</span><span><small>${e(item.label)}</small><b>${e(item.status)}</b></span></div>`);
  }
  const about=[...panel.querySelectorAll('.profile-card')].find(card=>card.querySelector('h2')?.textContent.trim().includes('Über mich'));
  const traits=`<section class="profile-card requested-info-traits"><div class="profile-card-head"><h2>${icon('user')}Merkmale</h2></div>${chipList(compactTraitLabels(detail))}</section>`;
  const opening=`<section class="profile-card requested-info-opening"><div class="profile-card-head"><h2>${icon('clock')}Öffnungszeiten</h2></div>${chipList(flatten(detail.openingTimes).map(String))}</section>`;
  if(about)about.insertAdjacentHTML('beforebegin',traits+opening);else panel.insertAdjacentHTML('beforeend',traits+opening);
}

function removeCardsByHeading(panel,names) {
  for(const card of panel.querySelectorAll('.profile-card')){const title=card.querySelector('h2')?.textContent.trim()||'';if(names.some(name=>title.includes(name)))card.remove();}
}

function enhanceServicePanel(panel) {
  removeCardsByHeading(panel,['Merkmale','Öffnungszeiten','Preislisten']);
  const heading=[...panel.querySelectorAll('.profile-card h2')].find(node=>node.textContent.trim().includes('Services'));
  if(heading)heading.innerHTML=`${icon('sparkles')}Dienste`;
}

function enhanceAnalysisPanel(panel) {
  removeCardsByHeading(panel,['Aktuelle Filter']);
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
  const pricingCache=new Map();
  let enhanceQueued=false;

  const queueEnhance=()=>{if(enhanceQueued)return;enhanceQueued=true;queueMicrotask(()=>{enhanceQueued=false;enhanceDom();});};
  function loadPricing(personId) {
    const key=String(personId),existing=pricingCache.get(key);
    if(existing)return existing;
    const entry={status:'loading',current:null,history:[]};
    pricingCache.set(key,entry);
    Promise.all([api.currentPricing(personId),api.pricingHistory(personId,100)])
      .then(([current,history])=>pricingCache.set(key,{status:'ready',current:current||null,history:history?.items||[]}))
      .catch(error=>pricingCache.set(key,{status:'error',error,current:null,history:[]}))
      .finally(queueEnhance);
    return entry;
  }

  function enhanceProfilePanels() {
    const state=stateOf(store);
    document.querySelectorAll('#screenHost .profile-details').forEach(details=>{
      const tabs=details.querySelector('.profile-tabs');
      const first=tabs?.querySelector('button[data-person-id]');
      const personId=Number(first?.dataset.personId||0);
      if(!tabs||!personId)return;
      const detail=S.detailFor(state,personId);
      const offer=tabs.querySelector('button[data-tab="offer"]');
      if(offer){const label=offer.querySelector('.profile-tab-label');if(label)label.textContent='Dienste';else offer.textContent='Dienste';}
      let historyTab=tabs.querySelector('button[data-requested-price-history]');
      if(!historyTab){historyTab=document.createElement('button');historyTab.dataset.action='requested-price-history';historyTab.dataset.requestedPriceHistory='1';historyTab.dataset.personId=String(personId);historyTab.textContent='Preishistorie';const history=tabs.querySelector('button[data-tab="history"]');tabs.insertBefore(historyTab,history||null);}
      const active=S.profileTab(state,personId);
      historyTab.classList.toggle('active',active==='price-history');
      if(active==='price-history')tabs.querySelectorAll('button[data-tab]').forEach(button=>button.classList.remove('active'));
      const panel=details.querySelector('.profile-panel');
      if(!panel||!detail)return;
      const pricing=loadPricing(personId);
      if(active==='price-history'){
        panel.dataset.requestedPanel='price-history';
        panel.innerHTML=priceHistoryPanel(pricing);
      }else{
        delete panel.dataset.requestedPanel;
        if(active==='info')enhanceInfoPanel(panel,detail,state.search.filters,pricing.status==='ready'?pricing.current:null);
        else if(active==='offer')enhanceServicePanel(panel);
        else if(active==='analysis')enhanceAnalysisPanel(panel);
      }
    });
  }

  function enhanceDom() {
    ensureRequestedStyles();
    addHideLikesOption(store);
    cleanGrid(hiddenLikedAdverts);
    cleanSwipe(store);
    consolidateDataTab(store);
    enhanceProfilePanels();
    enhanceActionStates(store);
    enhanceBottomUnread(store);
  }

  document.addEventListener('click',async event=>{
    const historyTab=event.target.closest('[data-action="requested-price-history"]');
    if(historyTab){
      event.preventDefault();
      event.stopImmediatePropagation();
      const personId=Number(historyTab.dataset.personId||0);
      if(personId)store.dispatch({type:'PROFILE_TAB',personId,tab:'price-history'});
      queueEnhance();
      return;
    }
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
    if(addingLike) { hiddenLikedAdverts.add(advertId); card?.remove(); }
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

  store.subscribe((_,action)=>{
    if(action?.type==='SEARCH_RESULTS')hiddenLikedAdverts.clear();
    if(invalidatesProfilePricing(action?.type))pricingCache.clear();
    queueEnhance();
  });
  enhanceDom();
}
