import { escapeHtml as e, escapeAttr as a, relativeAge } from '../../shared/html.js';
import { icon } from '../../shared/icons.js';
import * as S from '../../app/selectors.js';

const CHANGE_WINDOW_MS=36*60*60*1000;
const TABS=Object.freeze([
  ['FAVORITE','Favoriten'],
  ['LIKE','Likes'],
  ['DISLIKE','Dislikes'],
]);

const timestamp=value=>{const time=value?new Date(value).getTime():NaN;return Number.isFinite(time)?time:0;};
const changeFor=row=>row.recent_change||row.recentChange||{};
const isRecentChange=row=>{const created=timestamp(changeFor(row).created_at);return created>0&&Date.now()-created<CHANGE_WINDOW_MS;};
const activityTime=(row,preferChange=false)=>relativeAge(preferChange?changeFor(row).created_at:(row.updated_at||row.last_seen_at||row.person_last_seen_at||changeFor(row).created_at));

function changeBadges(row){
  const change=changeFor(row),type=String(change.event_type||'').toUpperCase(),fresh=isRecentChange(row),badges=[];
  const listingCount=Math.max(1,Number(row.listingCount||row.listing_count||1));
  if(listingCount>1)badges.push(`+${listingCount-1}`);
  if(fresh&&type==='LISTING_FIRST_SNAPSHOT'&&listingCount===1)badges.push('Neu');
  if(fresh&&type==='NEW_LISTING_FOR_PERSON')badges.push('Neue Anzeige');
  return badges.slice(0,2);
}

function statusMeta(row){
  const status=String(row.status||'UNKNOWN').toUpperCase();
  if(status==='ACTIVE')return {tone:'active',label:'Aktiv'};
  if(['UNAVAILABLE','INACTIVE','REMOVED'].includes(status))return {tone:'inactive',label:'Nicht verfügbar'};
  const seen=relativeAge(row.last_seen_at||row.person_last_seen_at);
  return {tone:'unknown',label:seen?`Zuletzt ${seen}`:'Status unbekannt'};
}

function countLabel(tab,count){
  if(tab==='FAVORITE')return count===1?'1 Favorit':`${count} Favoriten`;
  if(tab==='LIKE')return count===1?'1 Like':`${count} Likes`;
  return count===1?'1 Dislike':`${count} Dislikes`;
}

function rowMarkup(row,{preferChangeTime=false}={}){
  const status=statusMeta(row),badges=changeBadges(row),time=activityTime(row,preferChangeTime);
  return `<article class="saved-person-row">
    <button class="saved-person-open" data-action="open-profile" data-person-id="${a(row.personId)}" data-advert-id="${a(row.advertId)}" aria-label="Profil von ${a(row.name)} öffnen">
      <span class="saved-person-photo">${row.thumbnail?`<img src="${a(row.thumbnail)}" alt="" loading="lazy">`:`<span class="saved-person-photo-fallback">${icon('user')}</span>`}</span>
      <span class="saved-person-copy">
        <span class="saved-person-title"><b>${e(row.name)}</b>${row.age?`<small>${e(row.age)}</small>`:''}</span>
        <span class="saved-person-location">${icon('pin')}<span>${e(row.city||'Ort unbekannt')}</span></span>
        <span class="saved-person-meta">
          <span class="saved-status saved-status-${a(status.tone)}"><i></i>${e(status.label)}</span>
          ${badges.map(label=>`<span class="saved-change-badge">${e(label)}</span>`).join('')}
        </span>
      </span>
    </button>
    <div class="saved-person-side">
      <small class="saved-change-time">${e(time)}</small>
      <div class="saved-person-actions">
        <button class="saved-heart ${row.favorite?'selected':''}" data-action="list-favorite" data-person-id="${a(row.personId)}" aria-label="${row.favorite?'Aus Favoriten entfernen':'Zu Favoriten hinzufügen'}" title="Favorit umschalten">${icon('heart')}</button>
        <details class="saved-person-menu" name="saved-row-actions">
          <summary aria-label="Weitere Aktionen" title="Weitere Aktionen">${icon('more')}</summary>
          <div class="saved-person-menu-popover">
            <button data-action="status-single" data-advert-id="${a(row.advertId)}">${icon('refresh')}<span>Status prüfen</span></button>
          </div>
        </details>
      </div>
    </div>
  </article>`;
}

function recentSection(rows){
  if(!rows.length)return '';
  const visible=rows.slice(0,3),hidden=rows.slice(3);
  return `<section class="saved-section saved-recent-section">
    <h2>Neu &amp; geändert</h2>
    <div class="saved-row-list">${visible.map(row=>rowMarkup(row,{preferChangeTime:true})).join('')}</div>
    ${hidden.length?`<details class="saved-recent-more"><summary><span class="saved-show-all">Alle anzeigen</span><span class="saved-show-less">Weniger anzeigen</span> ›</summary><div class="saved-row-list saved-row-list-extra">${hidden.map(row=>rowMarkup(row,{preferChangeTime:true})).join('')}</div></details>`:''}
  </section>`;
}

function regularSection(rows,{heading=''}={}){
  if(!rows.length)return '';
  return `<section class="saved-section">${heading?`<h2>${e(heading)}</h2>`:''}<div class="saved-row-list">${rows.map(row=>rowMarkup(row)).join('')}</div></section>`;
}

export function renderFavorites(state){
  const rows=S.listRows(state),statusCheck=state.lists.statusCheck;
  const isWatchlist=state.lists.tab==='FAVORITE'&&state.lists.sort==='DATE';
  const recentRows=isWatchlist?rows.filter(isRecentChange):[];
  const recentIds=new Set(recentRows.map(row=>String(row.personId)));
  const remainingRows=isWatchlist?rows.filter(row=>!recentIds.has(String(row.personId))):rows;
  const emptyIcon=state.lists.tab==='DISLIKE'?'thumb-down':state.lists.tab==='LIKE'?'thumb-up':'heart';
  const progress=statusCheck.total?Math.min(100,Math.round(statusCheck.done/statusCheck.total*100)):0;
  const failed=Number(statusCheck.failed||0),succeeded=Number(statusCheck.succeeded||0);
  const progressMeta=`${statusCheck.done} von ${statusCheck.total} · ✓ ${succeeded}${failed?` · ${failed} Fehler`:''} · ${progress} %`;
  const busy=Boolean(state.lists.loading||statusCheck.running);

  return `<div class="screen-view saved-screen"><div class="screen-scroll" data-scroll-root>
    <div class="saved-segmented" role="tablist" aria-label="Gespeicherte Personen">
      ${TABS.map(([id,label])=>`<button role="tab" aria-selected="${state.lists.tab===id?'true':'false'}" class="${state.lists.tab===id?'active':''}" data-action="list-tab" data-tab="${id}" ${state.lists.loading?'disabled':''}>${e(label)}</button>`).join('')}
    </div>

    <div class="saved-toolbar">
      <b>${e(countLabel(state.lists.tab,rows.length))}</b>
      <div class="saved-toolbar-actions">
        <label class="saved-sort-control" title="Sortieren"><span class="saved-visually-hidden">Sortieren</span><select data-list-sort aria-label="Sortierung" ${state.lists.loading?'disabled':''}><option value="DATE" ${state.lists.sort==='DATE'?'selected':''}>Datum</option><option value="NAME" ${state.lists.sort==='NAME'?'selected':''}>Name</option></select><span class="saved-sort-chevron" aria-hidden="true">⌄</span></label>
        <button class="saved-refresh ${statusCheck.running?'running':''}" data-action="status-check-all" aria-label="Status aller Anzeigen prüfen" title="Status aller Anzeigen prüfen" ${busy?'disabled':''}>${icon('refresh')}</button>
      </div>
    </div>

    ${statusCheck.running?`<div class="saved-status-progress" role="status" aria-live="polite"><div class="saved-status-progress-copy"><b>Status wird aktualisiert</b><span>${e(progressMeta)}</span></div><progress class="saved-progress" max="${e(statusCheck.total||1)}" value="${e(statusCheck.done)}">${e(progress)} %</progress></div>`:''}
    ${state.lists.loading&&!rows.length?`<div class="saved-loading">${icon('refresh')}<span>Liste wird geladen …</span></div>`:''}
    ${rows.length?`<div class="saved-content">${recentSection(recentRows)}${regularSection(remainingRows,{heading:recentRows.length?'Weitere Favoriten':''})}</div>`:!state.lists.loading?`<div class="saved-empty">${icon(emptyIcon)}<b>Noch nichts hier</b><span>Gespeicherte Profile erscheinen automatisch in dieser Ansicht.</span></div>`:''}
  </div></div>`;
}
