import { escapeHtml as e, escapeAttr as a } from '../../shared/html.js';
import { icon } from '../../shared/icons.js';
import { availabilityMode } from '../../search_intelligence.js';
import { formatSavedResultDelta } from '../../saved_searches.js';

const RADIUS_OPTIONS=[5,10,20,30,50,70,100];
const IMAGE_MODES=[['ALL','Alle'],['NATURAL','Natürlich'],['STUDIO','Studio'],['FACEAPP','FaceApp'],['AI','KI-optimiert']];

function items(state){return Array.isArray(state.settings?.values?.saved_searches)?state.settings.values.saved_searches:[];}
function meta(item){const f=item?.filters||{},mode=availabilityMode(f);const suffix=mode==='NOW'?' · Jetzt':mode==='TODAY'?' · Heute':'';return `${f.city||'Ort'} · ${Number(f.radius||20)} km${suffix}`;}
function delta(item){return formatSavedResultDelta(item?.last_delta);}
function titleRow(iconName,title,description=''){return `<div class="filter-title-row">${icon(iconName,'filter-title-icon')}<div class="filter-title-copy"><h3>${e(title)}</h3>${description?`<p>${e(description)}</p>`:''}</div></div>`;}
function chip(group,value,label,on){return `<button type="button" class="${on?'selected':''}" data-saved-search-action="edit-filter-choice" data-saved-group-choice data-group="${a(group)}" data-value="${a(value)}" aria-pressed="${on}">${e(label)}</button>`;}
function toggle(key,label,description,on){return `<label class="option-row"><span class="option-copy"><b>${e(label)}</b><small>${e(description)}</small></span><span class="switch"><input type="checkbox" data-saved-local="${a(key)}" ${on?'checked':''}><span></span></span></label>`;}

function renderList(state){
  const saved=items(state);
  const rows=saved.map(item=>`<div class="saved-search-entry ${item.unread_new_results?'has-new-results':''}"><div class="saved-search-copy"><div class="saved-search-name-line"><b>${e(item.name||'Gespeicherte Suche')}</b>${item.unread_new_results?'<span class="saved-search-new-pill">Neu</span>':''}</div><small>${e(meta(item))}</small><span class="saved-search-delta">${e(delta(item))}</span></div><div class="saved-search-actions"><button data-saved-search-action="run" data-saved-id="${a(item.id||'')}" title="Suche aktualisieren">${icon('refresh')}<span>Prüfen</span></button><button data-saved-search-action="edit" data-saved-id="${a(item.id||'')}" title="Gespeicherte Suche bearbeiten">${icon('edit')}<span>Ändern</span></button><button class="saved-search-delete" data-saved-search-action="delete" data-saved-id="${a(item.id||'')}" title="Gespeicherte Suche löschen" aria-label="Gespeicherte Suche löschen">${icon('trash')}</button></div></div>`).join('');
  return `<div class="screen-view saved-search-screen"><div class="screen-scroll" data-scroll-root><section class="filter-card saved-searches-card">${titleRow('star','Gespeicherte Suchen','Werden regelmäßig getrennt von deiner aktuellen Suche geprüft. Neue Treffer beeinflussen Swipe und Entdecken nicht.')}<div class="saved-search-toolbar"><button data-saved-search-action="save-current">${icon('plus')}<span>Aktuelle Filter speichern</span></button></div>${saved.length?`<div class="saved-search-list">${rows}</div>`:'<p class="availability-note">Noch keine gespeicherten Suchen. Speichere deine aktuelle Filterkombination, um sie unabhängig im Hintergrund prüfen zu lassen.</p>'}</section></div></div>`;
}

function renderEditor(state,item){
  const f=item.filters||{},selected=f.selected||{},cfg=state.config||{},groups=cfg.filter_groups||[],categories=cfg.categories||[];
  const mode=availabilityMode(f);
  const groupCards=groups.map(group=>{
    const options=(group.options||[]).map(option=>{const value=String(option.value??option.label??''),label=String(option.label??value),on=(selected[group.key]||[]).map(String).includes(value)||(selected[group.key]||[]).map(String).includes(label);return chip(group.key,value,label,on);}).join('');
    if(!options)return '';
    return `<section class="filter-card saved-search-editor-card">${titleRow('sliders',group.label||group.key)}<div class="choice-row">${options}</div></section>`;
  }).join('');
  const categoryOptions=[`<option value="">Alle Kategorien</option>`,...categories.map(option=>{const value=String(option.value||''),label=String(option.label||value||'Alle');return `<option value="${a(value)}" ${String(f.category||'')===value?'selected':''}>${e(label)}</option>`;})].join('');
  return `<div class="screen-view saved-search-screen"><form class="screen-scroll saved-search-editor" data-scroll-root data-saved-search-form data-saved-id="${a(item.id||'')}">
    <section class="filter-card">${titleRow('edit','Gespeicherte Suche bearbeiten','Name und Filter werden nur für diese gespeicherte Suche geändert.')}<label class="field-input"><span>Name</span><input name="name" value="${a(item.name||'Gespeicherte Suche')}" required></label></section>
    <section class="filter-card">${titleRow('pin','Standort')}<div class="location-fields"><label class="field-input location-main"><span>Ort</span><input name="city" value="${a(f.city||'Leipzig')}"></label><label class="field-input radius-select"><span>Umkreis</span><select name="radius">${RADIUS_OPTIONS.map(value=>`<option value="${value}" ${Number(f.radius||20)===value?'selected':''}>${value} km</option>`).join('')}</select></label></div></section>
    <section class="filter-card">${titleRow('clock','Verfügbarkeit')}<div class="choice-row availability-quick" data-saved-availability-group>${[['ALL','Alle'],['NOW','Jetzt'],['TODAY','Heute']].map(([value,label])=>`<button type="button" class="${mode===value?'selected':''}" data-saved-search-action="edit-availability" data-saved-availability data-mode="${value}">${e(label)}</button>`).join('')}</div></section>
    <section class="filter-card">${titleRow('calendar','Bereiche')}<div class="saved-search-number-grid"><label class="field-input"><span>Alter ab</span><input name="ageMin" type="number" min="18" max="60" value="${a(f.ageMin??18)}"></label><label class="field-input"><span>Alter bis</span><input name="ageMax" type="number" min="18" max="60" value="${a(f.ageMax??60)}"></label><label class="field-input"><span>Größe ab</span><input name="heightMin" type="number" min="140" max="200" value="${a(f.heightMin??140)}"></label><label class="field-input"><span>Größe bis</span><input name="heightMax" type="number" min="140" max="200" value="${a(f.heightMax??200)}"></label></div><label class="field-input saved-search-category"><span>Kategorie</span><select name="category">${categoryOptions}</select></label></section>
    ${groupCards}
    <section class="filter-card">${titleRow('image','Bilddarstellung')}<div class="choice-row image-mode-row" data-saved-image-group>${IMAGE_MODES.map(([value,label])=>`<button type="button" class="${String(f.local?.imageMode||'ALL')===value?'selected':''}" data-saved-search-action="edit-image-mode" data-saved-image-mode data-value="${value}">${e(label)}</button>`).join('')}</div><div class="settings-divider"></div>${toggle('photosOnly','Nur mit Bildern','Profile ohne Bild ausblenden',!!f.local?.photosOnly)}${toggle('videoOnly','Nur mit Video','Nur Profile mit Video anzeigen',!!f.local?.videoOnly)}${toggle('hideBusinessProfiles','Betriebsprofile ausblenden','Betriebsprofile nicht anzeigen',f.local?.hideBusinessProfiles!==false)}${toggle('hideDislikes','Dislikes nicht anzeigen','Bereits abgelehnte Profile ausblenden',f.local?.hideDislikes!==false)}</section>
    <div class="saved-search-editor-actions"><button type="button" class="reset" data-saved-search-action="cancel-edit">Abbrechen</button><button type="submit" class="primary">${icon('check')}<span>Speichern</span></button></div>
  </form></div>`;
}

export function renderSavedSearches(state,route){
  const editId=String(route?.params?.edit||'');
  if(!editId)return renderList(state);
  const item=items(state).find(entry=>String(entry.id)===editId);
  return item?renderEditor(state,item):renderList(state);
}
