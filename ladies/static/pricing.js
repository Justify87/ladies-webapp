'use strict';

import { toast, closeSheet } from './app.js';

(() => {
  const P = {
    threadId:null,
    signal:null,
    timeline:[],
    drafts:new Map(),
    signalTimer:null,
    mutationTimer:null,
    busy:false,
  };
  const q=(s,r=document)=>r.querySelector(s);
  const qa=(s,r=document)=>[...r.querySelectorAll(s)];
  const esc=v=>String(v??'').replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const euro=cents=>cents==null?'—':new Intl.NumberFormat('de-DE',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(Number(cents)/100);
  const icon=name=>`<svg aria-hidden="true"><use href="/static/icons.svg#${name}"/></svg>`;
  const clone=value=>JSON.parse(JSON.stringify(value));
  async function req(url,options={}){const opts={...options,headers:{...(options.headers||{})}};if(opts.body&&typeof opts.body!=='string'){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(opts.body)}const res=await fetch(url,opts);let data={};try{data=await res.json()}catch{}if(!res.ok)throw new Error(data.error||`HTTP ${res.status}`);return data}
  function chatVisible(){const layer=q('#messagingChatLayer');return !!layer&&!layer.classList.contains('hidden')}
  function anchorMessageId(item){if(item?.anchor_message_id)return Number(item.anchor_message_id);const rows=item?.messages||[];const primary=rows.find(x=>x.role==='PRIMARY');if(primary)return Number(primary.message_id);const fallback=[...rows].reverse().find(x=>x.role!=='QUESTION')||rows.at(-1);return fallback?Number(fallback.message_id):null}
  function pendingItem(){return P.timeline.find(x=>x.status==='PENDING')||null}
  function proposalFor(item){if(item.status==='PENDING'){if(!P.drafts.has(item.price_extraction_id))P.drafts.set(item.price_extraction_id,clone(item.proposal));return P.drafts.get(item.price_extraction_id)}return item.proposal||item.effective_proposal||null}
  function sourceCount(item){return (item.messages||[]).length}
  function clearTimelineUi(){qa('[data-price-inline-artifact], [data-price-magic-trigger]').forEach(x=>x.remove())}

  function scheduleRefresh(delay=180){clearTimeout(P.signalTimer);P.signalTimer=setTimeout(refreshPricing,delay)}
  async function refreshPricing(){
    if(!chatVisible()||!P.threadId){clearTimelineUi();return}
    const threadId=P.threadId;
    try{
      const [signal,timeline]=await Promise.all([
        req(`/api/pricing/threads/${threadId}/signal`),
        req(`/api/pricing/threads/${threadId}/timeline?limit=40`),
      ]);
      if(threadId!==P.threadId)return;
      P.signal=signal;
      P.timeline=timeline.items||[];
      const liveIds=new Set(P.timeline.filter(x=>x.status==='PENDING').map(x=>Number(x.price_extraction_id)));
      [...P.drafts.keys()].forEach(id=>{if(!liveIds.has(Number(id)))P.drafts.delete(id)});
      renderInlinePricing();
    }catch{
      if(threadId===P.threadId){P.signal=null;P.timeline=[];clearTimelineUi()}
    }
  }

  function serviceStatusLabel(value){return ({INCLUDED:'inklusive',SURCHARGE:'Aufpreis',ON_REQUEST:'auf Anfrage',NEGOTIABLE:'verhandelbar',EXCLUDED:'nicht angeboten',UNKNOWN:'unklar'})[value]||value||'unklar'}
  function summaryOffers(proposal){const rows=(proposal?.offers||[]).filter(x=>x.amount_cents!=null);if(!rows.length)return'<span class="price-inline-empty">Keine Grundpreise</span>';return rows.map(x=>`<span class="price-summary-chip"><b>${esc(x.label||`${x.duration_minutes??'—'} Min.`)}</b><strong>${euro(x.amount_cents)}</strong></span>`).join('')}
  function summaryExtras(proposal){const services=(proposal?.service_rules||[]).map(x=>`<span>${esc(x.raw_label)} · ${esc(serviceStatusLabel(x.status))}${x.amount_cents!=null?` · ${euro(x.amount_cents)}`:''}</span>`);const conditions=(proposal?.conditions||[]).map(x=>`<span>${esc(x.label)}${x.amount_cents!=null?` · ${euro(x.amount_cents)}`:''}</span>`);return [...services,...conditions].join('')}

  function pendingCard(item){
    const p=proposalFor(item)||{};
    const offers=(p.offers||[]).map((o,i)=>`<div class="price-inline-edit-row" data-price-offer-index="${i}"><label><span>Dauer</span><input data-price-field="duration_minutes" inputmode="numeric" type="number" min="1" max="1440" value="${esc(o.duration_minutes??'')}"><em>Min.</em></label><label><span>Preis</span><input data-price-field="amount_eur" inputmode="decimal" type="number" min="0" step="1" value="${o.amount_cents==null?'':esc(o.amount_cents/100)}"><em>€</em></label><button data-price-remove-offer="${i}" type="button" aria-label="Preis entfernen">×</button></div>`).join('');
    const services=(p.service_rules||[]).map((s,i)=>`<div class="price-inline-service-row" data-price-service-index="${i}"><input data-price-service-field="raw_label" value="${esc(s.raw_label||'')}" aria-label="Service"><select data-price-service-field="status" aria-label="Status">${[['INCLUDED','inklusive'],['SURCHARGE','Aufpreis'],['ON_REQUEST','auf Anfrage'],['NEGOTIABLE','verhandelbar'],['EXCLUDED','nicht angeboten'],['UNKNOWN','unklar']].map(([v,l])=>`<option value="${v}" ${s.status===v?'selected':''}>${l}</option>`).join('')}</select><label><input data-price-service-field="amount_eur" inputmode="decimal" type="number" min="0" step="1" value="${s.amount_cents==null?'':esc(s.amount_cents/100)}"><em>€</em></label><button data-price-remove-service="${i}" type="button" aria-label="Service entfernen">×</button></div>`).join('');
    const conditions=(p.conditions||[]).map(c=>`<div class="price-inline-condition"><span>${esc(c.label)}</span>${c.amount_cents!=null?`<b>${euro(c.amount_cents)}</b>`:''}</div>`).join('');
    const uncertainties=(p.uncertainties||[]).map(x=>`<span class="price-inline-warning">${icon('info')}${esc(x)}</span>`).join('');
    const partial=item.base_price_list_id?`<label class="price-inline-mode"><span>Übernahme</span><select data-price-update-mode><option value="PARTIAL" ${p.update_mode==='PARTIAL'||p.update_mode==='UNKNOWN'?'selected':''}>Teiländerung</option><option value="FULL" ${p.update_mode==='FULL'?'selected':''}>Komplette Liste</option></select></label>`:'';
    return `<div class="price-inline-card price-inline-editor"><div class="price-inline-head"><span class="price-inline-icon">${icon('sparkles')}</span><div><b>Preise erfassen</b><small>Aus ${sourceCount(item)} Chat${sourceCount(item)===1?'nachricht':'nachrichten'} · nur lokal</small></div><span class="price-local-badge">Lokal</span></div>${partial}<section><div class="price-inline-section-head"><b>Preise</b><button data-price-add-offer type="button">+ Eintrag</button></div>${offers||'<p class="price-inline-empty">Keine Dauer-/Preispaare erkannt.</p>'}</section><section><div class="price-inline-section-head"><b>Services</b><button data-price-add-service type="button">+ Service</button></div>${services||'<p class="price-inline-empty">Keine Services erkannt.</p>'}</section>${conditions?`<section><div class="price-inline-section-head"><b>Bedingungen</b></div>${conditions}</section>`:''}${uncertainties?`<div class="price-inline-warnings">${uncertainties}</div>`:''}<p class="price-inline-note">${icon('info')} Automatisch erkannt. Bitte kurz prüfen – erst „Speichern“ erzeugt eine Preisrevision.</p><div class="price-inline-actions"><button data-price-reject type="button">Verwerfen</button><button class="primary" data-price-save type="button">${icon('check')} Speichern</button></div></div>`;
  }

  function summaryCard(item){
    const proposal=proposalFor(item)||{};
    const stale=item.status==='STALE';
    const unchanged=item.status==='ACCEPTED_NO_CHANGE';
    const extras=summaryExtras(proposal);
    const title=stale?'Preisvorschlag veraltet':unchanged?'Preise geprüft':'Preise gespeichert';
    const detail=stale?'Nicht übernommen – inzwischen existiert eine neuere Preisrevision.':unchanged?'Die erkannten Werte waren bereits aktuell.':'Als lokale Preisrevision im Profil gespeichert.';
    return `<div class="price-inline-card price-inline-summary ${stale?'stale':''}"><div class="price-inline-head"><span class="price-inline-icon">${icon(stale?'info':'check')}</span><div><b>${title}</b><small>${detail}</small></div><span class="price-local-badge">Lokal</span></div><div class="price-summary-offers">${summaryOffers(proposal)}</div>${extras?`<div class="price-summary-extras">${extras}</div>`:''}</div>`;
  }

  function renderArtifact(item){const wrap=document.createElement('div');wrap.className='price-inline-item';wrap.dataset.priceInlineArtifact=String(item.price_extraction_id);wrap.dataset.priceAnchorMessage=String(anchorMessageId(item)||'');wrap.innerHTML=item.status==='PENDING'?pendingCard(item):summaryCard(item);return wrap}
  function renderMagicTrigger(){
    const signal=P.signal;
    if(!signal||signal.enabled===false||signal.magic_button_enabled===false||!signal.strong||pendingItem()||P.busy)return;
    const seed=Number(signal.seed_message_id||0);if(!seed)return;
    const line=q(`#chatMessages [data-message-id="${seed}"]`);const bubble=line?.querySelector('.message-bubble');if(!bubble)return;
    const button=document.createElement('button');button.type='button';button.className='price-inline-trigger';button.dataset.priceMagicTrigger=String(seed);button.innerHTML=`${icon('sparkles')}<span>Preise erfassen</span>`;
    const footer=[...bubble.children].find(x=>x.tagName==='SMALL');if(footer)bubble.insertBefore(button,footer);else bubble.appendChild(button);
  }
  function renderInlinePricing(){
    if(!chatVisible()||!P.threadId)return clearTimelineUi();
    clearTimelineUi();
    const cursors=new Map();
    for(const item of P.timeline){
      const anchorId=anchorMessageId(item);if(!anchorId)continue;
      const message=q(`#chatMessages [data-message-id="${anchorId}"]`);if(!message)continue;
      const artifact=renderArtifact(item);const cursor=cursors.get(anchorId)||message;cursor.insertAdjacentElement('afterend',artifact);cursors.set(anchorId,artifact);
    }
    renderMagicTrigger();
  }

  function syncDraft(item){
    const id=Number(item.price_extraction_id),p=proposalFor(item);if(!p)return;
    const card=q(`[data-price-inline-artifact="${id}"]`);if(!card)return;
    p.update_mode=card.querySelector('[data-price-update-mode]')?.value||p.update_mode;
    card.querySelectorAll('[data-price-offer-index]').forEach(row=>{const i=Number(row.dataset.priceOfferIndex),o=p.offers?.[i];if(!o)return;const duration=row.querySelector('[data-price-field="duration_minutes"]')?.value,amount=row.querySelector('[data-price-field="amount_eur"]')?.value;o.duration_minutes=duration?Number(duration):null;o.amount_cents=amount!==''&&amount!=null?Math.round(Number(amount)*100):null;o.label=o.duration_minutes?`${o.duration_minutes} Minuten`:(o.label||'Preis')});
    card.querySelectorAll('[data-price-service-index]').forEach(row=>{const i=Number(row.dataset.priceServiceIndex),s=p.service_rules?.[i];if(!s)return;s.raw_label=row.querySelector('[data-price-service-field="raw_label"]')?.value||s.raw_label;s.status=row.querySelector('[data-price-service-field="status"]')?.value||s.status;const amount=row.querySelector('[data-price-service-field="amount_eur"]')?.value;s.amount_cents=amount!==''&&amount!=null?Math.round(Number(amount)*100):null});
  }
  function findItemFromTarget(target){const root=target.closest('[data-price-inline-artifact]');if(!root)return null;const id=Number(root.dataset.priceInlineArtifact);return P.timeline.find(x=>Number(x.price_extraction_id)===id)||null}
  function scrollToArtifact(item){const id=Number(item?.price_extraction_id||0);const el=q(`[data-price-inline-artifact="${id}"]`);if(el)el.scrollIntoView({behavior:'smooth',block:'center'})}

  async function createProposal(trigger='MANUAL',seedMessageId=null){
    if(!P.threadId||P.busy)return;
    const existing=pendingItem();if(existing){renderInlinePricing();scrollToArtifact(existing);return}
    P.busy=true;renderInlinePricing();
    try{
      toast('Preise werden erkannt …');
      const data=await req(`/api/pricing/threads/${P.threadId}/proposals`,{method:'POST',body:{trigger_type:trigger,seed_message_id:seedMessageId||P.signal?.seed_message_id||null}});
      data.anchor_message_id=anchorMessageId(data)||seedMessageId||P.signal?.seed_message_id||null;
      P.timeline=[...P.timeline.filter(x=>Number(x.price_extraction_id)!==Number(data.price_extraction_id)),data];
      P.drafts.set(Number(data.price_extraction_id),clone(data.proposal));
      renderInlinePricing();requestAnimationFrame(()=>scrollToArtifact(data));
    }catch(e){toast(e.message)}finally{P.busy=false;renderInlinePricing()}
  }
  async function saveProposal(item){
    if(!item||P.busy)return;syncDraft(item);P.busy=true;
    try{
      const result=await req(`/api/pricing/proposals/${item.price_extraction_id}/accept`,{method:'POST',body:{proposal:proposalFor(item)}});
      result.anchor_message_id=anchorMessageId(result)||anchorMessageId(item);
      P.timeline=P.timeline.map(x=>Number(x.price_extraction_id)===Number(item.price_extraction_id)?result:x);P.drafts.delete(Number(item.price_extraction_id));renderInlinePricing();toast(result.status==='ACCEPTED_NO_CHANGE'?'Preise waren bereits aktuell':'Preise gespeichert');
    }catch(e){toast(e.message);await refreshPricing().catch(()=>{})}finally{P.busy=false;scheduleRefresh(120)}
  }
  async function rejectProposal(item){
    if(!item||P.busy)return;P.busy=true;
    try{await req(`/api/pricing/proposals/${item.price_extraction_id}/reject`,{method:'POST'});P.timeline=P.timeline.filter(x=>Number(x.price_extraction_id)!==Number(item.price_extraction_id));P.drafts.delete(Number(item.price_extraction_id));renderInlinePricing();toast('Preisvorschlag verworfen')}catch(e){toast(e.message)}finally{P.busy=false;scheduleRefresh(120)}
  }

  function handleInlineClick(e){
    const trigger=e.target.closest('[data-price-magic-trigger]');if(trigger){e.preventDefault();return createProposal('AUTO',Number(trigger.dataset.priceMagicTrigger))}
    const item=findItemFromTarget(e.target);if(!item||item.status!=='PENDING')return;
    if(e.target.closest('[data-price-add-offer]')){syncDraft(item);proposalFor(item).offers.push({proposal_key:`manual_offer_${Date.now()}`,duration_minutes:null,duration_max_minutes:null,amount_type:'EXACT',amount_cents:null,amount_max_cents:null,label:'Preis',evidence:[]});return renderInlinePricing()}
    if(e.target.closest('[data-price-add-service]')){syncDraft(item);proposalFor(item).service_rules.push({proposal_key:`manual_service_${Date.now()}`,service_key:null,raw_label:'',status:'UNKNOWN',amount_cents:null,note:null,evidence:[]});return renderInlinePricing()}
    const removeOffer=e.target.closest('[data-price-remove-offer]');if(removeOffer){syncDraft(item);proposalFor(item).offers.splice(Number(removeOffer.dataset.priceRemoveOffer),1);return renderInlinePricing()}
    const removeService=e.target.closest('[data-price-remove-service]');if(removeService){syncDraft(item);proposalFor(item).service_rules.splice(Number(removeService.dataset.priceRemoveService),1);return renderInlinePricing()}
    if(e.target.closest('[data-price-reject]'))return rejectProposal(item);
    if(e.target.closest('[data-price-save]'))return saveProposal(item);
  }

  function injectChatMenuAction(){
    if(!chatVisible()||!P.threadId)return;const actions=q('#sheetContent .sheet-actions');if(!actions||q('#priceManualDetect'))return;
    const pending=pendingItem();
    actions.insertAdjacentHTML('afterbegin',`<button class="sheet-action" id="priceManualDetect" type="button">${icon('sparkles')}<span><b>${pending?'Offenen Preisvorschlag anzeigen':'Preise aus Chat erkennen'}</b><small>${pending?'Inline im Chat weiterbearbeiten':'Nachrichten und Preisbilder analysieren'}</small></span>${icon('chevron-right')}</button>`);
    q('#priceManualDetect').onclick=()=>{closeSheet();if(pending){renderInlinePricing();requestAnimationFrame(()=>scrollToArtifact(pending))}else createProposal('MANUAL',P.signal?.seed_message_id||null)};
  }
  async function injectSettings(){const page=q('#settingsPage');if(!page||q('#pricingSettingsCard'))return;let data;try{data=await req('/api/pricing/runtime/status')}catch{return}const s=data.settings||{};page.insertAdjacentHTML('beforeend',`<section id="pricingSettingsCard" class="settings-card pricing-settings-card"><div class="settings-card-head"><span class="settings-icon">${icon('sparkles')}</span><div><b>Preiserkennung</b><small>Lokale Erkennung aus WhatsApp-Chats</small></div></div><label class="pricing-setting-row"><span><b>Preiserkennung</b><small>Regelbasiert, ohne Cloud</small></span><input type="checkbox" data-pricing-setting="price_intelligence_enabled" ${s.price_intelligence_enabled?'checked':''}></label><label class="pricing-setting-row"><span><b>Inline-Hinweis</b><small>Direkt an Nachrichten mit erkannten Preisen</small></span><input type="checkbox" data-pricing-setting="price_magic_button_enabled" ${s.price_magic_button_enabled?'checked':''}></label><label class="pricing-setting-row"><span><b>OCR für Preisbilder</b><small>${data.ocr?.available?'PP-OCRv6 Tiny bereit':'Optionales OCR-Paket nicht installiert'}</small></span><input type="checkbox" data-pricing-setting="price_ocr_enabled" ${s.price_ocr_enabled?'checked':''}></label><div class="pricing-runtime-row"><span>Semantisches Mini-Modell</span><strong>${data.semantic_model?.available?'FunctionGemma 270M bereit':'nicht installiert · optional'}</strong></div></section>`);page.querySelectorAll('[data-pricing-setting]').forEach(input=>input.onchange=async()=>{const body={};body[input.dataset.pricingSetting]=input.checked;try{await req('/api/pricing/settings',{method:'POST',body});if(input.dataset.pricingSetting==='price_magic_button_enabled')scheduleRefresh(50)}catch(e){input.checked=!input.checked;toast(e.message)}})}

  document.addEventListener('ladies:messaging-thread-opened',event=>{const id=Number(event.detail?.threadId||0);if(!id)return;P.threadId=id;P.signal=null;P.timeline=[];P.drafts.clear();scheduleRefresh(60)});
  document.addEventListener('ladies:messaging-thread-updated',event=>{const id=Number(event.detail?.threadId||0);if(id&&id===P.threadId)scheduleRefresh(220)});
  document.addEventListener('ladies:messaging-thread-closed',()=>{P.threadId=null;P.signal=null;P.timeline=[];P.drafts.clear();clearTimeout(P.signalTimer);clearTimelineUi()});
  document.addEventListener('click',e=>{handleInlineClick(e);if(e.target.closest('#chatMore'))setTimeout(injectChatMenuAction,0)},true);
  const observer=new MutationObserver(()=>{if(q('#settingsScreen.active')){clearTimeout(P.mutationTimer);P.mutationTimer=setTimeout(injectSettings,80)}});observer.observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});
  document.addEventListener('DOMContentLoaded',()=>{if(q('#settingsScreen.active'))injectSettings()});
})();
