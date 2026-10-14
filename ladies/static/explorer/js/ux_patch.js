import { recordFilterUsage } from './data/filter_usage.js';

const OPEN_KEY='ladies.explorer.filter-sections.v1';
function openSections(){try{return new Set(JSON.parse(sessionStorage.getItem(OPEN_KEY)||'[]'));}catch{return new Set();}}
function saveSections(set){try{sessionStorage.setItem(OPEN_KEY,JSON.stringify([...set]));}catch{}}

export function installUxPatch({store}){
  let searchScroll=null;
  let focusRestore=null;
  const rememberSearchPosition=()=>{const scroller=document.querySelector('#screenHost .search-screen [data-scroll-root]');if(scroller)searchScroll=scroller.scrollTop;};
  const rememberFocus=event=>{const input=event.target.closest?.('.search-screen input,.search-screen select,.search-screen textarea');if(!input)return;focusRestore={filterSearch:input.matches('[data-filter-search]'),filterInput:input.dataset.filterInput||null,start:input.selectionStart??null,end:input.selectionEnd??null};};

  document.addEventListener('pointerdown',event=>{if(event.target.closest('.search-screen'))rememberSearchPosition();},true);
  document.addEventListener('input',event=>{if(event.target.closest('.search-screen')){rememberSearchPosition();rememberFocus(event);}},true);
  document.addEventListener('change',event=>{if(event.target.closest('.search-screen')){rememberSearchPosition();rememberFocus(event);}},true);

  document.addEventListener('click',event=>{
    const submit=event.target.closest('[data-action="search-submit"]');
    if(submit)recordFilterUsage(store.getState().search.filters,store.getState().config);
    const remove=event.target.closest('[data-pricing-remove]');
    if(remove){event.preventDefault();event.stopImmediatePropagation();remove.closest('[data-pricing-row]')?.remove();return;}
    const close=event.target.closest('[data-pricing-inline-close]');
    if(close){event.preventDefault();event.stopImmediatePropagation();store.dispatch({type:'PRICING_PROPOSAL',value:null});}
  },true);

  document.addEventListener('toggle',event=>{
    const details=event.target.closest?.('details[data-filter-section]');
    if(!details)return;const set=openSections();details.open?set.add(details.dataset.filterSection):set.delete(details.dataset.filterSection);saveSections(set);
  },true);

  document.addEventListener('keydown',event=>{
    if(event.key!=='Enter'||event.shiftKey||event.isComposing)return;
    const textarea=event.target.closest?.('[data-chat-form] textarea[name="text"]');
    if(!textarea)return;event.preventDefault();textarea.closest('form')?.requestSubmit();
  },true);

  store.subscribe(()=>{
    if(searchScroll==null&&!focusRestore)return;
    const targetScroll=searchScroll,focus=focusRestore;
    queueMicrotask(()=>requestAnimationFrame(()=>{
      const scroller=document.querySelector('#screenHost .search-screen [data-scroll-root]');
      if(scroller&&targetScroll!=null)scroller.scrollTop=targetScroll;
      if(focus){const input=focus.filterSearch?document.querySelector('#screenHost .search-screen [data-filter-search]'):focus.filterInput?document.querySelector(`#screenHost .search-screen [data-filter-input="${CSS.escape(focus.filterInput)}"]`):null;if(input){input.focus({preventScroll:true});if(focus.start!=null&&typeof input.setSelectionRange==='function')try{input.setSelectionRange(focus.start,focus.end??focus.start);}catch{}}}
      if(searchScroll===targetScroll)searchScroll=null;
      if(focusRestore===focus)focusRestore=null;
    }));
  });
}
