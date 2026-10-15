import { recordFilterUsage } from './data/filter_usage.js';

const OPEN_KEY='ladies.explorer.filter-sections.v1';
function openSections(){try{return new Set(JSON.parse(sessionStorage.getItem(OPEN_KEY)||'[]'));}catch{return new Set();}}
function saveSections(set){try{sessionStorage.setItem(OPEN_KEY,JSON.stringify([...set]));}catch{}}
function focusSnapshot(){const input=document.activeElement?.closest?.('.search-screen input,.search-screen select,.search-screen textarea');if(!input)return null;return {filterSearch:input.matches('[data-filter-search]'),filterInput:input.dataset.filterInput||null,start:input.selectionStart??null,end:input.selectionEnd??null};}
function priceNumber(value){const text=String(value??'').trim().replace(',','.');if(!text)return null;const number=Number(text);return Number.isFinite(number)?Math.round(number*100):null;}
const comparable=value=>String(value||'').normalize('NFKC').trim().toLocaleLowerCase('de-DE');

export function canonicalPricingService(rule,config){
  const options=(config?.filter_groups||[]).find(group=>group.key==='service')?.options||[];
  const current=String(rule?.service_key||'').trim();
  if(current){const match=options.find(option=>String(option.value??option.label??'')===current);return match?{value:String(match.value??match.label),label:String(match.label??match.value)}:null;}
  const raw=comparable(rule?.raw_label);
  if(!raw)return null;
  const match=options.find(option=>comparable(option.label)===raw);
  return match?{value:String(match.value??match.label),label:String(match.label??match.value)}:null;
}

export function currentSavedPricingExtractionId(timeline){
  const accepted=(timeline||[]).filter(item=>['ACCEPTED','ACCEPTED_NO_CHANGE'].includes(String(item?.status||'').toUpperCase())&&Number(item?.accepted_price_list_id||0)>0);
  if(!accepted.length)return null;
  const newest=[...accepted].sort((left,right)=>Number(right.price_extraction_id||0)-Number(left.price_extraction_id||0))[0];
  const currentList=Number(newest.accepted_price_list_id||0);
  return accepted.filter(item=>Number(item.accepted_price_list_id||0)===currentList).reduce((latest,item)=>Number(item.price_extraction_id||0)>Number(latest?.price_extraction_id||0)?item:latest,null)?.price_extraction_id||null;
}

export function installUxPatch({store}){
  function syncPricingDraft(form){
    const state=store.getState(),current=state.pricing.proposal?.proposal;
    if(!form||!current)return;
    const proposal=structuredClone(current);
    const pick=(items,row,index)=>items.find(item=>String(item.proposal_key||'')===String(row.dataset.proposalKey||''))||items[index]||{};
    const rows=kind=>[...form.querySelectorAll(`[data-pricing-kind="${kind}"]`)];
    proposal.offers=rows('offer').map((row,index)=>{const base=pick(current.offers||[],row,index),duration=Number(row.querySelector('[data-pricing-field="duration"]')?.value||0)||null;return {...base,label:duration?`${duration} Minuten`:null,duration_minutes:duration,duration_max_minutes:null,amount_cents:priceNumber(row.querySelector('[data-pricing-field="amount"]')?.value??'')};});
    proposal.service_rules=rows('rule').map((row,index)=>{const base=pick(current.service_rules||[],row,index),select=row.querySelector('[data-pricing-field="service"]'),canonical=canonicalPricingService(base,state.config),serviceKey=String(select?.value||canonical?.value||'').trim(),option=serviceKey?[...(select?.options||[])].find(item=>String(item.value)===serviceKey):null,label=String(option?.dataset.label||option?.textContent||canonical?.label||'').trim();return {...base,service_key:serviceKey||null,raw_label:serviceKey?label:'',status:String(row.querySelector('[data-pricing-field="status"]')?.value||base.status||'UNKNOWN'),amount_cents:priceNumber(row.querySelector('[data-pricing-field="amount"]')?.value??'')};});
    proposal.conditions=rows('condition').map((row,index)=>{const base=pick(current.conditions||[],row,index);return {...base,label:String(row.querySelector('[data-pricing-field="label"]')?.value||base.label||'').trim(),amount_cents:priceNumber(row.querySelector('[data-pricing-field="amount"]')?.value??'')};});
    store.dispatch({type:'PRICING_DRAFT',value:proposal},{silent:true});
  }

  function addPricingRow(kind){
    const current=store.getState().pricing.proposal?.proposal;if(!current)return;
    const proposal=structuredClone(current),key=`user_${kind}_${Date.now()}`;
    if(kind==='offer')proposal.offers=[...(proposal.offers||[]),{proposal_key:key,duration_minutes:null,duration_max_minutes:null,amount_type:'EXACT',amount_cents:null,amount_max_cents:null,label:null,evidence:[]}];
    else if(kind==='rule')proposal.service_rules=[...(proposal.service_rules||[]),{proposal_key:key,service_key:null,raw_label:'',status:'INCLUDED',amount_cents:null,note:null,evidence:[]}];
    else if(kind==='condition')proposal.conditions=[...(proposal.conditions||[]),{proposal_key:key,condition_type:'OTHER',label:'',amount_cents:null,note:null,evidence:[]}];
    store.dispatch({type:'PRICING_DRAFT',value:proposal});
  }

  function enhancePricingUi(){
    const state=store.getState(),active=state.pricing.proposal,form=document.querySelector('[data-pricing-form]');
    if(form&&active?.proposal){
      const rules=active.proposal.service_rules||[];
      [...form.querySelectorAll('[data-pricing-kind="rule"]')].forEach((row,index)=>{const select=row.querySelector('[data-pricing-field="service"]');if(!select||select.value)return;const base=rules.find(item=>String(item.proposal_key||'')===String(row.dataset.proposalKey||''))||rules[index];const canonical=canonicalPricingService(base,state.config);if(canonical&&[...select.options].some(option=>String(option.value)===canonical.value))select.value=canonical.value;});
      if(String(active.trigger_type||'').toUpperCase()==='REVISION'){
        const reject=form.querySelector('[data-action="pricing-reject"]');
        if(reject){reject.dataset.pricingRevisionDiscard='1';reject.textContent='Änderungen verwerfen';}
      }
    }
    const threadId=Number(state.messaging.currentThread?.thread_id||state.messaging.currentThread?.threadId||0),timeline=state.pricing.byThread?.[String(threadId)]?.timeline||[],currentId=Number(currentSavedPricingExtractionId(timeline)||0);
    for(const button of document.querySelectorAll('[data-action="pricing-revise"]')){
      if(Number(button.dataset.extractionId||0)===currentId)continue;
      const badge=document.createElement('span');badge.className='pricing-saved-badge';badge.textContent='Historisch';button.replaceWith(badge);
    }
  }

  document.addEventListener('input',event=>{const form=event.target.closest?.('[data-pricing-form]');if(form)syncPricingDraft(form);},true);
  document.addEventListener('change',event=>{const form=event.target.closest?.('[data-pricing-form]');if(form)syncPricingDraft(form);},true);
  document.addEventListener('submit',event=>{const form=event.target.closest?.('[data-pricing-form]');if(form)syncPricingDraft(form);},true);

  document.addEventListener('click',event=>{
    const submit=event.target.closest('[data-action="search-submit"]');
    if(submit)recordFilterUsage(store.getState().search.filters,store.getState().config);
    const add=event.target.closest('[data-pricing-add]');
    if(add){event.preventDefault();event.stopImmediatePropagation();syncPricingDraft(add.closest('[data-pricing-form]'));addPricingRow(add.dataset.pricingAdd);return;}
    const remove=event.target.closest('[data-pricing-remove]');
    if(remove){event.preventDefault();event.stopImmediatePropagation();const form=remove.closest('[data-pricing-form]');remove.closest('[data-pricing-row]')?.remove();syncPricingDraft(form);return;}
    const close=event.target.closest('[data-pricing-inline-close]');
    if(close){event.preventDefault();event.stopImmediatePropagation();store.dispatch({type:'PRICING_PROPOSAL',value:null});}
  },true);

  document.addEventListener('toggle',event=>{const details=event.target.closest?.('details[data-filter-section]');if(!details)return;const set=openSections();details.open?set.add(details.dataset.filterSection):set.delete(details.dataset.filterSection);saveSections(set);},true);

  document.addEventListener('keydown',event=>{
    if(event.key!=='Enter'||event.shiftKey||event.isComposing)return;
    const textarea=event.target.closest?.('[data-chat-form] textarea[name="text"]');
    if(!textarea)return;event.preventDefault();textarea.closest('form')?.requestSubmit();
  },true);

  store.subscribe(()=>{
    const focus=focusSnapshot();
    queueMicrotask(()=>requestAnimationFrame(()=>{
      enhancePricingUi();
      if(!focus)return;
      const input=focus.filterSearch?document.querySelector('#screenHost .search-screen [data-filter-search]'):focus.filterInput?document.querySelector(`#screenHost .search-screen [data-filter-input="${CSS.escape(focus.filterInput)}"]`):null;
      if(input){input.focus({preventScroll:true});if(focus.start!=null&&typeof input.setSelectionRange==='function')try{input.setSelectionRange(focus.start,focus.end??focus.start);}catch{}}
    }));
  });
  queueMicrotask(()=>requestAnimationFrame(enhancePricingUi));
}
