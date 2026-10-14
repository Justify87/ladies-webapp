import { escapeHtml as e, escapeAttr as a } from '../../shared/html.js';
import { icon } from '../../shared/icons.js';

const GROUP_LABELS={figur:'Figur',typ:'Typ',geschlecht:'Geschlecht',busen:'Busen',intimbereich:'Intimbereich',hautton:'Hautton',haarfarbe:'Haarfarbe',haarlaenge:'Haarlänge',augenfarbe:'Augenfarbe',koerperschmuck:'Körperschmuck',sonstiges:'Sonstiges',service:'Services',treff:'Treffpunkt',location:'Location',zeiten:'Verfügbarkeit'};
const PROPERTY_GROUPS=new Set(['figur','typ','geschlecht','busen','intimbereich','hautton','haarfarbe','haarlaenge','augenfarbe','koerperschmuck','sonstiges']);

function filterCard(iconName,title,description,body,extraClass=''){
  return `<section class="filter-card ${extraClass}"><div class="filter-title-row">${icon(iconName,'filter-title-icon')}<div class="filter-title-copy"><h3>${e(title)}</h3>${description?`<p>${e(description)}</p>`:''}</div></div>${body}</section>`;
}
function chipGroup(group,options,selected){
  const set=new Set(selected || []);
  return `<div class="choice-row">${options.map(opt=>{const value=String(opt.value ?? opt.label ?? '');const label=String(opt.label ?? value);const on=set.has(value)||set.has(label);return `<button class="${on?'selected':''}" data-action="filter-choice" data-group="${a(group)}" data-value="${a(value)}" aria-pressed="${on}">${e(label)}</button>`;}).join('')}</div>`;
}
function subgroup(group,selected){
  if(!group?.options?.length)return '';
  return `<div class="filter-subgroup"><small class="filter-subgroup-label">${e(GROUP_LABELS[group.key]||group.label||group.key)}</small>${chipGroup(group.key,group.options,selected[group.key])}</div>`;
}
function toggleRow(key,label,description,on){
  return `<label class="option-row"><span class="option-copy"><b>${e(label)}</b><small>${e(description)}</small></span><span class="switch"><input type="checkbox" data-local-filter="${a(key)}" ${on?'checked':''}><span></span></span></label>`;
}

export function renderSearch(state){
  const f=state.search.filters,cfg=state.config || {},groups=cfg.filter_groups || [],categories=cfg.categories || [];
  const preview=state.search.preview;
  const meetup=groups.find(group=>group.key==='treff');
  const services=groups.find(group=>group.key==='service');
  const properties=groups.filter(group=>PROPERTY_GROUPS.has(group.key));
  const extras=groups.filter(group=>['location','zeiten'].includes(group.key));
  const searchLabel=preview.status==='ready'?`Suche starten · ${e(preview.count)} Treffer`:preview.status==='loading'?'Treffer werden geprüft …':preview.status==='error'?'Suche starten':'Suche starten';
  return `<div class="screen-view search-screen"><div class="screen-scroll" data-scroll-root>
    <div class="search-heading"><h1>Suche &amp; Filter</h1><p>Finde genau die Ladies, die zu deinen Wünschen passen.</p></div>
    ${filterCard('calendar','Altersbereich','Wähle den gewünschten Altersbereich',`<div class="range-pair"><label><span>Von</span><input data-filter-input="ageMin" type="number" min="18" max="60" value="${a(f.ageMin)}"><b>Jahre</b></label><label><span>Bis</span><input data-filter-input="ageMax" type="number" min="18" max="60" value="${a(f.ageMax)}"><b>Jahre</b></label></div>`)}
    ${filterCard('pin','Standort','Suche in deiner Nähe',`<div class="location-fields"><label class="field-input location-main"><span>Ort</span><input data-filter-input="city" value="${a(f.city)}"></label><label class="field-input"><span>Umkreis</span><input data-filter-input="radius" type="number" min="1" max="200" value="${a(f.radius)}"><b>km</b></label></div>`)}
    ${meetup?filterCard('home','Treffpunkt','Wo möchtest du dich treffen?',chipGroup(meetup.key,meetup.options||[],f.selected[meetup.key])):''}
    ${filterCard('heart','Kategorien','Wähle eine oder mehrere Kategorien',`<div class="choice-row category-row">${categories.map(opt=>{const value=String(opt.value||'');const on=String(f.category||'')===value;return `<button class="${on?'selected':''}" data-action="filter-category" data-value="${a(value)}" aria-pressed="${on}">${icon('heart')}${e(opt.label||value||'Alle')}</button>`;}).join('')}</div>`)}
    ${filterCard('star','Eigenschaften','Wähle, was dir wichtig ist',`<div class="range-pair compact-range"><label><span>Größe ab</span><input data-filter-input="heightMin" type="number" min="140" max="200" value="${a(f.heightMin)}"><b>cm</b></label><label><span>Größe bis</span><input data-filter-input="heightMax" type="number" min="140" max="200" value="${a(f.heightMax)}"><b>cm</b></label></div>${properties.map(group=>subgroup(group,f.selected)).join('')}`)}
    ${services?filterCard('sparkles','Services','Wähle gewünschte Services',chipGroup(services.key,services.options||[],f.selected[services.key])):''}
    ${filterCard('sliders','Weitere Optionen','Zusätzliche Filter für genauere Ergebnisse',`${extras.map(group=>subgroup(group,f.selected)).join('')}<div class="settings-divider"></div>${toggleRow('photosOnly','Nur mit Bildern','Profile ohne Bild ausblenden',!!f.local?.photosOnly)}${toggleRow('videoOnly','Nur mit Video','Nur Profile mit Video anzeigen',!!f.local?.videoOnly)}${toggleRow('likesOnly','Nur Likes','Nur bereits gelikte Profile anzeigen',!!f.local?.likesOnly)}`)}
  </div><div class="search-footer"><button class="reset" data-action="reset-filters">${icon('refresh')}<span>Zurücksetzen</span></button><button class="primary ${preview.status==='loading'?'loading':''}" data-action="search-submit" ${state.search.loading?'disabled':''}>${state.search.loading?'<span class="spinner"></span>':icon('search')}<span>${searchLabel}</span></button></div></div>`;
}
