import { escapeHtml as e, escapeAttr as a, flatten, firstValue, fmtDate, euro, clamp } from '../shared/html.js';
import { icon } from '../shared/icons.js';
import { profileFilterEvidence } from '../data/adapters.js';

const infoItem=(ico,label,value)=>`<div class="info-item"><span>${icon(ico)}</span><span><small>${e(label)}</small><b>${e(value ?? '—')}</b></span></div>`;
const chips = values => `<div class="offer-chips">${values.map(v=>`<span>${e(v)}</span>`).join('') || '<small>Keine Angaben</small>'}</div>`;
const traitValue=(detail,...keys)=>firstValue(detail.traits,keys) || '—';
const serviceFlag=(detail,...aliases)=>{const names=flatten(detail.services).map(x=>String(x).toLowerCase());return names.some(n=>aliases.some(x=>n===x.toLowerCase()||n.includes(x.toLowerCase())))?'Ja':'—';};
const recentish=value=>{const time=value?new Date(value).getTime():NaN;return Number.isFinite(time)&&Date.now()-time<36*60*60*1000;};
const localDateTime=value=>{if(!value)return '';const d=new Date(value);if(Number.isNaN(d.getTime()))return '';const z=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`;};

export function renderMedia(vm,index=0,{standalone=false,autoplay=false}={}) {
  const media=vm.media || []; const i=clamp(index,0,Math.max(0,media.length-1)); const current=media[i];
  const body=current ? (current.kind==='VIDEO' ? `<video src="${a(current.src)}" ${current.poster?`poster="${a(current.poster)}"`:''} ${autoplay?'muted autoplay loop':'controls'} playsinline preload="metadata"></video>` : `<img src="${a(current.src)}" alt="Profilbild von ${a(vm.name)}" draggable="false">`) : `<div class="media-fallback">${icon('image')}<span>Kein Bild verfügbar</span></div>`;
  return `<div class="profile-media ${standalone?'standalone-media':''}" data-profile-media data-person-id="${a(vm.personId)}">${body}
    ${media.length>1?`<div class="media-story">${media.slice(0,10).map((_,n)=>`<i class="${n===i?'active':''}"></i>`).join('')}</div>`:''}
    <span class="media-count">${media.length?`${i+1} / ${media.length}`:'—'}</span>
    ${media.length>1?`<button class="media-tap prev" data-action="profile-media-step" data-delta="-1" data-person-id="${a(vm.personId)}" aria-label="Vorheriges Bild"></button><button class="media-tap next" data-action="profile-media-step" data-delta="1" data-person-id="${a(vm.personId)}" aria-label="Nächstes Bild"></button>`:''}
  </div>`;
}

export function renderActionRail(vm) {
  return `<div class="profile-action-rail">
    <button class="profile-action ${vm.preference==='LIKE'?'selected':''}" data-action="profile-like" data-person-id="${a(vm.personId)}" aria-pressed="${vm.preference==='LIKE'}">${icon('heart')}<span>Like</span></button>
    <button class="profile-action ${vm.favorite?'selected':''}" data-action="profile-favorite" data-person-id="${a(vm.personId)}" aria-pressed="${vm.favorite}">${icon('star')}<span>Favorit</span></button>
    <button class="profile-action" data-action="profile-chat" data-person-id="${a(vm.personId)}">${icon('chat')}<span>Chat</span></button>
    <button class="profile-action" data-action="profile-contact" data-person-id="${a(vm.personId)}">${icon('phone')}<span>Kontakt</span></button>
    <button class="profile-action" data-action="profile-share" data-person-id="${a(vm.personId)}" data-source-url="${a(vm.sourceUrl||'')}">${icon('external')}<span>Teilen</span></button>
  </div>`;
}

function renderInfo(detail) {
  const important=[
    ['calendar','Alter',detail.age||'—'],['heart','Busen',traitValue(detail,'Busen','Oberweite','Brustgröße','Brustgroesse','Brust')],
    ['heart','GV',serviceFlag(detail,'GV','Geschlechtsverkehr')],['heart','AV',serviceFlag(detail,'AV','Anal')],
    ['message','Küssen',serviceFlag(detail,'ZK','Küssen','Kuss')],['star','Girlfriend',serviceFlag(detail,'Girlfriend-Sex','Girlfriend Sex','GFS')]
  ];
  return `<section class="profile-card"><div class="profile-card-head"><h2>${icon('info')}Wichtige Informationen</h2></div><div class="info-grid">${important.map(x=>infoItem(...x)).join('')}</div></section>
  <section class="profile-card"><div class="profile-card-head"><h2>${icon('note')}Über mich</h2></div><p class="about-copy">${e(detail.description || 'Keine Beschreibung gespeichert.')}</p></section>
  ${detail.listings.length>1?`<section class="identity-card"><span class="id-icon">${icon('shield')}</span><span><b>Wiedererkannt · ${detail.listings.length} Anzeigen</b><small>${Math.max(0,detail.listings.length-1)} weitere Anzeige(n) dieser Person verknüpft</small></span></section>`:''}`;
}

function renderOffer(detail) {
  const prices=detail.prices || [];
  return `<section class="profile-card"><div class="profile-card-head"><h2>${icon('sparkles')}Services</h2></div>${chips(flatten(detail.services))}</section>
  <section class="profile-card"><div class="profile-card-head"><h2>${icon('user')}Merkmale</h2></div>${chips(Object.entries(detail.traits||{}).flatMap(([k,v])=>(Array.isArray(v)?v:[v]).filter(Boolean).map(x=>`${k}: ${x}`)))}</section>
  <section class="profile-card"><div class="profile-card-head"><h2>${icon('clock')}Öffnungszeiten</h2></div>${chips(flatten(detail.openingTimes))}</section>
  <section class="profile-card"><div class="profile-card-head"><h2>${icon('tag')}Preislisten</h2><button data-action="open-add-price" data-person-id="${a(detail.personId)}">${icon('plus')}Hinzufügen</button></div>
    ${prices.length?prices.map(list=>`<div class="price-list-block"><small>${e(fmtDate(list.observed_at))} · ${e(list.source||'')}</small>${(list.items||[]).map(item=>`<div class="price-row"><span>${e(item.label || (item.duration_minutes?`${item.duration_minutes} Minuten`:'Preis'))}</span><b>${e(euro(item.amount_eur))}</b></div>`).join('')}</div>`).join(''):'<p class="muted">Noch keine Preisliste gespeichert.</p>'}
  </section>`;
}

function renderAnalysis(detail,filters) {
  const analytics=detail.analytics || {}; const media=analytics.media || {}; const affinity=analytics.affinity || {}; const consistency=analytics.consistency || {};
  const evidence=profileFilterEvidence(detail,filters);
  return `<section class="profile-card analysis-card"><div class="profile-card-head"><h2>${icon('sparkles')}Analyse</h2></div>
    <div class="analysis-stats"><div><b>${media.analyzed_images ?? 0}</b><small>Bilder geprüft</small></div><div><b>${media.flagged_images ?? 0}</b><small>Hinweise</small></div><div><b>${detail.listings.length}</b><small>Anzeigen</small></div><div><b>${affinity.score ?? '—'}</b><small>Affinität</small></div></div>
    ${affinity.score==null?`<p class="analysis-caveat">${e(affinity.note || 'Noch nicht genug Vergleichsdaten für eine Affinitätsbewertung.')}</p>`:`<p class="analysis-caveat">Affinität basiert auf deinen bisherigen Likes/Dislikes und vergleichbaren Merkmalen, nicht auf einer objektiven Bewertung.</p>`}
  </section>
  <section class="profile-card"><div class="profile-card-head"><h2>${icon('filter')}Aktuelle Filter</h2></div><div class="offer-chips">${evidence.filter(x=>x.matchesFilter).map(x=>`<span class="matched">${icon('check')}${e(x.label)}</span>`).join('') || '<small>Keine direkten Merkmalsübereinstimmungen ermittelt.</small>'}</div></section>
  <section class="profile-card"><div class="profile-card-head"><h2>${icon('refresh')}Konsistenz</h2></div><div class="analysis-stats"><div><b>${consistency.city_count ?? 0}</b><small>Orte</small></div><div><b>${consistency.name_count ?? 0}</b><small>Namen</small></div><div><b>${media.shared_with_other_people ?? 0}</b><small>Geteilte Medien</small></div><div><b>${media.reused_across_listings ?? 0}</b><small>Wiederverwendet</small></div></div></section>`;
}

const ratingButtons=(name,value)=>`<div class="rating-row" data-rating-name="${a(name)}">${[1,2,3,4,5].map(n=>`<button type="button" class="${Number(value)===n?'selected':''}" data-action="rating-select" data-name="${a(name)}" data-value="${n}">${n}</button>`).join('')}</div>`;
const choice=(name,value,options)=>`<div class="choice-field" data-choice-name="${a(name)}">${options.map(opt=>`<button type="button" class="${String(value||'UNKNOWN')===opt?'selected':''}" data-action="choice-select" data-name="${a(name)}" data-value="${a(opt)}">${e(opt)}</button>`).join('')}</div>`;
const encounterServiceRow=item=>`<div class="encounter-service-row" data-encounter-service-row><input name="service_name" placeholder="Service" value="${a(item?.service_name||'')}"><input name="amount_eur" type="number" min="0" step="1" placeholder="€" value="${a(item?.amount_eur ?? '')}"><label class="check-row"><input name="performed" type="checkbox" ${item?.performed===false?'':'checked'}><span>Erbracht</span></label><input name="service_note" placeholder="Notiz" value="${a(item?.note||'')}"></div>`;

function renderMyData(detail) {
  const encounter=detail.encounters[0] || {};
  return `<form class="profile-card my-data-form" data-profile-data-form data-person-id="${a(detail.personId)}" data-encounter-id="${a(encounter.encounter_id || '')}">
    <div class="profile-card-head"><h2>${icon('note')}Meine Daten</h2></div>
    <label><span>Gesamteindruck</span>${ratingButtons('overall_rating',encounter.overall_rating)}</label>
    <label><span>Sympathie</span>${ratingButtons('sympathy_rating',encounter.sympathy_rating)}</label>
    <label><span>Fotos</span>${choice('photo_status',encounter.photo_status,['UNKNOWN','Wie auf Anzeige','Besser','Schlechter'])}</label>
    <label><span>Wiederholungsinteresse</span>${choice('repeat_interest',detail.repeatInterest,['UNKNOWN','Ja','Vielleicht','Nein'])}</label>
    <label><span>Service korrekt</span>${choice('service_accuracy',encounter.service_accuracy,['UNKNOWN','Ja','Teilweise','Nein'])}</label>
    <label><span>Hygiene</span>${choice('hygiene',encounter.hygiene,['UNKNOWN','Sehr gut','Okay','Schlecht'])}</label>
    <label><span>Küssen</span>${choice('kissing',encounter.kissing,['UNKNOWN','Ja','Auf Wunsch','Nein'])}</label>
    <label><span>Verhandelbar</span>${choice('negotiable',encounter.negotiable,['UNKNOWN','Ja','Teilweise','Nein'])}</label>
    <div class="field-grid"><label class="field-input"><span>Dauer Minuten</span><input name="duration_minutes" type="number" min="0" value="${a(encounter.duration_minutes ?? '')}"></label><label class="field-input"><span>Gesamtpreis €</span><input name="total_price_eur" type="number" min="0" step="1" value="${a(encounter.total_price_eur ?? '')}"></label></div>
    <label class="field-input"><span>Private Notiz</span><textarea name="private_note" rows="4">${e(detail.privateNote)}</textarea></label>
    <button class="settings-primary" type="submit">${icon('check')}Speichern</button>
  </form>`;
}

function renderEncounterForm(detail,encounter={}) {
  const services=[...(encounter.services||[]),{},{}];
  const listingId=encounter.listing_id || detail.listings?.[0]?.listing_id || '';
  return `<form class="my-data-form" data-encounter-services-form data-full-encounter="true" data-person-id="${a(detail.personId)}" data-encounter-id="${a(encounter.encounter_id||'')}">
    <input type="hidden" name="listing_id" value="${a(listingId)}">
    <div class="field-grid"><label class="field-input"><span>Zeitpunkt</span><input name="occurred_at" type="datetime-local" value="${a(localDateTime(encounter.occurred_at))}"></label><label class="field-input"><span>Dauer Minuten</span><input name="duration_minutes" type="number" min="0" value="${a(encounter.duration_minutes ?? '')}"></label></div>
    <div class="field-grid"><label class="field-input"><span>Basispreis €</span><input name="base_price_eur" type="number" min="0" step="1" value="${a(encounter.base_price_eur ?? '')}"></label><label class="field-input"><span>Gesamtpreis €</span><input name="total_price_eur" type="number" min="0" step="1" value="${a(encounter.total_price_eur ?? '')}"></label></div>
    <label><span>Gesamteindruck</span>${ratingButtons('overall_rating',encounter.overall_rating)}</label>
    <label><span>Sympathie</span>${ratingButtons('sympathy_rating',encounter.sympathy_rating)}</label>
    <label><span>Fotos</span>${choice('photo_status',encounter.photo_status,['UNKNOWN','Wie auf Anzeige','Besser','Schlechter'])}</label>
    <label><span>Service korrekt</span>${choice('service_accuracy',encounter.service_accuracy,['UNKNOWN','Ja','Teilweise','Nein'])}</label>
    <label><span>Hygiene</span>${choice('hygiene',encounter.hygiene,['UNKNOWN','Sehr gut','Okay','Schlecht'])}</label>
    <label><span>Küssen</span>${choice('kissing',encounter.kissing,['UNKNOWN','Ja','Auf Wunsch','Nein'])}</label>
    <label><span>Verhandelbar</span>${choice('negotiable',encounter.negotiable,['UNKNOWN','Ja','Teilweise','Nein'])}</label>
    <div class="encounter-services-editor"><b>Services</b>${services.map(encounterServiceRow).join('')}</div>
    <label class="field-input"><span>Notiz</span><textarea name="note" rows="3">${e(encounter.note||'')}</textarea></label>
    <button class="settings-primary" type="submit">${icon('check')}${encounter.encounter_id?'Treffen aktualisieren':'Treffen speichern'}</button>
  </form>`;
}

function renderEncounters(detail) {
  const encounters=detail.encounters || [];
  return `<section class="profile-card"><div class="profile-card-head"><h2>${icon('calendar')}Treffen</h2><small>${e(encounters.length)} gespeichert</small></div>
    ${encounters.length?encounters.map((encounter,index)=>`<details class="price-list-block" ${index===0?'open':''}><summary><b>${e(fmtDate(encounter.occurred_at))}</b> · ${e(encounter.duration_minutes?`${encounter.duration_minutes} Min.`:'Dauer offen')} · ${e(euro(encounter.total_price_eur))}</summary>${renderEncounterForm(detail,encounter)}</details>`).join(''):'<p class="muted">Noch kein Treffen gespeichert.</p>'}
    <details class="price-list-block"><summary><b>${icon('plus')} Neues Treffen erfassen</b></summary>${renderEncounterForm(detail,{})}</details>
  </section>`;
}

function eventTitle(event){const type=String(event.event_type || event.field_key || '').toUpperCase();if(type==='LISTING_FIRST_SNAPSHOT')return 'Anzeige entdeckt';if(type==='NEW_LISTING_FOR_PERSON')return 'Person wiedererkannt';if(type.includes('MEDIA'))return 'Bilder geändert';if(type==='STATUS_CHANGED')return 'Status geändert';return event.field_key?`${event.field_key} geändert`:'Profil geändert';}
function renderHistory(detail,{showDescriptionChanges=false}={}) {
  const changes=showDescriptionChanges?(detail.changes||[]):(detail.changes||[]).filter(ev=>String(ev.field_key||'').toLowerCase()!=='description');
  return `<section class="profile-card"><div class="profile-card-head"><h2>${icon('clock')}Profilverlauf</h2></div><div class="timeline">${changes.length?changes.map(ev=>`<div class="timeline-row"><i></i><span><b>${e(eventTitle(ev))}</b><small>${e(fmtDate(ev.created_at))}</small></span>${ev.from_snapshot_id&&ev.to_snapshot_id?`<button data-action="snapshot-compare" data-a="${a(ev.from_snapshot_id)}" data-b="${a(ev.to_snapshot_id)}">Vergleichen</button>`:''}</div>`).join(''):'<p class="muted">Noch keine Änderungen gespeichert.</p>'}</div></section>
  <section class="profile-card"><div class="profile-card-head"><h2>${icon('list')}Anzeigen</h2></div>${detail.listings.map(row=>`<div class="listing-row"><span><b>#${e(row.advert_id || row.external_listing_id || row.listing_id)}</b><small>${e(row.current_status || '')} · ${e(fmtDate(row.last_seen_at))}</small></span>${row.current_url?`<a href="${a(row.current_url)}" target="_blank" rel="noopener">${icon('external')}</a>`:''}</div>`).join('')}</section>`;
}

export function renderTabs(detail,active,filters,{showDescriptionChanges=false}={}) {
  const tabs=[['info','Info'],['offer','Angebot'],['analysis','Analyse'],['data','Meine Daten'],['encounters','Treffen'],['history','Verlauf']];
  const panels={info:renderInfo(detail),offer:renderOffer(detail),analysis:renderAnalysis(detail,filters),data:renderMyData(detail),encounters:renderEncounters(detail),history:renderHistory(detail,{showDescriptionChanges})};
  return `<div class="profile-details"><nav class="profile-tabs">${tabs.map(([id,label])=>`<button class="${active===id?'active':''}" data-action="profile-tab" data-tab="${id}" data-person-id="${a(detail.personId)}">${e(label)}</button>`).join('')}</nav><div class="profile-panel active">${panels[active] || panels.info}</div></div>`;
}

export function renderIdentity(vm) {
  const listing=vm.listing || vm.listings?.[0] || {};const status=String(listing.current_status||vm.status||'UNKNOWN').toUpperCase();const listingNew=recentish(listing.published_at||listing.first_seen_at||vm.listingPublishedAt||vm.listingFirstSeenAt);const personNew=Number(vm.listingCount||1)<=1&&recentish(vm.personFirstSeen);const statusText=status==='ACTIVE'?'Anzeige aktiv':status==='UNAVAILABLE'?'Anzeige nicht verfügbar':'Status unbekannt';
  return `<div class="profile-identity"><div class="profile-name-row"><h1>${e(vm.name)}${vm.age?`, ${e(String(vm.age).replace(/\s*Jahre?.*/i,''))}`:''}</h1>${vm.activeToday?'<span class="online-pill">Heute online</span>':''}</div><div class="profile-meta-place">${icon('pin')}${e(vm.city || 'Ort unbekannt')}</div><div class="badge-row">${vm.listingCount>1?`<span class="badge purple">${icon('users')}Person bekannt · ${vm.listingCount} Anzeigen</span>`:personNew?`<span class="badge blue">${icon('user')}Person neu erkannt</span>`:''}${listingNew?`<span class="badge blue">${icon('sparkles')}Anzeige neu</span>`:''}<span class="badge">${icon('shield')}${e(statusText)}</span>${vm.hasVideo?`<span class="badge blue">${icon('play')}Video</span>`:''}</div></div>`;
}

export function renderStandalone(detail,{activeTab='info',mediaIndex=0,filters={selected:{}},autoplay=false,showDescriptionChanges=false}={}) {
  return `<div class="screen-view standalone-profile"><div class="screen-scroll" data-scroll-root><section class="standalone-media-wrap"><button class="profile-back-button" data-action="back" aria-label="Zurück">${icon('back')}</button><button class="profile-more-button" data-action="profile-more" data-person-id="${a(detail.personId)}">${icon('more')}</button>${renderMedia(detail,mediaIndex,{standalone:true,autoplay})}</section><section class="standalone-profile-body">${renderIdentity(detail)}${renderActionRail(detail)}${renderTabs(detail,activeTab,filters,{showDescriptionChanges})}</section></div></div>`;
}

export function renderSwipe(bundle,detail,{activeTab='info',mediaIndex=0,filters={selected:{}},autoplay=false,showDescriptionChanges=false}={}) {
  const vm=detail || bundle;
  return `<div class="screen-view swipe-screen"><div class="screen-scroll" data-scroll-root data-swipe-scroll><section class="swipe-media-panel">${renderMedia(vm,mediaIndex,{autoplay})}</section><section class="swipe-details">${renderIdentity(vm)}${renderActionRail(vm)}${detail?renderTabs(detail,activeTab,filters,{showDescriptionChanges}):`<section class="profile-card loading-card"><span class="spinner"></span><span>Profildetails werden geladen …</span></section>`}</section></div></div>`;
}
