import { recordFilterUsage } from './data/filter_usage.js';

const OPEN_KEY='ladies.explorer.filter-sections.v1';
function openSections(){try{return new Set(JSON.parse(sessionStorage.getItem(OPEN_KEY)||'[]'));}catch{return new Set();}}
function saveSections(set){try{sessionStorage.setItem(OPEN_KEY,JSON.stringify([...set]));}catch{}}
function focusSnapshot(){const input=document.activeElement?.closest?.('.search-screen input,.search-screen select,.search-screen textarea');if(!input)return null;return {filterSearch:input.matches('[data-filter-search]'),filterInput:input.dataset.filterInput||null,start:input.selectionStart??null,end:input.selectionEnd??null};}
function priceNumber(value){return value===''?null:Math.round(Number(value)*100);}

export function installUxPatch({store}){
  function syncPricingDraft(form){
    const current=store.getState().pricing.proposal?.proposal;
    if(!form||!current)return;
    const proposal=structuredClone(current);
    const pick=(items,row,index)=>items.find(item=>String(item.proposal_key||'')===String(row.dataset.proposalKey||''))||items[index]||{};
    const rows=kind=>[...form.querySelectorAll(`[data-pricing-kind="${kind}"]`)];
    proposal.offers=rows('offer').map((row,index)=>{const base=pick(current.offers||[],row,index);return {...base,label:String(row.querySelector('[data-pricing-field="label"]')?.value||'').trim()||null,duration_minutes:row.querySelector('[data-pricing-field="duration"]')?.value===''?null:Number(row.querySelector('[data-pricing-field="duration"]')?.value),amount_cents:priceNumber(row.querySelector('[data-pricing-field="amount"]')?.value??'')};});
    proposal.service_rules=rows('rule').map((row,index)=>{const base=pick(current.service_rules||[],row,index);return {...base,raw_label:String(row.querySelector('[data-pricing-field="label"]')?.value||base.raw_label||'').trim(),status:String(row.querySelector('[data-pricing-field="status"]')?.value||base.status||'UNKNOWN'),amount_cents:priceNumber(row.querySelector('[data-pricing-field="amount"]')?.value??'')};});
    proposal.conditions=rows('condition').map((row,index)=>{const base=pick(current.conditions||[],row,index);return {...base,label:String(row.querySelector('[data-pricing-field="label"]')?.value||base.label||'').trim(),amount_cents:priceNumber(row.querySelector('[data-pricing-field="amount"]')?.value??'')};});
    store.dispatch({type:'PRICING_DRAFT',value:proposal},{silent:true});
  }

  document.addEventListener('input',event=>{const form=event.target.closest?.('[data-pricing-form]');if(form)syncPricingDraft(form);},true);
  document.addEventListener('change',event=>{const form=event.target.closest?.('[data-pricing-form]');if(form)syncPricingDraft(form);},true);

  document.addEventListener('click',event=>{
    const submit=event.target.closest('[data-action="search-submit"]');
    if(submit)recordFilterUsage(store.getState().search.filters,store.getState().config);
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
    const current=document.querySelector('#screenHost .search-screen [data-scroll-root]');
    if(!current)return;
    const targetScroll=current.scrollTop,focus=focusSnapshot();
    queueMicrotask(()=>requestAnimationFrame(()=>{
      const scroller=document.querySelector('#screenHost .search-screen [data-scroll-root]');
      if(!scroller)return;
      scroller.scrollTop=targetScroll;
      if(!focus)return;
      const input=focus.filterSearch?document.querySelector('#screenHost .search-screen [data-filter-search]'):focus.filterInput?document.querySelector(`#screenHost .search-screen [data-filter-input="${CSS.escape(focus.filterInput)}"]`):null;
      if(input){input.focus({preventScroll:true});if(focus.start!=null&&typeof input.setSelectionRange==='function')try{input.setSelectionRange(focus.start,focus.end??focus.start);}catch{}}
    }));
  });
}
